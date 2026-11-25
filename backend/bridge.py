"""backend/bridge.py — run actions in Discord *through the bot* (the backend never has the bot token)."""

import asyncio
from datetime import timedelta

from shared import settings
from shared.jobs import DONE, FAILED, new_job
from backend.db import coll
from backend.util import now


async def bot_online() -> bool:
    """The bot writes a heartbeat once a minute (see bot/cogs/dashboard_bridge.py)."""
    doc = await coll("servers").find_one({"guild_id": "_bot"}, {"heartbeat": 1})
    return bool(doc and doc.get("heartbeat") and now() - doc["heartbeat"] < timedelta(minutes=3))


async def run_job(guild_id: str, jtype: str, payload: dict, user_id: str, wait: float | None = None) -> dict:
    """Queue a job and wait briefly for the bot's answer.
    Returns {"ok": True, "result": …} | {"ok": False, "error": …} | {"ok": None, "queued": True} (bot slow/offline)."""
    job = new_job(guild_id, jtype, payload, user_id)
    jid = (await coll("bot_jobs").insert_one(job)).inserted_id
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (settings.JOB_WAIT_SECONDS if wait is None else wait)
    delay = 0.3
    while loop.time() < deadline:
        await asyncio.sleep(delay)
        delay = min(delay * 1.4, 1.2)  # back off: few reads on the free tier even when the bot is slow
        doc = await coll("bot_jobs").find_one({"_id": jid}, {"status": 1, "result": 1, "error": 1})
        if doc and doc["status"] == DONE:
            return {"ok": True, "result": doc.get("result") or {}}
        if doc and doc["status"] == FAILED:
            return {"ok": False, "error": doc.get("error") or "The bot could not complete that action."}
    return {"ok": None, "queued": True, "job_id": str(jid)}
