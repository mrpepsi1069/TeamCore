"""cogs/timeout.py"""

from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed
from utils.moderation import hierarchy_error, send_mod_log, format_minutes


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
        guild = interaction.guild
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Moderate Members permission."), ephemeral=True
            )
        if not guild.me.guild_permissions.moderate_members:
            return await interaction.response.send_message(
                embed=error_embed("Bot Permission Denied", "I need the Moderate Members permission."), ephemeral=True
            )
        problem = hierarchy_error(guild, interaction.user, user, "timeout")
        if problem:
            return await interaction.response.send_message(embed=error_embed("Cannot Timeout", problem), ephemeral=True)

        await interaction.response.defer()
        until = datetime.now(timezone.utc) + timedelta(minutes=duration)

        try:
            dm = discord.Embed(
                title="⏱️ You have been timed out",
                description=f"You were timed out in **{guild.name}**",
                color=0xFFA500,
            )
            dm.add_field(name="Duration", value=format_minutes(duration))
            dm.add_field(name="Reason", value=reason)
            dm.add_field(name="Timed out by", value=str(interaction.user))
            dm.add_field(name="Expires", value=discord.utils.format_dt(until, "F"))
            await user.send(embed=dm)
        except Exception:
            pass

        try:
            await user.timeout(until, reason=f"{reason} | Timed out by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.followup.send(embed=error_embed("Timeout Failed", str(e)), ephemeral=True)

        embed = discord.Embed(
            title="⏱️ User Timed Out",
            description=f"**{user}** has been timed out",
            color=0xFFA500,
        )
        embed.add_field(name="Duration", value=format_minutes(duration), inline=True)
        embed.add_field(name="Timed out by", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Expires", value=discord.utils.format_dt(until, "R"), inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await send_mod_log(guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Timeout(bot))
