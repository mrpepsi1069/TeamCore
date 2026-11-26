"""utils/schedule.py — record gametime and time polls so the dashboard can list them.

One poll can be created from two places: the slash commands (/gametime, /times) and the dashboard (which asks the bot
through `gametime_create` / `times_create`). Both write the same row here, so the Scheduling page shows everything.
"""

from datetime import datetime, timezone

import database as db


async def record_schedule(guild, kind: str, league: str, times, role, channel, message, created_by="") -> None:
    """Store where a poll lives (channel + message) for the dashboard's recent list.

    Best effort on purpose: a poll that is already posted must never be reported as failed because this row could not
    be written.
    """
    try:
        await db.col("schedule_polls").insert_one({
            "guild_id": str(guild.id),
            "kind": kind,                                  # "gametime" | "times"
            "league": league,
            "times": [str(t) for t in (times or [])],
            "role_id": str(getattr(role, "id", "") or ""),
            "role_name": getattr(role, "name", "") or "",
            "channel_id": str(channel.id),
            "message_id": str(message.id),
            "url": getattr(message, "jump_url", "") or "",
            "created_by": str(created_by or ""),
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:  # noqa: BLE001 — bookkeeping only
        print(f"⚠️  Could not record the {kind} poll for the dashboard: {e}")
