"""shared/jobs.py — the website→bot contract.

The backend never holds the bot token. When a dashboard action needs Discord (post a message, create a channel…),
the backend writes a job to the `bot_jobs` collection; the bot (which holds the token) claims and runs it, then writes
the result back. The backend waits briefly for that result so the browser gets a real answer.
"""

from datetime import datetime, timedelta, timezone

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"

# type -> required payload keys. Anything else is rejected, so the website can't ask the bot to do arbitrary things.
JOB_TYPES = {
    "refresh_cache": [],
    "publish": ["kind", "ref"],                 # post/refresh the Discord message for a poll/giveaway/countdown/role panel/verification
    "poll_close": ["poll_id"],
    "giveaway_end": ["giveaway_id"],
    "giveaway_reroll": ["giveaway_id"],
    "countdown_delete": ["countdown_id"],
    "suggestion_decide": ["suggestion_id", "status"],
    "send_embed": ["channel_id"],
    "record_changed": ["team_id"],
    "stats_refresh": [],
    "stats_remove": ["key"],
    "channel_lock": ["channel_id", "locked"],
    "backup_create": ["name"],
    "backup_restore": ["name", "mode"],
    "unban": ["user_id"],
    "members_sync": [],                       # publish a compact member roster for the dashboard directory
    "member_roles": ["user_id", "add", "remove"],  # add/remove Discord roles for one member
}


def new_job(guild_id: str, jtype: str, payload: dict, user_id: str) -> dict:
    if jtype not in JOB_TYPES:
        raise ValueError(f"unknown job type {jtype}")
    missing = [k for k in JOB_TYPES[jtype] if k not in payload]
    if missing:
        raise ValueError(f"job {jtype} missing {missing}")
    now = datetime.now(timezone.utc)
    return {
        "guild_id": str(guild_id), "type": jtype, "payload": payload, "status": PENDING, "result": None, "error": None,
        "created_by": str(user_id), "created_at": now,
        "expires_at": now + timedelta(days=1),   # TTL index removes old jobs so the free tier doesn't fill up
    }
