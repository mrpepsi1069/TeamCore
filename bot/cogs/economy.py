"""cogs/economy.py — Cash: /balance, /pay and the casino.

The balance itself always lives in MongoDB (database/economy.py); this cog only validates
input and builds the messages, so a stale number read a moment ago can never be spent twice.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from database import economy as cash
from shared.economy import CASINO_GAME_TIMEOUT, CASINO_GAMES, MAX_CASH, format_cash, play
from utils.embeds import error_embed, info_embed, success_embed
from utils.permissions import has_owner_perms
from utils.ui import ack

GAME_CHOICES = [
    app_commands.Choice(name=f"{spec['label']} — {spec['odds']}", value=key)
    for key, spec in CASINO_GAMES.items()
]


def _closed_view() -> discord.ui.View:
    """The game message after it ended: same button, no longer clickable."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Play", emoji="🎲", style=discord.ButtonStyle.primary, disabled=True))
    return view


def _result_embed(game: dict, won: bool, reward: int, balance: int) -> discord.Embed:
    """The settled game: what happened, and the member's Cash after it."""
    spec = CASINO_GAMES[game["game"]]
    title = (f"{spec['emoji']} You won {format_cash(reward)}!" if won
             else f"{spec['emoji']} You lost {format_cash(game['entry'])}")
    body = f"{spec['label']} · {game.get('detail') or ''}\n**Final balance: {format_cash(balance)}**"
    return (success_embed if won else error_embed)(title, body)


