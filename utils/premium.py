"""utils/premium.py — Premium feature checks."""

from datetime import datetime, timezone
import database as db


async def is_premium_guild(guild_id: str) -> bool:
    guild = await db.get_guild(guild_id)
    if not guild or not guild.get("premium"):
        return False
    expires = guild.get("premium_expires_at")
    if expires and expires < datetime.now(timezone.utc):
        await db.set_premium(guild_id, False, None)
        return False
    return True


async def get_premium_status(guild_id: str) -> dict:
    guild = await db.get_guild(guild_id)
    if not guild or not guild.get("premium"):
        return {"is_premium": False, "expires_at": None, "days_remaining": None}
    expires = guild.get("premium_expires_at")
    if not expires:
        return {"is_premium": True, "expires_at": None, "days_remaining": None}
    now = datetime.now(timezone.utc)
    days = (expires - now).days
    return {"is_premium": expires > now, "expires_at": expires, "days_remaining": days}
