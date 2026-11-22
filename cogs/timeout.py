"""cogs/timeout.py"""

from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed


def _fmt_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minute(s)"
    if minutes < 1440:
        return f"{minutes // 60} hour(s)"
    return f"{minutes // 1440} day(s)"


class Timeout(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="timeout", description="Timeout a user (Moderator only)")
    @app_commands.describe(user="User to timeout", duration="Duration in minutes (max 40320)", reason="Reason")
    @app_commands.default_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: app_commands.Range[int, 1, 40320],
        reason: str = "No reason provided",
    ):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Moderate Members permission."), ephemeral=True
            )
        if user.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=error_embed("Invalid Target", "You cannot timeout yourself!"), ephemeral=True
            )
        if user.id == interaction.guild.owner_id:
            return await interaction.response.send_message(
                embed=error_embed("Invalid Target", "You cannot timeout the server owner!"), ephemeral=True
            )
        if user.top_role >= interaction.user.top_role:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You cannot timeout someone with a higher or equal role!"), ephemeral=True
            )
        if not user.is_moderatable():
            return await interaction.response.send_message(
                embed=error_embed("Cannot Timeout", "I cannot timeout this user."), ephemeral=True
            )

        until = datetime.now(timezone.utc) + timedelta(minutes=duration)

        try:
            dm = discord.Embed(
                title="⏱️ You have been timed out",
                description=f"You were timed out in **{interaction.guild.name}**",
                color=0xFFA500,
            )
            dm.add_field(name="Duration", value=_fmt_duration(duration))
            dm.add_field(name="Reason", value=reason)
            dm.add_field(name="Timed out by", value=str(interaction.user))
            dm.add_field(name="Expires", value=discord.utils.format_dt(until, "F"))
            await user.send(embed=dm)
        except Exception:
            pass

        try:
            await user.timeout(until, reason=f"{reason} | Timed out by {interaction.user}")
            embed = discord.Embed(
                title="⏱️ User Timed Out",
                description=f"**{user}** has been timed out",
                color=0xFFA500,
            )
            embed.add_field(name="Duration", value=_fmt_duration(duration), inline=True)
            embed.add_field(name="Timed out by", value=str(interaction.user), inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Expires", value=discord.utils.format_dt(until, "R"), inline=True)
            embed.set_thumbnail(url=user.display_avatar.url)
            await interaction.response.send_message(embed=embed)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Timeout Failed", str(e)), ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Timeout(bot))
