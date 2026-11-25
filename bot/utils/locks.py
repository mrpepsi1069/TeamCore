"""utils/locks.py — Lock/unlock channels while preserving the original @everyone overwrite."""

from datetime import datetime, timezone
import discord
import database as db

_FIELDS = ("send_messages", "send_messages_in_threads")


async def lock_channel(channel: discord.TextChannel, actor: discord.abc.User, reason: str) -> str:
    """Returns 'locked', 'already' or 'failed'. The pre-lock overwrite is stored so unlock can restore it exactly
    (True / False / None=inherit) rather than blindly resetting to 'allowed'."""
    guild, everyone = channel.guild, channel.guild.default_role
    coll = db.col("channel_locks")
    if await coll.find_one({"guild_id": str(guild.id), "channel_id": str(channel.id)}):
        return "already"
    ow = channel.overwrites_for(everyone)
    await coll.insert_one({
        "guild_id": str(guild.id), "channel_id": str(channel.id), "original": {f: getattr(ow, f) for f in _FIELDS},
        "locked_by": str(actor.id), "reason": reason, "locked_at": datetime.now(timezone.utc),
    })
    try:
        for f in _FIELDS:
            setattr(ow, f, False)
        await channel.set_permissions(everyone, overwrite=ow, reason=f"Locked by {actor}: {reason}")
        return "locked"
    except discord.HTTPException:
        await coll.delete_one({"guild_id": str(guild.id), "channel_id": str(channel.id)})
        return "failed"


async def unlock_channel(channel: discord.TextChannel, actor: discord.abc.User) -> str:
    """Returns 'unlocked', 'not_locked' or 'failed'."""
    guild, everyone = channel.guild, channel.guild.default_role
    coll = db.col("channel_locks")
    doc = await coll.find_one({"guild_id": str(guild.id), "channel_id": str(channel.id)})
    if not doc:
        return "not_locked"
    ow = channel.overwrites_for(everyone)
    for f in _FIELDS:
        setattr(ow, f, doc["original"].get(f))
    try:
        await channel.set_permissions(everyone, overwrite=None if ow.is_empty() else ow, reason=f"Unlocked by {actor}")
    except discord.HTTPException:
        return "failed"
    await coll.delete_one({"_id": doc["_id"]})
    return "unlocked"
