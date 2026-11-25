"""backend/resources.py — generic, spec-driven CRUD for every dashboard resource, plus the per-resource rules.

Every handler: (1) auth via guild_ctx, (2) level check from the spec, (3) validate against the shared spec (unknown
fields are dropped, so a client can never set guild_id/_id), (4) write MongoDB scoped by guild_id, (5) ask the bot to act
in Discord if needed, (6) write an audit-log entry.
"""

import re
from fastapi import APIRouter, Body, Depends, HTTPException

from shared.access import DANGEROUS_BITS
from shared.jobs import JOB_TYPES  # noqa: F401
from shared.records import fmt_pct
from shared.resources import RESOURCES, ValidationError, validate
from shared.security import mask_secret
from shared.timeutil import parse_duration, parse_local_datetime
from database import teams as T
from backend import audit, bridge
from backend.auth import vault
from backend.db import coll, db as handle
from backend.deps import Ctx, guild_ctx
from backend.util import now, oid, regex_contains, ser

router = APIRouter(prefix="/api/guilds/{gid}/r/{res}", tags=["resources"])


# ───────────── helpers ─────────────
def spec_of(res: str) -> dict:
    spec = RESOURCES.get(res)
    if not spec:
        raise HTTPException(404, "Unknown resource")
    return spec


def err(msg: str, code: int = 422):
    raise HTTPException(code, {"errors": {"_": msg}})


async def next_seq(gid: str, name: str) -> int:
    return await T.next_seq(handle(), gid, name)


def channel_ok(ctx: Ctx, channel_id: str | None, field: str = "channel_id") -> None:
    """Channels/roles must belong to THIS server according to the bot's cache — stops cross-server targeting."""
    if channel_id and channel_id not in {c["id"] for c in ctx.guild.get("channels", [])}:
        raise HTTPException(422, {"errors": {field: "That channel isn't in this server (or the bot can't see it)."}})


def role_ok(ctx: Ctx, role_id: str | None, field: str = "role_id") -> None:
    if role_id and role_id not in {r["id"] for r in ctx.guild.get("roles", [])}:
        raise HTTPException(422, {"errors": {field: "That role isn't in this server."}})


async def team_of(ctx: Ctx, team_id) -> dict:
    t = await coll("teams").find_one({"guild_id": ctx.guild_id, "team_id": int(team_id)})
    if not t:
        raise HTTPException(422, {"errors": {"team_id": "That team doesn't exist."}})
    return t


def finish(ctx_result: dict, res: dict) -> dict:
    """Translate a bridge result into the API response (or an error)."""
    if res["ok"] is False:
        raise HTTPException(502, res["error"])
    if res["ok"] is None:
        ctx_result["queued"] = True
        ctx_result["notice"] = "Saved. The bot hasn't answered yet — it will finish this as soon as it is online."
    return ctx_result


async def team_history(ctx: Ctx, team: dict, action: str, before: dict, after: dict, note: str = "") -> None:
    await T.add_history(handle(), ctx.guild_id, team, action, ctx.user["user_id"], before, after, note)


async def bump_team(ctx: Ctx, team: dict, field: str, count: int, action: str, note: str = "") -> dict:
    return await T.bump(handle(), ctx.guild_id, team, field, count, action, ctx.user["user_id"], note)


async def notify_records(ctx: Ctx, team_id: int) -> None:
    """Fire-and-forget: tell the bot to refresh live leaderboards and stat channels."""
    await bridge.run_job(ctx.guild_id, "record_changed", {"team_id": team_id}, ctx.user["user_id"], wait=0)


# ───────────── list enrichment (derived columns) ─────────────
TEAM_NAMED = {"team_members", "player_profiles", "tryouts"}


async def _counts(collection: str, key: str, ctx: Ctx, ids: list) -> dict:
    if not ids:
        return {}
    cur = await coll(collection).aggregate([{"$match": {"guild_id": ctx.guild_id, key: {"$in": ids}}}, {"$group": {"_id": f"${key}", "n": {"$sum": 1}}}])
    return {r["_id"]: r["n"] for r in await cur.to_list(None)}


