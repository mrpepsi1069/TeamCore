"""cogs/dmmembers.py"""

import asyncio
import time
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms

COOLDOWN_SECONDS = 300  # 5 minutes
_cooldowns: dict[int, float] = {}  # guild_id -> last used timestamp


class DmMembers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="dmmembers", description="DM all members with up to 4 roles (Coach only)")
    @app_commands.describe(
        role1="First role to DM",
        role2="Second role to DM (optional)",
        role3="Third role to DM (optional)",
        role4="Fourth role to DM (optional)",
        message="Message to send (max 1000 chars)",
    )
    async def dmmembers(
        self,
        interaction: discord.Interaction,
        role1: discord.Role,
        message: app_commands.Range[str, 1, 1000],
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
        role4: discord.Role | None = None,
    ):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True
            )

        # 5 minute cooldown per guild
        guild_id = interaction.guild_id
        now = time.time()
        last_used = _cooldowns.get(guild_id, 0)
        remaining = COOLDOWN_SECONDS - (now - last_used)
        if remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            return await interaction.response.send_message(
                embed=error_embed("Cooldown", f"This command is on cooldown. Try again in **{mins}m {secs}s**."),
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # Collect unique members across all provided roles
        roles = [r for r in [role1, role2, role3, role4] if r is not None]
        seen = set()
        members = []
        for role in roles:
            for m in role.members:
                if not m.bot and m.id not in seen:
                    seen.add(m.id)
                    members.append(m)

        if not members:
            role_mentions = " ".join(r.mention for r in roles)
            return await interaction.followup.send(
                embed=error_embed("No Members", f"No members found with the selected roles: {role_mentions}"),
                ephemeral=True
            )

        # Channel jump link
        channel_link = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}"
        role_names = " • ".join(r.name for r in roles)

        # Update cooldown
        _cooldowns[guild_id] = now

        ok = fail = 0
        for m in members:
            try:
                embed = discord.Embed(
                    title="Message Received",
                    description=f"🔔 **Message:** {message}",
                    color=0x5865F2,
                )
                embed.set_author(
                    name=interaction.guild.name,
                    icon_url=interaction.guild.icon.url if interaction.guild.icon else None
                )
                embed.add_field(name="👤 Sent By", value=str(interaction.user), inline=False)
                embed.add_field(
                    name="🏠 Server",
                    value=f"[{interaction.guild.name} • #{interaction.channel.name}]({channel_link})",
                    inline=False
                )
                await m.send(embed=embed)
                ok += 1
                await asyncio.sleep(1)
            except Exception:
                fail += 1

        role_mentions = " ".join(r.mention for r in roles)
        text = f"Successfully sent DM to **{ok}** member(s) across {role_mentions}"
        if fail:
            text += f"\n\n⚠️ Failed to DM **{fail}** member(s) (DMs disabled)"
        await interaction.followup.send(embed=success_embed("📨 DMs Sent", text), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DmMembers(bot))