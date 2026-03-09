"""utils/permissions.py — Role-based permission helpers."""

import discord
import database as db


async def has_owner_perms(interaction: discord.Interaction) -> bool:
    if interaction.guild.owner_id == interaction.user.id:
        return True
    roles = await db.get_guild_roles(str(interaction.guild_id))
    owner_role_id = roles.get("owner")
    if owner_role_id:
        return interaction.user.get_role(int(owner_role_id)) is not None
    return False


async def has_coach_perms(interaction: discord.Interaction) -> bool:
    if interaction.guild.owner_id == interaction.user.id:
        return True
    roles = await db.get_guild_roles(str(interaction.guild_id))
    if roles.get("owner") and interaction.user.get_role(int(roles["owner"])):
        return True
    if roles.get("coach") and interaction.user.get_role(int(roles["coach"])):
        return True
    return False


# Aliases
has_manager_perms = has_coach_perms
has_staff_perms = has_coach_perms