async def enrich(ctx: Ctx, res: str, items: list) -> None:
    if res == "teams":
        for t in items:
            t["record"] = f"{t['wins']}-{t['losses']}-{t['ties']}"
            t["win_pct"] = fmt_pct(t["wins"], t["losses"], t["ties"])
    ids = {i.get("team_id") for i in items if isinstance(i.get("team_id"), int)} | {i.get(k) for i in items for k in ("home_team_id", "away_team_id") if i.get(k) is not None}
    if ids and (res in TEAM_NAMED or res == "matches"):
        names = {t["team_id"]: t["name"] for t in await coll("teams").find({"guild_id": ctx.guild_id, "team_id": {"$in": list(ids)}}).to_list(None)}
        for i in items:
            if res in TEAM_NAMED:
                i["team_name"] = names.get(i.get("team_id"), "—")
            else:
                i["home_name"], i["away_name"] = names.get(i["home_team_id"], "?"), names.get(i["away_team_id"], "?")
                i["score"] = f"{i['home_score']} – {i['away_score']}"
    if res == "polls":
        c = await _counts("poll_votes", "poll_id", ctx, [i["poll_id"] for i in items])
        for i in items:
            i["voters"] = c.get(i["poll_id"], 0)
    elif res == "giveaways":
        c = await _counts("giveaway_entries", "giveaway_id", ctx, [i["giveaway_id"] for i in items])
        for i in items:
            i["entries"] = c.get(i["giveaway_id"], 0)
    elif res == "role_panels":
        for i in items:
            i["role_count"] = len(i.get("roles", []))
    elif res == "webhooks":
        for i in items:
            url = vault().decrypt(i.get("url_enc", "")) or ""
            i["url_masked"] = mask_secret(url)
    elif res == "stats_channels":
        pass


# ───────────── create hooks ─────────────
async def create_default(ctx: Ctx, spec: dict, data: dict) -> dict:
    doc = {**data, "guild_id": ctx.guild_id, "created_at": now(), "created_by": ctx.user["user_id"]}
    doc["_id"] = (await coll(spec["collection"]).insert_one(doc)).inserted_id
    return {"item": doc}


async def c_teams(ctx, spec, data):
    try:
        doc = await T.create_team(handle(), ctx.guild_id, data["name"], data.get("competition"), data.get("division"), data.get("logo"), ctx.user["user_id"])
    except T.TeamError as e:
        err(str(e))
    await notify_records(ctx, doc["team_id"])
    return {"item": doc}


async def c_team_linked(ctx, spec, data):
    if data.get("team_id") is not None:
        data["team_name"] = (await team_of(ctx, data["team_id"]))["name"]
    return await create_default(ctx, spec, data)


async def c_roster(ctx, spec, data):
    team = await team_of(ctx, data["team_id"])
    try:
        doc = await T.add_roster_member(handle(), ctx.guild_id, team, data["user_id"], data.get("username", ""), data.get("position", ""),
                                        data.get("jersey"), data.get("status", "active"), data.get("notes", ""), ctx.user["user_id"])
    except T.TeamError as e:
        err(str(e))
    return {"item": doc}


async def c_competitions(ctx, spec, data):
    if await coll("competitions").find_one({"guild_id": ctx.guild_id, "name_lower": data["name"].lower()}):
        err("That competition already exists.")
    return await create_default(ctx, spec, {**data, "name_lower": data["name"].lower()})


async def c_divisions(ctx, spec, data):
    comp = await coll("competitions").find_one({"guild_id": ctx.guild_id, "name_lower": data["competition"].lower()})
    if not comp:
        err("Create the competition first.")
    key = {"guild_id": ctx.guild_id, "competition_lower": data["competition"].lower(), "name_lower": data["name"].lower()}
    if await coll("divisions").find_one(key):
        err("That division already exists.")
    return await create_default(ctx, spec, {**data, "competition_lower": key["competition_lower"], "name_lower": key["name_lower"]})


async def c_matches(ctx, spec, data):
    home, away = await team_of(ctx, data["home_team_id"]), await team_of(ctx, data["away_team_id"])
    try:
        doc, updated = await T.log_match(handle(), ctx.guild_id, home, away, data["home_score"], data["away_score"], data.get("competition"),
                                         data["played_at"], data.get("notes", ""), data.get("apply_record", False), ctx.user["user_id"])
    except T.TeamError as e:
        err(str(e))
    for t in updated:
        await notify_records(ctx, t["team_id"])
    return {"item": doc}


def _publish_rollback(spec_collection: str):
    async def rb(ctx, doc):
        await coll(spec_collection).delete_one({"_id": doc["_id"]})
    return rb


