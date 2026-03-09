"""cogs/gametime.py — Game time attendance polls with button support."""

import asyncio
import re
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms
import database as db


def _extract_user_ids(value: str) -> list[str]:
    return re.findall(r"<@(\d+)>", value)


def _format_list(ids: list[str]) -> str:
    return "\n".join(f"• <@{uid}>" for uid in ids) if ids else "• None yet"


class GametimeView(discord.ui.View):
    def __init__(self, message_id: str = None):
        super().__init__(timeout=None)
        self.message_id = message_id

    async def _handle(self, interaction: discord.Interaction, response: str, message_id: str = None):
        await interaction.response.defer(ephemeral=True)

        # Determine which message to update
        target_msg = interaction.message
        is_dm = False

        if message_id:
            is_dm = True
            gametime = await db.get_gametime_by_message_id(message_id)
            if not gametime:
                return await interaction.followup.send("❌ Could not find the original poll.", ephemeral=True)
            try:
                ch = interaction.client.get_channel(int(gametime.get("channelId") or gametime.get("channel_id")))
                target_msg = await ch.fetch_message(int(message_id))
            except Exception:
                return await interaction.followup.send("❌ Could not find the poll message.", ephemeral=True)

        embed = target_msg.embeds[0]
        fields = embed.fields
        can_ids   = _extract_user_ids(fields[0].value)
        cant_ids  = _extract_user_ids(fields[1].value)
        unsure_ids = _extract_user_ids(fields[2].value)

        uid = str(interaction.user.id)
        can_ids   = [i for i in can_ids   if i != uid]
        cant_ids  = [i for i in cant_ids  if i != uid]
        unsure_ids = [i for i in unsure_ids if i != uid]

        if response == "yes":    can_ids.append(uid)
        elif response == "no":   cant_ids.append(uid)
        elif response == "unsure": unsure_ids.append(uid)

        new_embed = discord.Embed.from_dict(embed.to_dict())
        new_embed.clear_fields()
        new_embed.add_field(name=f"✅ Can Make ({len(can_ids)})",    value=_format_list(can_ids),    inline=False)
        new_embed.add_field(name=f"❌ Can't Make ({len(cant_ids)})", value=_format_list(cant_ids),   inline=False)
        new_embed.add_field(name=f"❓ Unsure ({len(unsure_ids)})",   value=_format_list(unsure_ids), inline=False)
        await target_msg.edit(embed=new_embed)

        labels = {"yes": "Can Make ✅", "no": "Can't Make ❌", "unsure": "Unsure ❓"}
        text = f"✅ Response recorded: **{labels[response]}**"
        if is_dm:
            text += "\n\nThe poll in the server has been updated!"
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success, custom_id="gametime_yes", emoji="✅")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "yes")

    @discord.ui.button(label="No", style=discord.ButtonStyle.danger, custom_id="gametime_no", emoji="❌")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "no")

    @discord.ui.button(label="Unsure", style=discord.ButtonStyle.secondary, custom_id="gametime_unsure", emoji="❓")
    async def unsure(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "unsure")


class Gametime(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(GametimeView())

    @app_commands.command(name="gametime", description="Create a game-time attendance poll")
    @app_commands.describe(league="League name/abbreviation", time="Game time (e.g., 8 PM EST)", role="Role to ping")
    async def gametime(self, interaction: discord.Interaction, league: str, time: str, role: discord.Role):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="⏰ Gametime Scheduled",
            description=f"**League:** {league}\n**Time:** {time}",
            color=0x5865F2,
        )
        embed.add_field(name="✅ Can Make (0)",    value="• None yet", inline=False)
        embed.add_field(name="❌ Can't Make (0)", value="• None yet", inline=False)
        embed.add_field(name="❓ Unsure (0)",      value="• None yet", inline=False)
        embed.set_footer(text="LockerRoom | Gametime Manager")

        try:
            msg = await interaction.channel.send(
                content=role.mention,
                embed=embed,
                view=GametimeView(),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.Forbidden:
            return await interaction.followup.send("❌ I don't have permission to send messages here.", ephemeral=True)

        jump = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{msg.id}"
        await db.create_gametime(
            str(interaction.guild_id), league, time,
            str(msg.id), str(interaction.channel_id), str(role.id), str(interaction.user.id),
        )

        # DM role members
        dm_count = 0
        for member in role.members:
            if member.bot:
                continue
            try:
                dm_embed = discord.Embed(
                    title="📅 Gametime Attendance",
                    description=f"**League:** {league}\n**Time:** {time}\n\nCan you make it?",
                    color=0x5865F2,
                )
                dm_embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
                dm_embed.add_field(name="🔗 Jump to Poll", value=f"[Click Here]({jump})", inline=False)

                dm_view = discord.ui.View(timeout=None)
                dm_view.add_item(discord.ui.Button(label="Yes",    style=discord.ButtonStyle.success,   custom_id=f"gametime_yes_{msg.id}",    emoji="✅"))
                dm_view.add_item(discord.ui.Button(label="No",     style=discord.ButtonStyle.danger,    custom_id=f"gametime_no_{msg.id}",     emoji="❌"))
                dm_view.add_item(discord.ui.Button(label="Unsure", style=discord.ButtonStyle.secondary, custom_id=f"gametime_unsure_{msg.id}", emoji="❓"))
                await member.send(embed=dm_embed, view=dm_view)
                dm_count += 1
                await asyncio.sleep(0.8)
            except Exception:
                pass

        await interaction.followup.send(
            embed=success_embed("Gametime Created", f"Poll created for **{league}**\n📨 {dm_count} DMs sent\n⏰ {time}"),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle DM gametime buttons with message IDs in the custom_id."""
        if not interaction.data or interaction.data.get("component_type") != 2:
            return
        cid = interaction.data.get("custom_id", "")
        # Format: gametime_{response}_{message_id}
        parts = cid.split("_")
        if len(parts) != 3 or parts[0] != "gametime":
            return
        response = parts[1]
        message_id = parts[2]
        view = GametimeView()
        await view._handle(interaction, response, message_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Gametime(bot))
