"""cogs/activitycheck.py"""

from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_manager_perms
import database as db


class ActivityCheck(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="activitycheck", description="Create an activity check (Manager only)")
    @app_commands.describe(duration="Duration in hours (1-168)", role="Role to check (optional)")
    async def activitycheck(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Range[int, 1, 168],
        role: discord.Role = None,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )

        expires_at = datetime.now(timezone.utc) + timedelta(hours=duration)
        ts = int(expires_at.timestamp())

        embed = discord.Embed(
            title="✅ Activity Check",
            description=(
                f"{role.mention if role else '@everyone'}\n\n"
                f"**React with ✅ to confirm you're active!**\n\n"
                f"Expires: <t:{ts}:R>"
            ),
            color=0x57F287,
        )

        msg = await interaction.channel.send(
            content=role.mention if role else None,
            embed=embed,
        )
        await msg.add_reaction("✅")

        await db.create_activity_check(
            str(interaction.guild_id), str(msg.id),
            str(interaction.channel_id), expires_at, str(interaction.user.id)
        )

        await interaction.response.send_message(
            embed=success_embed("Activity Check Created", f"Expires in **{duration}** hour(s)."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityCheck(bot))