async def c_polls(ctx, spec, data):
    channel_ok(ctx, data["channel_id"])
    td = parse_duration(data["duration"])
    created = now()
    doc = {"guild_id": ctx.guild_id, "poll_id": await next_seq(ctx.guild_id, "poll"), "channel_id": data["channel_id"], "message_id": None,
           "question": data["question"], "options": data["options"], "anonymous": data["anonymous"], "multiple": data["multiple"],
           "created_by": ctx.user["user_id"], "created_at": created, "ends_at": created + td, "status": "open"}
    doc["_id"] = (await coll("polls").insert_one(doc)).inserted_id
    r = await bridge.run_job(ctx.guild_id, "publish", {"kind": "poll", "ref": doc["poll_id"]}, ctx.user["user_id"])
    if r["ok"] is False:
        await coll("polls").delete_one({"_id": doc["_id"]})
    return finish({"item": doc}, r)


async def c_giveaways(ctx, spec, data):
    channel_ok(ctx, data["channel_id"])
    role_ok(ctx, data.get("required_role_id"), "required_role_id")
    created = now()
    doc = {"guild_id": ctx.guild_id, "giveaway_id": await next_seq(ctx.guild_id, "giveaway"), "channel_id": data["channel_id"], "message_id": None,
           "prize": data["prize"], "winners_count": data["winners_count"], "created_by": ctx.user["user_id"], "created_at": created,
           "ends_at": created + parse_duration(data["duration"]), "status": "open", "required_role_id": data.get("required_role_id"),
           "min_account_days": data.get("min_account_days") or 0, "winners": []}
    doc["_id"] = (await coll("giveaways").insert_one(doc)).inserted_id
    r = await bridge.run_job(ctx.guild_id, "publish", {"kind": "giveaway", "ref": doc["giveaway_id"]}, ctx.user["user_id"])
    if r["ok"] is False:
        await coll("giveaways").delete_one({"_id": doc["_id"]})
    return finish({"item": doc}, r)


async def c_countdowns(ctx, spec, data):
    channel_ok(ctx, data["channel_id"])
    role_ok(ctx, data.get("role_id"))
    tz = data.get("timezone") or "UTC"
    ends = parse_local_datetime(data["date"], data["time"], tz)
    if ends is None:
        err("Invalid date/time/timezone. Use YYYY-MM-DD, HH:MM (24h) and a name like America/New_York.")
    if ends <= now():
        err("That time has already passed.")
    doc = {"guild_id": ctx.guild_id, "countdown_id": await next_seq(ctx.guild_id, "countdown"), "channel_id": data["channel_id"], "message_id": None,
           "title": data["title"], "ends_at": ends, "timezone": tz, "role_id": data.get("role_id"), "created_by": ctx.user["user_id"], "created_at": now(), "status": "open"}
    doc["_id"] = (await coll("countdowns").insert_one(doc)).inserted_id
    r = await bridge.run_job(ctx.guild_id, "publish", {"kind": "countdown", "ref": doc["countdown_id"]}, ctx.user["user_id"])
    if r["ok"] is False:
        await coll("countdowns").delete_one({"_id": doc["_id"]})
    return finish({"item": doc}, r)


async def c_announcements(ctx, spec, data):
    channel_ok(ctx, data["channel_id"])
    role_ok(ctx, data.get("ping_role_id"), "ping_role_id")
    doc = {**data, "guild_id": ctx.guild_id, "announcement_id": await next_seq(ctx.guild_id, "announcement"), "message_id": None,
           "created_at": now(), "created_by": ctx.user["user_id"], "by": ctx.user["user_id"], "at": now()}
    doc["_id"] = (await coll("announcements").insert_one(doc)).inserted_id
    embed = {k: data.get(k) for k in ("title", "description", "color", "author_name", "image", "thumbnail", "footer", "fields")}
    r = await bridge.run_job(ctx.guild_id, "send_embed", {"channel_id": data["channel_id"], "embed": embed, "ping_role_id": data.get("ping_role_id"),
                                                          "announcement_id": doc["announcement_id"]}, ctx.user["user_id"])
    if r["ok"] is False:
        await coll("announcements").delete_one({"_id": doc["_id"]})
    return finish({"item": doc}, r)


