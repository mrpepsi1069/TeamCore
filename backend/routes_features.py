"""backend/routes_features.py — records, leaderboard, activity, member stats, backups, locks, verification, exports."""

import csv
import io
import json
import zipfile
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from shared.records import fmt_pct, sort_teams
from shared.resources import F, RESOURCES, clean_value
from backend import audit, bridge
from backend.db import coll
from backend.deps import Ctx, guild_ctx
from database import teams as T
from backend.db import db as handle
from backend.resources import bump_team, channel_ok, finish, notify_records, role_ok
from backend.util import now, ser

router = APIRouter(prefix="/api/guilds/{gid}", tags=["features"])


# ───────────── team records ─────────────
@router.post("/teams/{team_id}/record")
async def change_record(team_id: int, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """action: win | loss | tie (+count) · set (wins/losses/ties) · reset. Every change writes record history."""
    ctx.require("staff")
    team = await coll("teams").find_one({"guild_id": ctx.guild_id, "team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    action = body.get("action")
    try:
        count = max(1, min(int(body.get("count", 1)), 50))
    except (TypeError, ValueError):
        raise HTTPException(422, "count must be a number")
    if action in ("win", "loss", "tie"):
        field = {"win": "wins", "loss": "losses", "tie": "ties"}[action]
        new = await bump_team(ctx, team, field, count, f"+{count} {action}", body.get("note", "")[:200])
    elif action in ("set", "reset"):
        vals = {"wins": 0, "losses": 0, "ties": 0} if action == "reset" else {}
        if action == "set":
            for k in ("wins", "losses", "ties"):
                try:
                    vals[k] = int(body[k])
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(422, f"{k} must be a number")
                if not 0 <= vals[k] <= 9999:
                    raise HTTPException(422, f"{k} must be 0–9999")
        new = await T.set_record(handle(), ctx.guild_id, team, vals["wins"], vals["losses"], vals["ties"], action, ctx.user["user_id"], body.get("note", "")[:200])
    else:
        raise HTTPException(422, "Unknown action")
    await notify_records(ctx, team_id)
    await audit.log(ctx, f"record.{action}", team["name"])
    new["record"], new["win_pct"] = f"{new['wins']}-{new['losses']}-{new['ties']}", fmt_pct(new["wins"], new["losses"], new["ties"])
    return {"team": ser(new)}


ROSTER_ROW = [F("user_id", "snowflake", "Discord ID", required=True), F("username", max=80), F("position", max=40),
              F("jersey", "int", min=0, max=999), F("status", "select", choices=list(T.ROSTER_STATUSES), default="active")]


@router.post("/teams/{team_id}/roster")
async def add_roster(team_id: int, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Build a whole roster in one go (Rosters → New roster). The batch is all-or-nothing: one bad row adds nobody."""
    ctx.require("staff")
    team = await coll("teams").find_one({"guild_id": ctx.guild_id, "team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    rows = body.get("players")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(422, {"errors": {"players": "Add at least one player."}})
    if len(rows) > T.ROSTER_MAX:
        raise HTTPException(422, {"errors": {"players": f"A roster holds up to {T.ROSTER_MAX} players."}})
    players = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise HTTPException(422, {"errors": {"players": f"Row {i + 1} is invalid."}})
        try:
            players.append({f["name"]: clean_value(f, row.get(f["name"])) for f in ROSTER_ROW})
        except ValueError as e:
            raise HTTPException(422, {"errors": {"players": f"Row {i + 1}: {e}"}})
    try:
        docs = await T.add_roster_batch(handle(), ctx.guild_id, team, players, ctx.user["user_id"])
    except T.TeamError as e:
        raise HTTPException(422, {"errors": {"players": str(e)}})
    await audit.log(ctx, "roster.create", f"{team['name']} ({len(docs)} players)")
    return {"items": [ser(d) for d in docs], "added": len(docs)}


@router.get("/teams/{team_id}/history")
async def record_history(team_id: int, ctx: Ctx = Depends(guild_ctx)):
    rows = await coll("team_history").find({"guild_id": ctx.guild_id, "team_id": team_id}).sort("timestamp", -1).limit(50).to_list(None)
    return {"items": ser(rows)}


@router.get("/leaderboard")
async def leaderboard(sort: str = "wins", competition: str | None = None, division: str | None = None, ctx: Ctx = Depends(guild_ctx)):
    q = {"guild_id": ctx.guild_id}
    if competition:
        q["competition_lower"] = competition.lower()
    if division:
        q["division"] = division
    teams = sort_teams(await coll("teams").find(q).to_list(None), sort if sort in ("wins", "percentage", "losses") else "wins")
    rows = [{"rank": i + 1, "team_id": t["team_id"], "name": t["name"], "logo": t.get("logo"), "competition": t.get("competition"),
             "division": t.get("division"), "wins": t["wins"], "losses": t["losses"], "ties": t["ties"], "pct": fmt_pct(t["wins"], t["losses"], t["ties"])}
            for i, t in enumerate(teams)]
    return {"items": rows}


# ───────────── activity / member stats / poll archive ─────────────
def _day(d) -> str:
    return d.strftime("%Y-%m-%d")


@router.get("/activity")
async def activity(period: str = "week", ctx: Ctx = Depends(guild_ctx)):
    match = {"guild_id": ctx.guild_id}
    if period in ("day", "week"):
        match["date"] = {"$gte": _day(now() - timedelta(days=0 if period == "day" else 6))}
    top = await (await coll("activity_daily").aggregate([
        {"$match": match}, {"$group": {"_id": "$user_id", "count": {"$sum": "$count"}, "name": {"$max": "$user_name"}}},
        {"$sort": {"count": -1}}, {"$limit": 25}])).to_list(None)
    daily = await (await coll("activity_daily").aggregate([
        {"$match": {"guild_id": ctx.guild_id, "date": {"$gte": _day(now() - timedelta(days=13))}}},
        {"$group": {"_id": "$date", "count": {"$sum": "$count"}}}, {"$sort": {"_id": 1}}])).to_list(None)
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    return {"enabled": bool(cfg.get("activity_enabled")), "top": [{"user_id": r["_id"], "name": r.get("name") or r["_id"], "count": r["count"]} for r in top],
            "daily": [{"date": r["_id"], "count": r["count"]} for r in daily]}


@router.get("/memberstats")
async def memberstats(days: int = 30, ctx: Ctx = Depends(guild_ctx)):
    days = max(3, min(days, 90))
    start = _day(now() - timedelta(days=days - 1))
    snaps = await coll("member_snapshots").find({"guild_id": ctx.guild_id, "date": {"$gte": start}}).sort("date", 1).to_list(None)
    daily = await coll("member_daily").find({"guild_id": ctx.guild_id, "date": {"$gte": start}}).sort("date", 1).to_list(None)
    return {"snapshots": [{"date": s["date"], "total": s["total"], "humans": s.get("humans"), "bots": s.get("bots")} for s in snaps],
            "daily": [{"date": d["date"], "joined": d.get("joined", 0), "left": d.get("left", 0)} for d in daily],
            "joined": sum(d.get("joined", 0) for d in daily), "left": sum(d.get("left", 0) for d in daily)}


@router.get("/poll-archive")
async def poll_archive(page: int = 1, ctx: Ctx = Depends(guild_ctx)):
    per = 10
    q = {"guild_id": ctx.guild_id, "status": "closed"}
    polls = await coll("polls").find(q).sort("poll_id", -1).skip((max(page, 1) - 1) * per).limit(per).to_list(None)
    ids = [p["poll_id"] for p in polls]
    votes = await coll("poll_votes").find({"guild_id": ctx.guild_id, "poll_id": {"$in": ids}}).to_list(None) if ids else []
    out = []
    for p in polls:
        counts = [0] * len(p["options"])
        mine = [v for v in votes if v["poll_id"] == p["poll_id"]]
        for v in mine:
            for i in v.get("choices", []):
                if 0 <= i < len(counts):
                    counts[i] += 1
        out.append({"id": str(p["_id"]), "poll_id": p["poll_id"], "question": p["question"], "closed_at": ser(p.get("closed_at") or p.get("ends_at")),
                    "voters": len(mine), "anonymous": p["anonymous"], "results": [{"option": o, "votes": counts[i]} for i, o in enumerate(p["options"])]})
    return {"items": out, "total": await coll("polls").count_documents(q)}


# ───────────── moderation: channel locks ─────────────
@router.get("/locks")
async def locks(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("staff")
    return {"items": ser(await coll("channel_locks").find({"guild_id": ctx.guild_id}).to_list(None))}


@router.post("/channels/{channel_id}/lock")
async def lock_channel(channel_id: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    channel_ok(ctx, channel_id, "channel")
    locked = bool(body.get("locked"))
    r = await bridge.run_job(ctx.guild_id, "channel_lock", {"channel_id": channel_id, "locked": locked, "reason": str(body.get("reason") or "Dashboard")[:200],
                                                            "by": ctx.user["username"]}, ctx.user["user_id"])
    await audit.log(ctx, "channel.lock" if locked else "channel.unlock", channel_id)
    return finish({"ok": True}, r)


# ───────────── backups ─────────────
@router.get("/backups")
async def backups(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    rows = await coll("backups").find({"guild_id": ctx.guild_id}, {"data": 0}).sort("created_at", -1).to_list(None)
    return {"items": ser(rows)}


@router.post("/backups")
async def backup_create(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    name = str(body.get("name", "")).strip()[:50]
    if not name:
        raise HTTPException(422, {"errors": {"name": "Required"}})
    if await coll("backups").find_one({"guild_id": ctx.guild_id, "name_lower": name.lower()}):
        raise HTTPException(422, {"errors": {"name": "A backup with that name exists."}})
    r = await bridge.run_job(ctx.guild_id, "backup_create", {"name": name, "by": ctx.user["user_id"]}, ctx.user["user_id"], wait=25)
    await audit.log(ctx, "backup.create", name)
    return finish({"ok": True}, r)


@router.post("/backups/{name}/restore")
async def backup_restore(name: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    mode = body.get("mode")
    if mode not in ("missing", "full"):
        raise HTTPException(422, "mode must be 'missing' or 'full'")
    if mode == "full" and body.get("confirm") != "RESTORE":
        raise HTTPException(422, {"errors": {"confirm": "Type RESTORE to confirm a full (destructive) restore."}})
    if mode == "full" and ctx.user["user_id"] != ctx.guild.get("owner_id"):
        raise HTTPException(403, "Only the server owner can run a full restore from the dashboard.")
    if not await coll("backups").find_one({"guild_id": ctx.guild_id, "name_lower": name.lower()}, {"_id": 1}):
        raise HTTPException(404, "Backup not found")
    r = await bridge.run_job(ctx.guild_id, "backup_restore", {"name": name, "mode": mode, "by": ctx.user["user_id"]}, ctx.user["user_id"], wait=60)
    await audit.log(ctx, f"backup.restore.{mode}", name)
    return finish({"ok": True, "result": r.get("result")}, r)


@router.delete("/backups/{name}")
async def backup_delete(name: str, ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    res = await coll("backups").delete_one({"guild_id": ctx.guild_id, "name_lower": name.lower()})
    if not res.deleted_count:
        raise HTTPException(404, "Backup not found")
    await audit.log(ctx, "backup.delete", name)
    return {"ok": True}


# ───────────── verification (a button message that gives a role) ─────────────
@router.post("/verification/publish")
async def verification_publish(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    if not cfg.get("verify_role") or not cfg.get("verify_channel"):
        raise HTTPException(422, "Set a verification role and channel in Settings first.")
    channel_ok(ctx, str(cfg["verify_channel"]), "verify_channel")
    role_ok(ctx, str(cfg["verify_role"]), "verify_role")
    r = await bridge.run_job(ctx.guild_id, "publish", {"kind": "verification", "ref": 1}, ctx.user["user_id"])
    await audit.log(ctx, "verification.publish")
    return finish({"ok": True}, r)


# ───────────── data export ─────────────
EXPORTABLE = sorted({s["collection"] for s in RESOURCES.values() if s["collection"] not in ("webhooks",)} | {"team_history", "poll_votes", "giveaway_entries", "member_snapshots", "member_daily"})
SECRET_FIELDS = {"url_enc", "token_hash", "access_token_enc"}


@router.get("/export")
async def export(collections: str = "", fmt: str = "json", ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    wanted = [c for c in collections.split(",") if c] or EXPORTABLE
    bad = [c for c in wanted if c not in EXPORTABLE]
    if bad:
        raise HTTPException(422, f"Not exportable: {', '.join(bad)}")
    data = {}
    for c in wanted:
        rows = await coll(c).find({"guild_id": ctx.guild_id}).limit(20000).to_list(None)  # bounded: free-tier friendly
        data[c] = [{k: v for k, v in ser(r).items() if k not in SECRET_FIELDS} for r in rows]
    await audit.log(ctx, "export", ",".join(wanted), {"format": fmt})
    stamp = now().strftime("%Y%m%d-%H%M")
    if fmt == "csv":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for c, rows in data.items():
                cols = sorted({k for r in rows for k in r})
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
                z.writestr(f"{c}.csv", out.getvalue())
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="teamcore-{ctx.guild_id}-{stamp}.zip"'})
    return Response(json.dumps(data, indent=1), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="teamcore-{ctx.guild_id}-{stamp}.json"'})


@router.get("/export/options")
async def export_options(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    return {"collections": EXPORTABLE}
