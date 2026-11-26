"""backend/routes_schedule.py — create gametime and multi-time polls from the dashboard.

The Discord message is built and posted by the bot (it holds the token): the API validates the request, writes a job
(`gametime_create` / `times_create`), and waits briefly for the jump link. Both land in the server's configured
gametime channel, exactly like `/gametime` and `/times`, and the bot records what it posted in `schedule_polls` so the
page can list recent polls.
"""

from fastapi import APIRouter, Body, Depends

from backend import audit, bridge
from backend.db import coll
from backend.deps import Ctx, guild_ctx
from backend.resources import err, finish
from backend.util import ser

router = APIRouter(prefix="/api/guilds/{gid}/schedule", tags=["schedule"])

MAX_OPTIONS = 6


def _label(value, limit: int = 80) -> str:
    """One line, no double spaces — the time is embedded in the poll text, so it can't carry newlines."""
    return " ".join(str(value or "").split())[:limit]


def _role(ctx: Ctx, role_id) -> dict:
    rid = str(role_id or "").strip()
    role = next((r for r in ctx.guild.get("roles", []) if str(r.get("id")) == rid), None)
    if role is None or rid == ctx.guild_id:
        err("Pick a role to ping — @everyone cannot be used.")
    return role


def _out(kind: str, res: dict, extra: dict) -> dict:
    out = {"ok": True, "kind": kind, **extra}
    if res["ok"]:
        out["url"] = (res.get("result") or {}).get("message")
        out["channel"] = (res.get("result") or {}).get("channel")
    return finish(out, res)


@router.get("")
async def schedule_overview(ctx: Ctx = Depends(guild_ctx)):
    """The gametime channel this server posts to, plus what was posted recently."""
    ctx.require("staff")
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    cid = str(cfg.get("gametime_channel") or "")
    channel = next((c for c in ctx.guild.get("channels", []) if str(c.get("id")) == cid), None)
    items = await coll("schedule_polls").find({"guild_id": ctx.guild_id}).sort("created_at", -1).limit(25).to_list(None)
    return {
        "channel": {"id": cid, "name": channel.get("name")} if channel else None,
        "managed": ctx.level,
        "items": [{
            "id": str(i["_id"]), "kind": i.get("kind"), "league": i.get("league"), "times": i.get("times") or [],
            "role_name": i.get("role_name"), "url": i.get("url"), "created_at": ser(i.get("created_at")),
        } for i in items],
    }


@router.post("/gametime")
async def create_gametime(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("staff")
    league, time = _label(body.get("league")), _label(body.get("time"))
    if not league:
        err("Which league is this gametime for?")
    if not time:
        err("Give the gametime a time, e.g. “Friday 8 PM EST”.")
    role = _role(ctx, body.get("role_id"))
    dm = bool(body.get("dm"))
    res = await bridge.run_job(ctx.guild_id, "gametime_create",
                               {"league": league, "time": time, "role_id": str(role["id"]), "dm": dm},
                               ctx.user["user_id"])
    await audit.log(ctx, "schedule.gametime", league, {"time": time, "role_id": str(role["id"]), "dm": dm})
    return _out("gametime", res, {"league": league, "time": time})


@router.post("/times")
async def create_times(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    league = _label(body.get("league"))
    if not league:
        err("Which league are these times for?")
    raw = body.get("times")
    if not isinstance(raw, list):
        err("Add at least two time options.")
    options: list[str] = []
    for t in (_label(x) for x in raw):
        if t and t not in options:
            options.append(t)
    if len(options) < 2:
        err("Add at least two different times.")
    if len(options) > MAX_OPTIONS:
        err(f"At most {MAX_OPTIONS} time options.")
    role = _role(ctx, body.get("role_id"))
    res = await bridge.run_job(ctx.guild_id, "times_create",
                               {"league": league, "times": options, "role_id": str(role["id"])},
                               ctx.user["user_id"])
    await audit.log(ctx, "schedule.times", league, {"times": options, "role_id": str(role["id"])})
    return _out("times", res, {"league": league, "times": options})
