"""utils/permissions.py — Role-based permission checks using DB config"""

import discord
import database as db
from config import OWNER_ID

# Each tier lists the configured roles that satisfy it (higher roles always satisfy lower tiers).
_ADMIN       = ("admin_role",)
_FRANCHISE   = ("admin_role", "franchise_owner_role")
_MANAGER     = ("admin_role", "franchise_owner_role", "general_manager_role")
_COACH       = ("admin_role", "franchise_owner_role", "general_manager_role", "head_coach_role")
_STAFF       = ("admin_role", "franchise_owner_role", "general_manager_role", "head_coach_role", "assistant_coach_role")


async def _get_config(guild_id: str) -> dict:
    return await db.get_guild_config(guild_id) or {}


def _member_matches(cfg: dict, member: discord.Member, role_keys: tuple) -> bool:
    for key in role_keys:
        rid = cfg.get(key)
        if rid and member.get_role(int(rid)):
            return True
    return False


async def _has_role(interaction: discord.Interaction, *role_keys: str) -> bool:
    """True if the user holds any of the given configured roles, or owns the server."""
    guild = interaction.guild
    if guild is None:  # DMs have no roles
        return False
    if interaction.user.id == guild.owner_id:
        return True
    if not isinstance(interaction.user, discord.Member):
        return False
    cfg = await _get_config(str(guild.id))
    return _member_matches(cfg, interaction.user, role_keys)


async def member_is_staff(guild: discord.Guild, member: discord.Member) -> bool:
    """Staff-or-higher check for an arbitrary member (not just the person who ran a command)."""
    if member.id == guild.owner_id:
        return True
    cfg = await _get_config(str(guild.id))
    return _member_matches(cfg, member, _STAFF)


# ── Permission levels ────────────────────────────────────────────

async def has_owner_perms(interaction: discord.Interaction) -> bool:
    """Bot owner only."""
    return interaction.user.id == OWNER_ID


async def has_admin_perms(interaction: discord.Interaction) -> bool:
    """Admin role — or server owner.

    (This used to accept every staff tier all the way down to Assistant Coach,
    which contradicted its own name.)
    """
    return await _has_role(interaction, *_ADMIN)


async def has_franchise_owner_perms(interaction: discord.Interaction) -> bool:
    """Franchise Owner, Admin — or server owner."""
    return await _has_role(interaction, *_FRANCHISE)


async def has_manager_perms(interaction: discord.Interaction) -> bool:
    """General Manager and above — or server owner."""
    return await _has_role(interaction, *_MANAGER)


async def has_coach_perms(interaction: discord.Interaction) -> bool:
    """Head Coach and above — or server owner."""
    return await _has_role(interaction, *_COACH)


async def has_staff_perms(interaction: discord.Interaction) -> bool:
    """Assistant Coach and above — or server owner."""
    return await _has_role(interaction, *_STAFF)


async def is_server_admin(interaction: discord.Interaction) -> bool:
    """Server owner, Discord Administrator, or the configured Admin role."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return await has_admin_perms(interaction)


async def is_staff_or_mod(interaction: discord.Interaction) -> bool:
    """Staff role (Assistant Coach+), or Discord Manage Messages / Manage Server."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    p = interaction.user.guild_permissions
    return p.manage_messages or p.manage_guild or await has_staff_perms(interaction)


async def is_manager_or_mod(interaction: discord.Interaction) -> bool:
    """Manager role (GM+), or Discord Manage Server."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.guild_permissions.manage_guild or await has_manager_perms(interaction)
