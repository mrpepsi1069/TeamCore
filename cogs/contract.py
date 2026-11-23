"""cogs/contract.py — Player contract management."""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms
import database as db


class ContractView(discord.ui.View):
    """Persistent view for contract messages.

    IMPORTANT: user_id is intentionally NOT relied upon for the actual
    database update — it's only used as a fallback for display. The real
    source of truth is always looked up fresh via the message id, so a
    single instance of this view can be registered once at startup with
    bot.add_view() and keep working for every contract message, even after
    a bot restart (a per-user-parameterized view has no way to be
    re-registered for messages sent in a previous process).
    """

    def __init__(self, user_id: str | None = None):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Mark as Paid", style=discord.ButtonStyle.success, emoji="💰", custom_id="contract_mark_paid")
    async def mark_paid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not await has_coach_perms(interaction):
            return await interaction.followup.send("❌ Only coaches can manage contracts!", ephemeral=True)

        contract = await db.get_contract_by_message_id(str(interaction.message.id))
        if not contract:
            return await interaction.followup.send("❌ Contract not found in database!", ephemeral=True)

        user_id = contract["userId"]
        new_paid = not contract.get("paid", False)
        await db.mark_contract_paid(str(interaction.guild_id), user_id, new_paid)

        embed = interaction.message.embeds[0]
        new_embed = discord.Embed.from_dict(embed.to_dict())
        for f in new_embed.fields:
            if f.name == "💳 Paid":
                idx = new_embed.fields.index(f)
                new_embed.set_field_at(idx, name="💳 Paid", value="✅ **YES**" if new_paid else "❌ **NO**", inline=f.inline)
                break
        new_embed.color = 0x00FF00 if new_paid else 0xFFD700

        # Update button labels
        new_view = ContractView(user_id)
        new_view.children[0].label = "Mark as Unpaid" if new_paid else "Mark as Paid"
        new_view.children[0].style = discord.ButtonStyle.secondary if new_paid else discord.ButtonStyle.success
        await interaction.message.edit(embed=new_embed, view=new_view)

        await interaction.followup.send(f"✅ Contract marked as **{'PAID' if new_paid else 'UNPAID'}** for <@{user_id}>!", ephemeral=True)

    @discord.ui.button(label="Delete Contract", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="contract_delete")
    async def delete_contract(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not await has_coach_perms(interaction):
            return await interaction.followup.send("❌ Only coaches can manage contracts!", ephemeral=True)

        contract = await db.get_contract_by_message_id(str(interaction.message.id))
        if not contract:
            return await interaction.followup.send("❌ Contract not found in database!", ephemeral=True)

        user_id = contract["userId"]
        await db.remove_contract(str(interaction.guild_id), user_id)
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass
        await interaction.followup.send(f"🗑️ Contract for <@{user_id}> has been deleted!", ephemeral=True)


class Contract(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(ContractView())

    group = app_commands.Group(name="contract", description="Manage player contracts")

    @group.command(name="add", description="Add a player contract")
    @app_commands.describe(user="Player to contract", position="Position (e.g., QB)", amount="Contract amount ($)", due="Payment due date", terms="Contract terms (optional)")
    async def add(self, interaction: discord.Interaction, user: discord.User, position: str, amount: app_commands.Range[int, 0], due: str, terms: str = "Standard player contract"):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        if not cfg.get("contract_channel"):
            return await interaction.followup.send(embed=error_embed("Setup Required", "Run `/setup` first to configure the contract channel."), ephemeral=True)

        contract_ch = interaction.guild.get_channel(int(cfg["contract_channel"]))
        if not contract_ch:
            return await interaction.followup.send(embed=error_embed("Channel Not Found", "Contract channel no longer exists. Run `/setup` again."), ephemeral=True)

        existing = await db.get_player_contract(str(interaction.guild_id), str(user.id))
        if existing:
            return await interaction.followup.send(embed=error_embed("Contract Exists", f"{user.mention} already has a contract! Use `/contract remove` first."), ephemeral=True)

        pos = position.upper()
        embed = discord.Embed(title="📜 PLAYER CONTRACT", description=f"**{interaction.guild.name}** has contracted a new player!", color=0xFFD700)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="👤 Player",   value=user.mention,             inline=True)
        embed.add_field(name="🎮 Position", value=pos,                      inline=True)
        embed.add_field(name="\u200b",       value="\u200b",                inline=True)
        embed.add_field(name="💰 Amount",   value=f"${amount:,}",           inline=True)
        embed.add_field(name="📅 Due Date", value=due,                      inline=True)
        embed.add_field(name="💳 Paid",     value="❌ **NO**",              inline=True)
        embed.add_field(name="📋 Terms",    value=terms,                   inline=False)
        embed.add_field(name="✍️ By",       value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"Contract • {interaction.guild.name}")

        view = ContractView(str(user.id))
        try:
            msg = await contract_ch.send(
                content=f"🎉 **NEW CONTRACT!** Welcome {user.mention} to the team!",
                embed=embed,
                view=view,
            )
        except discord.HTTPException:
            return await interaction.followup.send(embed=error_embed("Couldn't Post", f"I can't post in {contract_ch.mention}."), ephemeral=True)

        try:
            saved = await db.add_contract(str(interaction.guild_id), str(user.id), pos, amount, due, terms, False, str(msg.id), str(interaction.user.id))
        except Exception:  # e.g. two coaches submitting a contract for the same player at once
            saved = None
        if not saved:
            try:
                await msg.delete()  # don't leave a contract card that isn't backed by a database record
            except discord.HTTPException:
                pass
            return await interaction.followup.send(embed=error_embed("Couldn't Save", f"The contract for {user.mention} could not be saved."), ephemeral=True)

        try:
            dm = discord.Embed(title="🎉 Congratulations!", description=f"You've been contracted to **{interaction.guild.name}**!", color=0x00FF00)
            dm.add_field(name="🎮 Position", value=pos,          inline=True)
            dm.add_field(name="💰 Amount",   value=f"${amount:,}", inline=True)
            dm.add_field(name="📅 Due Date", value=due,          inline=True)
            dm.add_field(name="📋 Terms",    value=terms,        inline=False)
            dm.set_footer(text=f"Welcome to {interaction.guild.name}!")
            await user.send(embed=dm)
        except Exception:
            pass

        await interaction.followup.send(embed=success_embed("Contract Created", f"Contract for {user.mention} posted to {contract_ch.mention}!\n\n**Position:** {pos}\n**Amount:** ${amount:,}\n**Due:** {due}"), ephemeral=True)

    @group.command(name="remove", description="Remove a player contract")
    @app_commands.describe(user="Player whose contract to remove")
    async def remove(self, interaction: discord.Interaction, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        contract = await db.get_player_contract(str(interaction.guild_id), str(user.id))
        if not contract:
            return await interaction.followup.send(embed=error_embed("Not Found", f"{user.mention} doesn't have an active contract."), ephemeral=True)

        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        if cfg.get("contract_channel") and contract.get("messageId"):
            ch = interaction.guild.get_channel(int(cfg["contract_channel"]))
            if ch:
                try:
                    m = await ch.fetch_message(int(contract["messageId"]))
                    await m.delete()
                except Exception:
                    pass

        await db.remove_contract(str(interaction.guild_id), str(user.id))
        await interaction.followup.send(embed=success_embed("Contract Removed", f"Removed contract for {user.mention}\n\n**Position:** {contract['position']}\n**Amount:** ${contract['amount']:,}"), ephemeral=True)

    @group.command(name="post", description="Post all active contracts")
    @app_commands.describe(filter="Filter contracts")
    @app_commands.choices(filter=[
        app_commands.Choice(name="All Contracts", value="all"),
        app_commands.Choice(name="Unpaid Only", value="unpaid"),
        app_commands.Choice(name="Paid Only", value="paid"),
    ])
    async def post(self, interaction: discord.Interaction, filter: str = "all"):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)
        await interaction.response.defer()
        contracts = await db.get_all_contracts(str(interaction.guild_id))
        if not contracts:
            return await interaction.followup.send(embed=error_embed("No Contracts", "No contracts found. Use `/contract add`."))

        if filter == "unpaid":
            contracts = [c for c in contracts if not c.get("paid")]
        elif filter == "paid":
            contracts = [c for c in contracts if c.get("paid")]

        if not contracts:
            return await interaction.followup.send(embed=error_embed("No Contracts", f"No {filter} contracts found."))

        lines = []
        total_paid = total_unpaid = 0
        for c in contracts:
            uname = f"<@{c['userId']}>"
            status = "✅ PAID" if c.get("paid") else "❌ UNPAID"
            emoji = "💚" if c.get("paid") else "💰"
            lines.append(f"{emoji} **{uname}** — {c['position']}\n└ ${c['amount']:,} | Due: {c['due']} | {status}")
            if c.get("paid"):
                total_paid += c["amount"]
            else:
                total_unpaid += c["amount"]

        label = {"all": "All Contracts", "unpaid": "Unpaid Contracts", "paid": "Paid Contracts"}[filter]
        # An embed description holds 4096 characters; keep what fits and say how many were cut.
        shown, used = [], 0
        for i, line in enumerate(lines):
            if used + len(line) + 2 > 3900:
                shown.append(f"*…and {len(lines) - i} more*")
                break
            shown.append(line)
            used += len(line) + 2
        embed = discord.Embed(title=f"📋 {label}", description="\n\n".join(shown), color=0x5865F2)
        embed.add_field(name="💰 Total Unpaid", value=f"${total_unpaid:,}", inline=True)
        embed.add_field(name="💚 Total Paid",   value=f"${total_paid:,}",   inline=True)
        embed.add_field(name="📊 Total",        value=str(len(contracts)),  inline=True)
        embed.set_footer(text=f"{interaction.guild.name} • Contract Overview")
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="view", description="View a player's contract (yours, or anyone's if you're a coach)")
    @app_commands.describe(user="Player to look up (leave empty for yourself)")
    async def view(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        if target.id != interaction.user.id and not await has_coach_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You can only view your own contract."), ephemeral=True
            )
        contract = await db.get_player_contract(str(interaction.guild_id), str(target.id))
        if not contract:
            return await interaction.response.send_message(
                embed=error_embed("No Contract", f"{target.mention} doesn't have a contract."), ephemeral=True
            )
        embed = discord.Embed(
            title="📜 Contract",
            color=0x00FF00 if contract.get("paid") else 0xFFD700,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="👤 Player", value=target.mention, inline=True)
        embed.add_field(name="🎮 Position", value=contract["position"], inline=True)
        embed.add_field(name="💰 Amount", value=f"${contract['amount']:,}", inline=True)
        embed.add_field(name="📅 Due Date", value=contract["due"], inline=True)
        embed.add_field(name="💳 Paid", value="✅ **YES**" if contract.get("paid") else "❌ **NO**", inline=True)
        embed.add_field(name="📋 Terms", value=contract["terms"], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Contract(bot))
