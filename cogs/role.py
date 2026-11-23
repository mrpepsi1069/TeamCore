"""cogs/role.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed, success_embed
from utils.moderation import send_mod_log


async def check_role_change(interaction: discord.Interaction, role: discord.Role) -> str | None:
    """Shared validation for /role and /unrole. Returns an error message or None."""
    guild = interaction.guild
    # default_permissions is only a *default* that server admins can change under
    # Integrations, so the permission must also be enforced in code.
    if not interaction.user.guild_permissions.manage_roles:
        return "You need Manage Roles permission."
    if role.is_default() or role.managed:
        return "That role can't be assigned manually."
    if interaction.user.id != guild.owner_id and role >= interaction.user.top_role:
        return "You cannot manage a role equal to or higher than your own!"
    if role >= guild.me.top_role:
        return "That role is above my highest role."
    return None


class Role(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="role", description="Assign a role to a user")
    @app_commands.describe(user="User to assign the role to", role="Role to assign")
    @app_commands.default_permissions(manage_roles=True)
    async def role(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        problem = await check_role_change(interaction, role)
        if problem:
            return await interaction.response.send_message(embed=error_embed("Cannot Assign Role", problem), ephemeral=True)
        if user.get_role(role.id):
            return await interaction.response.send_message(
                embed=error_embed("Already Has Role", f"{user} already has **{role.name}**."), ephemeral=True
            )
        try:
            await user.add_roles(role, reason=f"Added by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(embed=error_embed("Failed", str(e)), ephemeral=True)

        embed = success_embed("Role Added", f"Added **{role.name}** to **{user}**")
        await interaction.response.send_message(embed=embed)
        await send_mod_log(interaction.guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Role(bot))
