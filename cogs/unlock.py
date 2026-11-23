"""cogs/unlock.py"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.embeds import error_embed
from utils.locks import unlock_channel
from utils.moderation import send_mod_log
from utils.permissions import is_server_admin
from utils.ui import brand
from cogs.lock import can_manage_channels


class Unlock(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="unlock", description="Restore normal messaging permissions")
    @app_commands.describe(reason="Optional reason", server_wide="Unlock every channel that was locked (admins only)")
    @app_commands.guild_only()
    async def unlock(self, interaction: discord.Interaction, reason: app_commands.Range[str, 1, 200] = "No reason provided", server_wide: bool = False):
        if not await can_manage_channels(interaction) or (server_wide and not await is_server_admin(interaction)):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Server-wide unlocking needs an admin." if server_wide else "You need Manage Channels or a Manager role."), ephemeral=True
            )
        await interaction.response.defer()

        if server_wide:  # every channel TeamCore locked (including ones locked one at a time)
            docs = await db.col("channel_locks").find({"guild_id": str(interaction.guild_id)}).to_list(None)
            targets = [c for c in (interaction.guild.get_channel(int(d["channel_id"])) for d in docs) if c]
            for d in docs:  # channels deleted since being locked
                if interaction.guild.get_channel(int(d["channel_id"])) is None:
                    await db.col("channel_locks").delete_one({"_id": d["_id"]})
        else:
            targets = [interaction.channel]

        counts = {"unlocked": 0, "not_locked": 0, "failed": 0}
        for ch in targets:
            counts[await unlock_channel(ch, interaction.user)] += 1

        if not server_wide and counts["not_locked"]:
            return await interaction.followup.send(embed=error_embed("Not Locked", "I didn't lock this channel, so there's nothing to restore. Use Discord's permissions to change it manually."), ephemeral=True)

        e = brand("🔓 Server Unlocked" if server_wide else "🔓 Channel Unlocked", f"**Reason:** {reason}", 0x57F287)
        e.add_field(name="Unlocked by", value=interaction.user.mention, inline=True)
        if server_wide:
            e.add_field(name="Result", value=f"🔓 {counts['unlocked']} unlocked · ⚠️ {counts['failed']} failed", inline=False)
        await interaction.followup.send(embed=e)
        await send_mod_log(interaction.guild, e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Unlock(bot))
