"""cogs/role.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed, success_embed


class Role(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="role", description="Assign a role to a user")
    @app_commands.describe(user="User to assign the role to", role="Role to assign")
    @app_commands.default_permissions(manage_roles=True)
    async def role(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        me = interaction.guild.me
        if role >= interaction.user.top_role:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You cannot assign a role equal to or higher than your own!"), ephemeral=True
            )
        if role >= me.top_role:
            return await interaction.response.send_message(
                embed=error_embed("Bot Permission Denied", "That role is above my highest role."), ephemeral=True
            )
        if user.get_role(role.id):
            return await interaction.response.send_message(
                embed=error_embed("Already Has Role", f"{user} already has **{role.name}**."), ephemeral=True
            )
        try:
            await user.add_roles(role)
            await interaction.response.send_message(
                embed=success_embed("Role Added", f"Added **{role.name}** to **{user}**")
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Failed", str(e)), ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Role(bot))
