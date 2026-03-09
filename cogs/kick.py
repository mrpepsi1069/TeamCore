"""cogs/kick.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed


class Kick(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kick", description="Kick a user from the server (Admin only)")
    @app_commands.describe(user="User to kick", reason="Reason for kick")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Kick Members permission."), ephemeral=True
            )
        if user.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=error_embed("Invalid Target", "You cannot kick yourself!"), ephemeral=True
            )
        if user.id == interaction.guild.owner_id:
            return await interaction.response.send_message(
                embed=error_embed("Invalid Target", "You cannot kick the server owner!"), ephemeral=True
            )
        if user.top_role >= interaction.user.top_role:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You cannot kick someone with an equal or higher role!"), ephemeral=True
            )
        if not user.is_kickable():
            return await interaction.response.send_message(
                embed=error_embed("Cannot Kick", "I cannot kick this user."), ephemeral=True
            )

        try:
            dm_embed = discord.Embed(
                title="👢 You have been kicked",
                description=f"You were kicked from **{interaction.guild.name}**",
                color=0xFFA500,
            )
            dm_embed.add_field(name="Reason", value=reason)
            dm_embed.add_field(name="Kicked by", value=str(interaction.user))
            await user.send(embed=dm_embed)
        except Exception:
            pass

        try:
            await user.kick(reason=f"{reason} | Kicked by {interaction.user}")
            embed = discord.Embed(
                title="👢 User Kicked",
                description=f"**{user}** has been kicked from the server",
                color=0xFFA500,
            )
            embed.add_field(name="User ID", value=str(user.id), inline=True)
            embed.add_field(name="Kicked by", value=str(interaction.user), inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_thumbnail(url=user.display_avatar.url)
            await interaction.response.send_message(embed=embed)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Kick Failed", str(e)), ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Kick(bot))
