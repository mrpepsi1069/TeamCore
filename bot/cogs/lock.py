"""cogs/lock.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed
from utils.locks import lock_channel
from utils.moderation import send_mod_log
from utils.permissions import has_manager_perms, is_server_admin
from utils.ui import brand


async def can_manage_channels(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.manage_channels or await has_manager_perms(interaction)


class Lock(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="lock", description="Stop regular members from sending messages in this channel")
    @app_commands.describe(reason="Why it's being locked", server_wide="Lock every text channel (admins only)")
    @app_commands.guild_only()
    async def lock(self, interaction: discord.Interaction, reason: app_commands.Range[str, 1, 200] = "No reason provided", server_wide: bool = False):
        if not await can_manage_channels(interaction) or (server_wide and not await is_server_admin(interaction)):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Server-wide locking needs an admin." if server_wide else "You need Manage Channels or a Manager role."), ephemeral=True
            )
        if not interaction.guild.me.guild_permissions.manage_roles:
            return await interaction.response.send_message(embed=error_embed("Missing Permission", "I need **Manage Roles** to change channel permissions."), ephemeral=True)
        await interaction.response.defer()

        targets = interaction.guild.text_channels if server_wide else [interaction.channel]
        counts = {"locked": 0, "already": 0, "failed": 0}
        for ch in targets:
            counts[await lock_channel(ch, interaction.user, reason)] += 1

        e = brand("🔒 Server Locked" if server_wide else "🔒 Channel Locked", f"**Reason:** {reason}", 0xED4245)
        e.add_field(name="Locked by", value=interaction.user.mention, inline=True)
        if server_wide or counts["failed"] or counts["already"]:
            e.add_field(name="Result", value=f"🔒 {counts['locked']} locked · ↩️ {counts['already']} already locked · ⚠️ {counts['failed']} failed", inline=False)
        await interaction.followup.send(embed=e)
        await send_mod_log(interaction.guild, e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Lock(bot))