def build_panel_roles(ctx: Ctx, role_ids: list, existing: list | None = None) -> list:
    cache = {r["id"]: r for r in ctx.guild.get("roles", [])}
    old = {r["role_id"]: r for r in (existing or [])}
    out = []
    for rid in role_ids:
        r = cache.get(rid)
        if not r or rid == ctx.guild_id:
            raise HTTPException(422, {"errors": {"role_ids": f"Role {rid} isn't in this server."}})
        if r.get("managed"):
            raise HTTPException(422, {"errors": {"role_ids": f"@{r['name']} is managed by Discord/an integration."}})
        if int(r.get("permissions", 0)) & DANGEROUS_BITS:
            raise HTTPException(422, {"errors": {"role_ids": f"@{r['name']} has moderation/admin permissions and can't be self-assigned."}})
        out.append({"role_id": rid, "name": r["name"], "label": old.get(rid, {}).get("label"), "emoji": old.get(rid, {}).get("emoji")})
    return out


async def c_panels(ctx, spec, data):
    channel_ok(ctx, data["channel_id"])
    roles = build_panel_roles(ctx, data.pop("role_ids"))
    doc = {**data, "guild_id": ctx.guild_id, "panel_id": await next_seq(ctx.guild_id, "rolepanel"), "message_id": None, "roles": roles,
           "created_at": now(), "created_by": ctx.user["user_id"]}
    doc["_id"] = (await coll("role_panels").insert_one(doc)).inserted_id
    r = await bridge.run_job(ctx.guild_id, "publish", {"kind": "role_panel", "ref": doc["panel_id"]}, ctx.user["user_id"])
    if r["ok"] is False:
        await coll("role_panels").delete_one({"_id": doc["_id"]})
    return finish({"item": doc}, r)


async def c_stats(ctx, spec, data):
    t = data["type"]
    team = None
    if t == "team":
        if data.get("team_id") is None:
            err("Pick a team for a team-record statistic.")
        team = await team_of(ctx, data["team_id"])
        key = f"team:{team['team_id']}"
    elif t == "custom":
        if not data.get("label"):
            err("Custom statistics need a name.")
        key = f"custom:{data['label'].strip().lower()}"
    else:
        key = t
    if await coll("stats_channels").find_one({"guild_id": ctx.guild_id, "key": key}):
        err("That statistic already exists.")
    doc = {"guild_id": ctx.guild_id, "key": key, "type": t, "channel_id": None, "template": data.get("template"), "last_name": None}
    if team:
        doc["team_id"] = team["team_id"]
    if t == "custom":
        doc.update(label=data["label"], value=data.get("value") or "")
    doc["_id"] = (await coll("stats_channels").insert_one(doc)).inserted_id
    r = await bridge.run_job(ctx.guild_id, "stats_refresh", {}, ctx.user["user_id"])
    return finish({"item": doc}, r)


async def c_scheduled(ctx, spec, data):
    channel_ok(ctx, data["channel_id"])
    if data["send_at"] <= now() and data.get("repeat", "none") == "none":
        err("Pick a time in the future.")
    return await create_default(ctx, spec, data)


async def c_reminders(ctx, spec, data):
    channel_ok(ctx, data.get("channel_id"))
    return await create_default(ctx, spec, {**data, "status": "pending"})


async def c_workflows(ctx, spec, data):
    channel_ok(ctx, data.get("channel_id"))
    role_ok(ctx, data.get("role_id"))
    return await create_default(ctx, spec, data)


async def c_webhooks(ctx, spec, data):
    if await coll("webhooks").find_one({"guild_id": ctx.guild_id, "name_lower": data["name"].lower()}):
        err("A webhook with that name already exists.")
    if not re.match(r"^https://(discord(app)?\.com)/api/(v\d+/)?webhooks/\d+/[\w-]+$", data["url"]):
        err("That doesn't look like a Discord webhook URL.")
    url = data.pop("url")
    return await create_default(ctx, spec, {**data, "name_lower": data["name"].lower(), "url_enc": vault().encrypt(url)})


async def c_unique_user(ctx, spec, data):
    if await coll("dashboard_permissions").find_one({"guild_id": ctx.guild_id, "user_id": data["user_id"]}):
        err("That user already has dashboard access — edit the existing entry.")
    return await create_default(ctx, spec, data)


async def c_author(ctx, spec, data):
    return await create_default(ctx, spec, {**data, "author_id": ctx.user["user_id"], "author_name": ctx.user["username"],
                                           "scout_id": ctx.user["user_id"], "scout_name": ctx.user["username"]})


