"""cogs/unrole.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed, success_embed
from utils.moderation import send_mod_log
from cogs.role import check_role_change


class Unrole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="unrole", description="Remove a role from a user")
    @app_commands.describe(user="User to remove the role from", role="Role to remove")
    @app_commands.default_permissions(manage_roles=True)
    async def unrole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        problem = await check_role_change(interaction, role)
        if problem:
            return await interaction.response.send_message(embed=error_embed("Cannot Remove Role", problem), ephemeral=True)
        if not user.get_role(role.id):
            return await interaction.response.send_message(
                embed=error_embed("Doesn't Have Role", f"{user} does not have **{role.name}**."), ephemeral=True
            )
        try:
            await user.remove_roles(role, reason=f"Removed by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(embed=error_embed("Failed", str(e)), ephemeral=True)

        embed = success_embed("Role Removed", f"Removed **{role.name}** from **{user}**")
        await interaction.response.send_message(embed=embed)
        await send_mod_log(interaction.guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Unrole(bot))
