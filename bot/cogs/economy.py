"""cogs/economy.py — Cash: /balance, /pay and the casino.

The balance itself always lives in MongoDB (database/economy.py); this cog only validates
input and builds the messages, so a stale number read a moment ago can never be spent twice.
"""

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from database import economy as cash
from shared.economy import (
    CASINO_GAME_TIMEOUT, CASINO_GAMES, DAILY_MAX_STREAK, DAILY_MIN_ACCOUNT_AGE_DAYS,
    DAILY_MIN_MEMBER_HOURS, MAX_CASH, daily_reward, format_cash, play,
)
from utils.embeds import error_embed, info_embed, success_embed
from utils.permissions import has_owner_perms
from utils.ui import ack

GAME_CHOICES = [
    app_commands.Choice(name=f"{spec['label']} — {spec['odds']}", value=key)
    for key, spec in CASINO_GAMES.items()
]


def _until_utc_midnight() -> str:
    """How long until the next daily claim unlocks, for the 'already claimed' reply."""
    now = datetime.now(timezone.utc)
    left = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - now
    hours, minutes = divmod(int(left.total_seconds()) // 60, 60)
    return f"{hours}h {minutes}m"


def _humanise(delta: timedelta) -> str:
    """'2d 3h' / '5h 20m' / '40m' for 'how long until this unlocks' messages."""
    minutes = max(1, int(delta.total_seconds() // 60))
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _daily_lock_reason(user) -> str | None:
    """Why this account can't claim /daily yet, or None when it can.

    A fresh Discord account can be created for free, so an ungated daily would turn alt farming
    into the cheapest way to earn Cash. Both the account's age and its time in this server are
    checked, and neither depends on anything the caller can send.
    """
    now = datetime.now(timezone.utc)
    created = getattr(user, "created_at", None)
    if created is not None:
        age = now - created
        needed = timedelta(days=DAILY_MIN_ACCOUNT_AGE_DAYS)
        if age < needed:
            return (f"Your Discord account must be at least **{DAILY_MIN_ACCOUNT_AGE_DAYS} days** old "
                    f"before it can claim daily Cash — about **{_humanise(needed - age)}** to go.")
    joined = getattr(user, "joined_at", None)
    if joined is not None:
        here = now - joined
        needed = timedelta(hours=DAILY_MIN_MEMBER_HOURS)
        if here < needed:
            return (f"You need **{DAILY_MIN_MEMBER_HOURS} hours** in this server before claiming daily Cash "
                    f"— about **{_humanise(needed - here)}** to go.")
    return None


def _open_embed(opened: dict) -> discord.Embed:
    """The message for a game waiting on Play — used by /casino and by Play Again."""
    spec = CASINO_GAMES[opened["game"]]
    return discord.Embed(
        title=f"{spec['emoji']} {spec['label']}",
        description=(
            f"Entry: **{format_cash(opened['entry'])}**\n{spec['odds']}\n\n"
            f"Balance: **{format_cash(opened['balance'])}**\n\n"
            f"Press **Play** within {CASINO_GAME_TIMEOUT} seconds. If the game expires, your entry is refunded."
        ),
        color=0x5865F2,
    )


class ClosedView(discord.ui.View):
    """A settled game: the outcome, plus one more round for the same player."""

    def __init__(self, cog: "Economy", game: dict, timeout: float = 180):
        super().__init__(timeout=timeout)
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

    @discord.ui.button(label="Play Again", emoji="🔁", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.cog.replay(interaction, self.game, self.message)

    async def on_timeout(self):
        # A button that outlives its view would still look pressable, so grey it out.
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


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

    async def economy_on(self, interaction: discord.Interaction) -> bool:
        """Whether Cash is enabled in this server (`/setup` and the dashboard toggle it).

        A missing setting means on, and a failed read never blocks play: the toggle is a
        preference, not a safety control.
        """
        try:
            cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        except Exception:
            return True
        if cfg.get("economy_enabled", True):
            return True
        await interaction.response.send_message(
            embed=error_embed(
                "Cash Is Turned Off",
                "This server turned Cash off. A server admin can turn it back on in `/setup` or in the dashboard's Server settings.",
            ),
            ephemeral=True,
        )
        return False

    @app_commands.command(name="balance", description="Check how much Cash you (or another member) have")
    @app_commands.describe(member="Whose balance to show (default: yours)")
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        if not await self.economy_on(interaction):
            return
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
        if not await self.economy_on(interaction):
            return
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
        if not await self.economy_on(interaction):
            return
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

        embed = _open_embed(opened)
        view = CasinoView(self, opened)
        await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="daily", description="Claim your daily Cash — consecutive days pay more")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction):
        if not await self.economy_on(interaction):
            return
        blocked = _daily_lock_reason(interaction.user)
        if blocked:
            return await interaction.response.send_message(
                embed=info_embed("Daily Cash Locked", blocked), ephemeral=True)
        if not await ack(interaction):
            return
        try:
            result = await cash.claim_daily(db.handle(), interaction.guild_id, interaction.user.id)
        except cash.EconomyError as e:
            return await interaction.followup.send(embed=error_embed("Daily Cash Unavailable", str(e)), ephemeral=True)
        if not result["claimed"]:
            return await interaction.followup.send(
                embed=info_embed(
                    "Already Claimed",
                    f"Your next daily Cash unlocks in **{_until_utc_midnight()}** (midnight UTC).\n"
                    f"Your streak is **{result['streak']} day(s)** — keep it going.",
                ),
                ephemeral=True,
            )
        streak = result["streak"]
        embed = success_embed(
            "💵 Daily Cash",
            f"You claimed **{format_cash(result['amount'])}** — day **{streak}** of your streak.\n"
            f"**Final balance: {format_cash(result['balance'])}**",
        )
        embed.set_footer(
            text=(f"Tomorrow pays {format_cash(daily_reward(streak + 1))}." if streak < DAILY_MAX_STREAK
                  else f"Day {DAILY_MAX_STREAK} is the top streak — {format_cash(daily_reward(DAILY_MAX_STREAK))} every day.")
        )
        await interaction.edit_original_response(embed=embed, allowed_mentions=discord.AllowedMentions.none())

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
        view = ClosedView(self, game)
        if message is None:
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)
            return
        try:
            await message.edit(embed=embed, view=view)
            view.message = message
        except discord.HTTPException:
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

    async def expire(self, game: dict, message: discord.Message | None):
        """The Play button was never pressed: refund exactly once and close the message."""
        try:
            result = await cash.expire_game(db.handle(), game["game_id"])
        except Exception as e:
            print(f"⚠️ casino expiry failed for {game.get('game_id')}: {e}")
            return
        if result is None or message is None:
            return
        view = ClosedView(self, game)
        try:
            await message.edit(
                embed=info_embed(
                    "⌛ Game Expired",
                    f"No result was played, so your {format_cash(game['entry'])} entry was refunded.\n"
                    f"**Final balance: {format_cash(result['balance'])}**",
                ),
                view=view,
            )
            view.message = message
        except discord.HTTPException:
            pass

    async def replay(self, interaction: discord.Interaction, game: dict, message: discord.Message | None):
        """Play Again: a fresh round of the same game at the same entry, charged like any other."""
        if not await self.economy_on(interaction):
            return
        if not await ack(interaction):
            return
        try:
            opened = await cash.open_game(db.handle(), interaction.guild_id, interaction.user.id,
                                          game["game"], int(game["entry"]))
        except cash.InsufficientCash as e:
            return await interaction.followup.send(embed=error_embed("Not Enough Cash", str(e)), ephemeral=True)
        except cash.EconomyError as e:
            return await interaction.followup.send(embed=error_embed("Casino Closed", str(e)), ephemeral=True)
        embed = _open_embed(opened)
        view = CasinoView(self, opened)
        if message is None:
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)
            return
        try:
            await message.edit(embed=embed, view=view)
            view.message = message
        except discord.HTTPException:
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

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
        if not await self.economy_on(interaction):
            return
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
        if not await self.economy_on(interaction):
            return
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