async def c_awards(ctx, spec, data):
    if await coll("awards").find_one({"guild_id": ctx.guild_id, "league_id": "", "user_id": data["user_id"], "award_name": data["award_name"], "season": data["season"]}):
        err("They already have that award for that season.")
    # same shape as the bot's /award so /awardcheck shows it
    doc = {**data, "guild_id": ctx.guild_id, "league_id": "", "given_by": ctx.user["user_id"], "given_at": now(), "created_at": now()}
    doc["_id"] = (await coll("awards").insert_one(doc)).inserted_id
    return {"item": doc}


CREATE = {"teams": c_teams, "team_members": c_roster, "player_profiles": c_team_linked, "tryouts": c_team_linked,
          "competitions": c_competitions, "divisions": c_divisions, "matches": c_matches, "polls": c_polls, "giveaways": c_giveaways,
          "countdowns": c_countdowns, "announcements": c_announcements, "role_panels": c_panels, "stats_channels": c_stats,
          "scheduled_messages": c_scheduled, "reminders": c_reminders, "workflows": c_workflows, "webhooks": c_webhooks,
          "dashboard_permissions": c_unique_user, "staff_reports": c_author, "scouting_reports": c_author, "awards": c_awards}


# ───────────── update hooks (return extra $set fields; may run follow-ups) ─────────────
async def u_teams(ctx, old, data):
    extra = {}
    if "name" in data and data["name"].lower() != old["name_lower"]:
        if await coll("teams").find_one({"guild_id": ctx.guild_id, "name_lower": data["name"].lower(), "team_id": {"$ne": old["team_id"]}}):
            err("Another team already has that name.")
        extra["name_lower"] = data["name"].lower()
        for c in TEAM_NAMED:  # keep denormalised names in sync
            await coll(c).update_many({"guild_id": ctx.guild_id, "team_id": old["team_id"]}, {"$set": {"team_name": data["name"]}})
    if "competition" in data:
        extra["competition_lower"] = (data["competition"] or "General").lower()
    return extra


async def after_teams(ctx, new):
    await notify_records(ctx, new["team_id"])


async def u_team_linked(ctx, old, data):
    return {"team_name": (await team_of(ctx, data["team_id"]))["name"]} if data.get("team_id") is not None else {}


async def u_panels(ctx, old, data):
    extra = {}
    if "role_ids" in data:
        extra["roles"] = build_panel_roles(ctx, data.pop("role_ids"), old.get("roles"))
    return extra


async def after_panels(ctx, new):
    return await bridge.run_job(ctx.guild_id, "publish", {"kind": "role_panel", "ref": new["panel_id"]}, ctx.user["user_id"])


async def after_stats(ctx, new):
    return await bridge.run_job(ctx.guild_id, "stats_refresh", {}, ctx.user["user_id"])


async def u_channels(ctx, old, data):
    channel_ok(ctx, data.get("channel_id"))
    role_ok(ctx, data.get("role_id"))
    return {}


UPDATE = {"teams": (u_teams, after_teams), "team_members": (u_team_linked, None), "player_profiles": (u_team_linked, None),
          "tryouts": (u_team_linked, None), "role_panels": (u_panels, after_panels), "stats_channels": (None, after_stats),
          "scheduled_messages": (u_channels, None), "workflows": (u_channels, None), "reminders": (u_channels, None)}


# ───────────── delete hooks ─────────────
async def d_teams(ctx, doc):
    gid = ctx.guild_id
    await T.delete_team(handle(), gid, doc)
    return await bridge.run_job(gid, "stats_refresh", {}, ctx.user["user_id"], wait=0)  # bot removes the now-invalid channel


async def d_polls(ctx, doc):
    if doc["status"] == "open":
        err("End the poll first.", 409)
    await coll("poll_votes").delete_many({"guild_id": ctx.guild_id, "poll_id": doc["poll_id"]})


async def d_giveaways(ctx, doc):
    if doc["status"] == "open":
        err("End the giveaway first.", 409)
    await coll("giveaway_entries").delete_many({"guild_id": ctx.guild_id, "giveaway_id": doc["giveaway_id"]})


async def d_countdowns(ctx, doc):
    r = await bridge.run_job(ctx.guild_id, "countdown_delete", {"countdown_id": doc["countdown_id"]}, ctx.user["user_id"])
    return r  # message removal is best-effort; the record is deleted either way


