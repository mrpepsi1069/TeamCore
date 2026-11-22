"""cogs/unrole.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed, success_embed


class Unrole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="unrole", description="Remove a role from a user")
    @app_commands.describe(user="User to remove the role from", role="Role to remove")
    @app_commands.default_permissions(manage_roles=True)
    async def unrole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        me = interaction.guild.me
        if role >= interaction.user.top_role:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You cannot remove a role equal to or higher than your own!"), ephemeral=True
            )
        if role >= me.top_role:
            return await interaction.response.send_message(
                embed=error_embed("Bot Permission Denied", "That role is above my highest role."), ephemeral=True
            )
        if not user.get_role(role.id):
            return await interaction.response.send_message(
                embed=error_embed("Doesn't Have Role", f"{user} does not have **{role.name}**."), ephemeral=True
            )
        try:
            await user.remove_roles(role)
            await interaction.response.send_message(
                embed=success_embed("Role Removed", f"Removed **{role.name}** from **{user}**")
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Failed", str(e)), ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Unrole(bot))