class CasinoView(discord.ui.View):
    def __init__(self, cog: "Economy", game: dict):
        super().__init__(timeout=CASINO_GAME_TIMEOUT)
        self.cog = cog
        self.game = game
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != str(self.game["user_id"]):
            await interaction.response.send_message(
                f"❌ This game belongs to <@{self.game['user_id']}> — run `/casino` to start your own.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Play", emoji="🎲", style=discord.ButtonStyle.primary)
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.cog.settle(interaction, self.game, self.message)

    async def on_timeout(self):
        await self.cog.expire(self.game, self.message)


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.expire_due.start()

    def cog_unload(self):
        self.expire_due.cancel()

    @app_commands.command(name="balance", description="Check how much Cash you (or another member) have")
    @app_commands.describe(member="Whose balance to show (default: yours)")
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        # Acknowledge before touching MongoDB: Discord invalidates an interaction that has not
        # been answered within 3 seconds, and a cold database round-trip can take longer.
        if not await ack(interaction):
            return
        try:
            held = await cash.balance(db.handle(), interaction.guild_id, target.id)
        except cash.EconomyError as e:
            return await interaction.followup.send(embed=error_embed("Cash Unavailable", str(e)), ephemeral=True)
        embed = info_embed("💵 Cash", f"{target.mention} has **{format_cash(held)}**")
        await interaction.edit_original_response(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="pay", description="Send Cash to another member")
    @app_commands.describe(member="Who receives the Cash", amount="How much Cash to send")
    async def pay(self, interaction: discord.Interaction, member: discord.Member,
                  amount: app_commands.Range[int, 1, MAX_CASH]):
        # The sender is always the person running the command — never a value the client sends.
        if member.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=error_embed("Can't Pay Yourself", "Send Cash to someone else."), ephemeral=True)
        if member.bot:
            return await interaction.response.send_message(
                embed=error_embed("Bots Can't Be Paid", "Cash can only be sent to members."), ephemeral=True)
        if not await ack(interaction):  # two accounts + a ledger write can outlast the 3-second window
            return
        try:
            result = await cash.transfer(db.handle(), interaction.guild_id, interaction.user.id,
                                         member.id, amount, interaction.id)
        except cash.InsufficientCash as e:
            return await interaction.followup.send(embed=error_embed("Not Enough Cash", str(e)), ephemeral=True)
        except cash.EconomyError as e:
            return await interaction.followup.send(embed=error_embed("Payment Failed", str(e)), ephemeral=True)
        embed = success_embed("Cash Sent", f"Successfully sent {format_cash(result['amount'])}.")
        embed.add_field(name="From", value=f"{interaction.user.display_name} — {format_cash(result['sender_balance'])}", inline=True)
        embed.add_field(name="To", value=f"{member.display_name} — {format_cash(result['recipient_balance'])}", inline=True)
        await interaction.edit_original_response(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="casino", description="Play a casino game with Cash")
    @app_commands.describe(game="Which game to play", entry="How much Cash to put in")
    @app_commands.choices(game=GAME_CHOICES)
    async def casino(self, interaction: discord.Interaction, game: app_commands.Choice[str],
                     entry: app_commands.Range[int, 1, MAX_CASH]):
        spec = CASINO_GAMES[game.value]
        # The entry is charged before the game message exists, so acknowledge first: if the
        # database is slow and the token expires, the member's Cash is taken and no message
        # ever appears (the expiry sweep refunds it, but nobody should be left waiting on that).
        if not await ack(interaction):
            return
        try:
            opened = await cash.open_game(db.handle(), interaction.guild_id, interaction.user.id,
                                          game.value, entry)
        except cash.InsufficientCash as e:
            return await interaction.followup.send(embed=error_embed("Not Enough Cash", str(e)), ephemeral=True)
        except cash.EconomyError as e:
            return await interaction.followup.send(embed=error_embed("Casino Closed", str(e)), ephemeral=True)

        embed = discord.Embed(
            title=f"{spec['emoji']} {spec['label']}",
            description=(
                f"Entry: **{format_cash(entry)}**\n{spec['odds']}\n\n"
                f"Balance: **{format_cash(opened['balance'])}**\n\n"
                f"Press **Play** within {CASINO_GAME_TIMEOUT} seconds. If the game expires, your entry is refunded."
            ),
            color=0x5865F2,
        )
        view = CasinoView(self, opened)
        await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def settle(self, interaction: discord.Interaction, game: dict, message: discord.Message | None):
        if not await ack(interaction):
            return
        outcome = play(game["game"], int(game["entry"]))
        try:
            result = await cash.settle_game(db.handle(), game["game_id"], game["guild_id"], game["user_id"],
                                            outcome["won"], outcome["reward"], outcome["detail"])
        except cash.EconomyError as e:
            return await interaction.followup.send(embed=error_embed("Casino Closed", str(e)), ephemeral=True)
        if result is None:  # the timer (or another press) settled it first
            return await interaction.followup.send(
                embed=info_embed("Game Already Ended", "This game was already settled — if it expired, the entry was refunded."),
                ephemeral=True,
            )
        game["detail"] = outcome["detail"]
        embed = _result_embed(game, outcome["won"], int(outcome["reward"]), result["balance"])
        if message is None:
            return await interaction.followup.send(embed=embed)
        try:
            await message.edit(embed=embed, view=_closed_view())
        except discord.HTTPException:
            await interaction.followup.send(embed=embed)

    async def expire(self, game: dict, message: discord.Message | None):
        """The Play button was never pressed: refund exactly once and close the message."""
        try:
            result = await cash.expire_game(db.handle(), game["game_id"])
        except Exception as e:
            print(f"⚠️ casino expiry failed for {game.get('game_id')}: {e}")
            return
        if result is None or message is None:
            return
        try:
            await message.edit(
                embed=info_embed(
                    "⌛ Game Expired",
                    f"No result was played, so your {format_cash(game['entry'])} entry was refunded.\n"
                    f"**Final balance: {format_cash(result['balance'])}**",
                ),
                view=_closed_view(),
            )
        except discord.HTTPException:
            pass

    # ── owner tools ──────────────────────────────────────────────────────
    # Direct balance edits are bot-owner only, answered privately (ephemeral), and still go
    # through database/economy.py so they are atomic and appear in economy_transactions.
    cash_group = app_commands.Group(name="cash", description="[Bot owner] Add, remove or set Cash", guild_only=True)

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if await has_owner_perms(interaction):
            return True
        await interaction.response.send_message(
            embed=error_embed("Owner Only", "Only the bot owner can manage Cash directly."), ephemeral=True)
        return False

    async def _single(self, interaction: discord.Interaction, member: discord.User, amount: int, mode: str):
        if not await self._owner_only(interaction):
            return
        if not await ack(interaction, ephemeral=True):
            return
        try:
            result = await cash.admin_adjust(db.handle(), interaction.guild_id, member.id, mode, amount,
                                             interaction.user.id)
        except cash.InsufficientCash as e:
            return await interaction.followup.send(embed=error_embed("Not Enough Cash", str(e)), ephemeral=True)
        except cash.EconomyError as e:
            return await interaction.followup.send(
                embed=error_embed("Cash Update Failed", f"{e}\nCheck `/balance` — it always shows the stored balance."),
                ephemeral=True,
            )
        detail = {
            "add": f"Added {format_cash(result['amount'])} to {member.mention}.",
            "remove": f"Removed {format_cash(result['amount'])} from {member.mention}.",
            "set": f"Replaced {member.mention}'s balance.",
        }[mode]
        embed = success_embed("Cash Updated", f"{detail}\nBalance: **{format_cash(result['balance'])}**")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def _many(self, interaction: discord.Interaction, amount: int, mode: str):
        if not await self._owner_only(interaction):
            return
        ids = [str(m.id) for m in interaction.guild.members if not m.bot]
        if not await ack(interaction, ephemeral=True):
            return
        try:
            result = await cash.admin_adjust_many(db.handle(), interaction.guild_id, ids, mode, amount,
                                                  interaction.user.id)
        except cash.EconomyError as e:
            # The loop stops at the first unexpected error; earlier accounts already changed.
            return await interaction.followup.send(
                embed=error_embed(
                    "Cash Update Stopped",
                    f"{e}\nBalances changed before the error stay applied — `/balance` shows the stored value.",
                ),
                ephemeral=True,
            )
        title = {"add": "Cash Added — Everyone", "remove": "Cash Removed — Everyone",
                 "set": "Cash Set — Everyone"}[mode]
        embed = success_embed(title, f"Updated **{result['updated']}** member(s).")
        if mode == "remove" and result["skipped"]:
            embed.add_field(
                name="Skipped",
                value=f"{result['skipped']} member(s) held less than {format_cash(result['amount'])} and were left alone.",
                inline=False,
            )
        else:
            embed.add_field(name="Amount", value=format_cash(result["amount"]), inline=True)
        embed.add_field(name="Batch", value=f"`{result['batch_id']}`", inline=True)
        embed.set_footer(text="Every non-bot member in this server; each change has its own ledger entry.")
        await interaction.followup.send(embed=embed)

    @cash_group.command(name="add", description="[Bot owner] Give Cash to one member")
    @app_commands.describe(member="Who receives the Cash", amount="How much Cash to add")
    async def cash_add(self, interaction: discord.Interaction, member: discord.User,
                       amount: app_commands.Range[int, 1, MAX_CASH]):
        await self._single(interaction, member, amount, "add")

    @cash_group.command(name="remove", description="[Bot owner] Take Cash from one member")
    @app_commands.describe(member="Who loses the Cash", amount="How much Cash to remove")
    async def cash_remove(self, interaction: discord.Interaction, member: discord.User,
                          amount: app_commands.Range[int, 1, MAX_CASH]):
        await self._single(interaction, member, amount, "remove")

    @cash_group.command(name="set", description="[Bot owner] Set one member's balance exactly")
    @app_commands.describe(member="Whose balance to replace", amount="The new balance (0 is allowed)")
    async def cash_set(self, interaction: discord.Interaction, member: discord.User,
                       amount: app_commands.Range[int, 0, MAX_CASH]):
        await self._single(interaction, member, amount, "set")

    @cash_group.command(name="add-all", description="[Bot owner] Give Cash to every member in this server")
    @app_commands.describe(amount="How much Cash each member gets")
    async def cash_add_all(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, MAX_CASH]):
        await self._many(interaction, amount, "add")

    @cash_group.command(name="remove-all", description="[Bot owner] Take Cash from every member in this server")
    @app_commands.describe(amount="How much Cash to remove from each member")
    async def cash_remove_all(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, MAX_CASH]):
        await self._many(interaction, amount, "remove")

    @cash_group.command(name="set-all", description="[Bot owner] Set every member's balance to the same amount")
    @app_commands.describe(amount="The new balance for everyone (0 is allowed)")
    async def cash_set_all(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0, MAX_CASH]):
        await self._many(interaction, amount, "set")

    @tasks.loop(seconds=30)
    async def expire_due(self):
        if not db.is_connected():
            return
        try:
            await cash.expire_due(db.handle())
        except Exception as e:
            print(f"⚠️ casino expiry sweep failed: {e}")

    @expire_due.before_loop
    async def _before_expire_due(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
