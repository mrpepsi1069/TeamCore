"""cogs/ban.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed


class Ban(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ban", description="Ban a user from the server (Admin only)")
    @app_commands.describe(user="User to ban", reason="Reason for ban", delete_days="Delete messages from last X days (0-7)")
    @app_commands.default_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str = "No reason provided",
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ):
        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Ban Members permission."), ephemeral=True
            )

        member = interaction.guild.get_member(user.id)

        if member:
            if user.id == interaction.user.id:
                return await interaction.response.send_message(
                    embed=error_embed("Invalid Target", "You cannot ban yourself!"), ephemeral=True
                )
            if user.id == interaction.guild.owner_id:
                return await interaction.response.send_message(
                    embed=error_embed("Invalid Target", "You cannot ban the server owner!"), ephemeral=True
                )
            if member.top_role >= interaction.user.top_role:
                return await interaction.response.send_message(
                    embed=error_embed("Permission Denied", "You cannot ban someone with an equal or higher role!"),
                    ephemeral=True,
                )
            if not member.is_bannable():
                return await interaction.response.send_message(
                    embed=error_embed("Cannot Ban", "I do not have permission to ban this user!"), ephemeral=True
                )

            try:
                dm_embed = discord.Embed(
                    title="🔨 You have been banned",
                    description=f"You have been banned from **{interaction.guild.name}**",
                    color=0xED4245,
                )
                dm_embed.add_field(name="Reason", value=reason)
                dm_embed.add_field(name="Banned by", value=str(interaction.user))
                await user.send(embed=dm_embed)
            except Exception:
                pass

        try:
            await interaction.guild.ban(
                user,
                reason=f"{reason} | Banned by {interaction.user}",
                delete_message_days=delete_days,
            )
            embed = discord.Embed(
                title="🔨 User Banned",
                description=f"**{user}** has been banned from the server",
                color=0xED4245,
            )
            embed.add_field(name="User ID", value=str(user.id), inline=True)
            embed.add_field(name="Banned by", value=str(interaction.user), inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Messages Deleted", value=f"{delete_days} day(s)", inline=True)
            embed.set_thumbnail(url=user.display_avatar.url)
            await interaction.response.send_message(embed=embed)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Ban Failed", str(e)), ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Ban(bot))
