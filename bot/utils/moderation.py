"""utils/moderation.py — Hierarchy checks and mod-log helper shared by the moderation commands."""

from datetime import datetime, timezone

import discord
import database as db


def hierarchy_error(guild: discord.Guild, actor: discord.Member, target: discord.Member, verb: str) -> str | None:
    """Return a human-readable reason the action is not allowed, or None if it is.

    discord.py has no Member.is_bannable()/is_kickable()/is_moderatable() (those are
    discord.js), so the old moderation commands raised AttributeError on every use.
    """
    if target.id == actor.id:
        return f"You cannot {verb} yourself!"
    if target.id == guild.owner_id:
        return f"You cannot {verb} the server owner!"
    if target.id == guild.me.id:
        return f"I can't {verb} myself."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return f"You cannot {verb} someone with an equal or higher role!"
    if target.top_role >= guild.me.top_role:
        return f"I can't {verb} this user — their highest role is at or above mine."
    return None


async def send_mod_log(guild: discord.Guild, embed: discord.Embed) -> None:
    """Post to the server's configured Log Channel (set in /setup), if there is one."""
    if db.is_connected():  # also keep a searchable copy for the website's Moderation Logs page
        mod = next((f.value for f in embed.fields if f.name.lower().endswith(" by")), None)
        await db.col("moderation_logs").insert_one({
            "guild_id": str(guild.id), "title": embed.title or "", "text": (embed.description or "")[:500], "moderator": mod,
            "created_at": datetime.now(timezone.utc)})
    cfg = await db.get_guild_config(str(guild.id)) or {}
    cid = cfg.get("log_channel")
    if not cid:
        return
    channel = guild.get_channel(int(cid))
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


def format_minutes(minutes: int) -> str:
    """90 -> '1 hour, 30 minutes' (the old formatter reported 90 minutes as '1 hour(s)')."""
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins or not parts:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return ", ".join(parts)