async def d_panels(ctx, doc):
    return await bridge.run_job(ctx.guild_id, "publish", {"kind": "role_panel_delete", "ref": doc["panel_id"]}, ctx.user["user_id"])


async def d_stats(ctx, doc):
    return await bridge.run_job(ctx.guild_id, "stats_remove", {"key": doc["key"]}, ctx.user["user_id"])


async def d_competitions(ctx, doc):
    if await coll("teams").find_one({"guild_id": ctx.guild_id, "competition_lower": doc["name_lower"]}):
        err("Teams still belong to this competition — move them first.", 409)
    await coll("divisions").delete_many({"guild_id": ctx.guild_id, "competition_lower": doc["name_lower"]})


async def d_divisions(ctx, doc):
    if await coll("teams").find_one({"guild_id": ctx.guild_id, "competition_lower": doc["competition_lower"], "division": doc["name"]}):
        err("Teams still use this division.", 409)


DELETE = {"teams": d_teams, "polls": d_polls, "giveaways": d_giveaways, "countdowns": d_countdowns, "role_panels": d_panels,
          "stats_channels": d_stats, "competitions": d_competitions, "divisions": d_divisions}


# ───────────── actions ─────────────
async def a_poll_end(ctx, doc, p):
    return await bridge.run_job(ctx.guild_id, "poll_close", {"poll_id": doc["poll_id"]}, ctx.user["user_id"])


async def a_giveaway_end(ctx, doc, p):
    return await bridge.run_job(ctx.guild_id, "giveaway_end", {"giveaway_id": doc["giveaway_id"]}, ctx.user["user_id"])


async def a_giveaway_reroll(ctx, doc, p):
    return await bridge.run_job(ctx.guild_id, "giveaway_reroll", {"giveaway_id": doc["giveaway_id"], "count": p.get("count") or 1}, ctx.user["user_id"])


async def a_suggestion(ctx, doc, p, status):
    return await bridge.run_job(ctx.guild_id, "suggestion_decide", {"suggestion_id": doc["suggestion_id"], "status": status,
                                                                    "response": p.get("response"), "by": ctx.user["username"], "by_id": ctx.user["user_id"]}, ctx.user["user_id"])


async def a_appeal(ctx, doc, p, status):
    await coll("appeals").update_one({"_id": doc["_id"]}, {"$set": {"status": status, "response": p.get("response"), "decided_by": ctx.user["user_id"], "decided_at": now()}})
    if status == "approved" and p.get("unban"):
        return await bridge.run_job(ctx.guild_id, "unban", {"user_id": doc["user_id"], "reason": f"Appeal approved by {ctx.user['username']}"}, ctx.user["user_id"])
    return {"ok": True, "result": {}}


async def a_webhook_send(ctx, doc, p):
    import httpx
    url = vault().decrypt(doc.get("url_enc", ""))
    if not url:
        raise HTTPException(500, "Stored webhook can't be decrypted (DATA_ENCRYPTION_KEY changed?).")
    body = {"content": p["content"], "allowed_mentions": {"parse": []}}  # no @everyone/role pings from a dashboard webhook
    if doc.get("username"):
        body["username"] = doc["username"]
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json=body)
    if r.status_code >= 300:
        raise HTTPException(502, f"Discord rejected the webhook message ({r.status_code}).")
    return {"ok": True, "result": {}}


ACTIONS = {("polls", "end"): a_poll_end, ("giveaways", "end"): a_giveaway_end, ("giveaways", "reroll"): a_giveaway_reroll,
           ("suggestions", "approve"): lambda c, d, p: a_suggestion(c, d, p, "approved"),
           ("suggestions", "deny"): lambda c, d, p: a_suggestion(c, d, p, "denied"),
           ("appeals", "approve"): lambda c, d, p: a_appeal(c, d, p, "approved"),
           ("appeals", "deny"): lambda c, d, p: a_appeal(c, d, p, "denied"),
           ("webhooks", "send"): a_webhook_send}


# ───────────── routes ─────────────
@router.get("")
async def list_items(res: str, q: str = "", page: int = 1, per_page: int = 25, status: str | None = None, team_id: int | None = None,
                     competition: str | None = None, ctx: Ctx = Depends(guild_ctx)):
    spec = spec_of(res)
    ctx.require(spec["read"])
    per_page = max(1, min(per_page, 100))
    query: dict = {"guild_id": ctx.guild_id}
    if status:
        query["status"] = status
    if team_id is not None:
        query["team_id"] = team_id
    if competition:
        query["competition_lower"] = competition.lower()
    if q and spec["search"]:
        query["$or"] = [{f: regex_contains(q)} for f in spec["search"]]
    c = coll(spec["collection"])
    total = await c.count_documents(query)
    cur = c.find(query).sort(spec["sort"][0], spec["sort"][1]).skip((max(page, 1) - 1) * per_page).limit(per_page)
    items = await cur.to_list(length=per_page)
    await enrich(ctx, res, items)
    return {"items": [ser(i) for i in items], "total": total, "page": page, "per_page": per_page}


@router.post("")
async def create_item(res: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    spec = spec_of(res)
    if not spec["create"]:
        raise HTTPException(405, "Items of this type can't be created here.")
    ctx.require(spec["write"])
    try:
        data = validate(spec, body)
    except ValidationError as e:
        raise HTTPException(422, {"errors": e.errors})
    out = await CREATE.get(res, create_default)(ctx, spec, data)
    out["item"] = ser(out["item"])
    await audit.log(ctx, f"{res}.create", out["item"].get("name") or out["item"].get("title") or out["item"].get("id", ""))
    return out


@router.patch("/{rid}")
async def update_item(res: str, rid: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    spec = spec_of(res)
    if not spec["update"]:
        raise HTTPException(405, "Items of this type can't be edited.")
    ctx.require(spec["write"])
    c = coll(spec["collection"])
    old = await c.find_one({"_id": oid(rid), "guild_id": ctx.guild_id})
    if not old:
        raise HTTPException(404, "Not found")
    try:
        data = validate(spec, body, partial=True, creating=False)
    except ValidationError as e:
        raise HTTPException(422, {"errors": e.errors})
    pre, post = UPDATE.get(res, (None, None))
    extra = await pre(ctx, old, data) if pre else {}
    if "url" in data:  # webhooks never accept a plain URL update through the generic path
        data.pop("url")
    if not data and not extra:
        raise HTTPException(422, {"errors": {"_": "Nothing to change."}})
    await c.update_one({"_id": old["_id"]}, {"$set": {**data, **extra, "updated_at": now(), "updated_by": ctx.user["user_id"]}})
    new = await c.find_one({"_id": old["_id"]})
    out = {"item": ser(new)}
    if post:
        r = await post(ctx, new)
        if isinstance(r, dict) and "ok" in r:
            finish(out, r)
    await audit.log(ctx, f"{res}.update", rid, {"fields": list(data)})
    return out


@router.delete("/{rid}")
async def delete_item(res: str, rid: str, ctx: Ctx = Depends(guild_ctx)):
    spec = spec_of(res)
    ctx.require(spec["delete"])
    c = coll(spec["collection"])
    doc = await c.find_one({"_id": oid(rid), "guild_id": ctx.guild_id})
    if not doc:
        raise HTTPException(404, "Not found")
    out = {"ok": True}
    hook = DELETE.get(res)
    r = await hook(ctx, doc) if hook else None
    await c.delete_one({"_id": doc["_id"]})
    if isinstance(r, dict) and r.get("ok") is False:
        out["notice"] = f"Deleted here, but Discord cleanup failed: {r['error']}"
    await audit.log(ctx, f"{res}.delete", doc.get("name") or doc.get("title") or rid)
    return out


@router.post("/{rid}/actions/{name}")
async def run_action(res: str, rid: str, name: str, body: dict = Body(default={}), ctx: Ctx = Depends(guild_ctx)):
    spec = spec_of(res)
    act = next((a for a in spec["actions"] if a["name"] == name), None)
    fn = ACTIONS.get((res, name))
    if not act or not fn:
        raise HTTPException(404, "Unknown action")
    ctx.require(act.get("level") or spec["write"])
    doc = await coll(spec["collection"]).find_one({"_id": oid(rid), "guild_id": ctx.guild_id})
    if not doc:
        raise HTTPException(404, "Not found")
    for k, v in (act.get("when") or {}).items():
        if doc.get(k) != v:
            raise HTTPException(409, f"This isn't possible while {k} is '{doc.get(k)}'.")
    try:
        params = validate({"fields": act.get("params", [])}, body)
    except ValidationError as e:
        raise HTTPException(422, {"errors": e.errors})
    r = await fn(ctx, doc, params)
    await audit.log(ctx, f"{res}.{name}", rid)
    return finish({"ok": True}, r)
