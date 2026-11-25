"""backend/routes_guild.py — server list, metadata, overview, settings, notifications, search, user preferences."""

from datetime import timedelta
from zoneinfo import available_timezones

from fastapi import APIRouter, Body, Depends, HTTPException

from shared.access import DANGEROUS_BITS, has_level, level_from_discord
from shared.resources import F, RESOURCES, clean_value, public_spec
from shared import settings as env
from backend import audit, bridge
from backend.auth import refreshed_guilds
from backend.db import coll
from backend.deps import Ctx, current_session, current_user, guild_ctx
from backend.util import now, regex_contains, ser

router = APIRouter(tags=["guild"])

ROLE_KEYS = ["admin_role", "franchise_owner_role", "general_manager_role", "head_coach_role", "assistant_coach_role"]
CHANNEL_KEYS = ["log_channel", "announce_channel", "gametime_channel", "matchmaking_channel", "contract_channel", "awards_channel",
                "league_log_channel", "suggestion_channel", "memberstats_channel", "verify_channel"]
SETTING_FIELDS = (
    [{"key": k, "type": "role"} for k in ROLE_KEYS + ["verify_role"]]
    + [{"key": k, "type": "channel"} for k in CHANNEL_KEYS]
    + [{"key": "activity_enabled", "type": "bool"}, {"key": "activity_channels", "type": "channels"}, {"key": "timezone", "type": "timezone"}]
)


@router.get("/api/meta")
async def meta():
    """Resource specs the frontend renders its tables and forms from (no secrets, no auth needed)."""
    return {"resources": {k: public_spec(v) for k, v in RESOURCES.items()}, "settings": SETTING_FIELDS,
            "client_id": env.DISCORD_CLIENT_ID}


@router.get("/api/guilds")
async def my_guilds(refresh: bool = False, session: dict = Depends(current_session)):
    """Servers the user belongs to, split into: manageable (bot present + access) and 'add the bot' candidates."""
    guilds = await refreshed_guilds(session, force=refresh)
    str_ids = [str(g["id"]) for g in guilds]
    int_ids = [int(g["id"]) for g in str_ids if g.isdigit()]
    query_ids = str_ids + int_ids
    present = {str(s["guild_id"]): s for s in await coll("servers").find({"guild_id": {"$in": query_ids}}, {"guild_id": 1, "member_count": 1}).to_list(None)}
    grants = {str(g["guild_id"]): g["level"] for g in await coll("dashboard_permissions").find({"guild_id": {"$in": query_ids}, "user_id": session["user_id"]}).to_list(None)}
    out = []
    for g in guilds:
        gid = str(g["id"])
        level = level_from_discord(g["owner"], g["permissions"]) or grants.get(gid)
        has_bot = gid in present
        if level:
            out.append({**g, "level": level, "bot_present": has_bot, "member_count": present.get(gid, {}).get("member_count"),
                        "invite_url": None if has_bot else f"https://discord.com/oauth2/authorize?client_id={env.DISCORD_CLIENT_ID}&scope=bot%20applications.commands&permissions=1099784744055&guild_id={gid}"})
    out.sort(key=lambda g: (not g["bot_present"], g["name"].lower()))
    return {"guilds": out}


@router.get("/api/guilds/{gid}/meta")
async def guild_meta(ctx: Ctx = Depends(guild_ctx)):
    g = ctx.guild
    teams = await coll("teams").find({"guild_id": ctx.guild_id}, {"team_id": 1, "name": 1, "competition": 1}).sort("name_lower", 1).to_list(None)
    comps = await coll("competitions").find({"guild_id": ctx.guild_id}, {"name": 1}).to_list(None)
    divs = await coll("divisions").find({"guild_id": ctx.guild_id}, {"name": 1, "competition": 1}).to_list(None)
    return {
        "level": ctx.level, "guild": {"id": ctx.guild_id, "name": g.get("name"), "icon": g.get("icon")},
        "channels": [c for c in g.get("channels", []) if c.get("type") in ("text", "news", "voice", "forum", "stage")],
        "roles": [{k: r.get(k) for k in ("id", "name", "color", "managed", "position")} for r in sorted(g.get("roles", []), key=lambda r: -r.get("position", 0))],
        "teams": [{"id": t["team_id"], "name": t["name"], "competition": t.get("competition")} for t in teams],
        "competitions": sorted({c["name"] for c in comps} | {t.get("competition") for t in teams if t.get("competition")}),
        "divisions": [{"name": d["name"], "competition": d["competition"]} for d in divs],
        "timezones": sorted(available_timezones()),
    }


@router.get("/api/guilds/{gid}/overview")
async def overview(ctx: Ctx = Depends(guild_ctx)):
    gid, g = ctx.guild_id, ctx.guild
    count = lambda c, q: coll(c).count_documents({"guild_id": gid, **q})  # noqa: E731
    recent = await coll("audit_logs").find({"guild_id": gid}).sort("created_at", -1).limit(6).to_list(None) if ctx.level != "viewer" else []
    return {
        "server": {"name": g.get("name"), "icon": g.get("icon"), "owner_id": g.get("owner_id"), "member_count": g.get("member_count"),
                   "boosts": g.get("boosts"), "tier": g.get("tier"), "channels": len(g.get("channels", [])), "roles": len(g.get("roles", [])),
                   "synced_at": ser(g.get("updated_at"))},
        "bot_online": await bridge.bot_online(),
        "counts": {"teams": await count("teams", {}), "matches": await count("matches", {}), "open_polls": await count("polls", {"status": "open"}),
                   "open_giveaways": await count("giveaways", {"status": "open"}), "pending_suggestions": await count("suggestions", {"status": "pending"}),
                   "pending_appeals": await count("appeals", {"status": "pending"}), "players": await count("player_profiles", {})},
        "recent": ser(recent), "level": ctx.level,
    }


@router.post("/api/guilds/{gid}/refresh")
async def refresh_cache(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("staff")
    return await bridge.run_job(ctx.guild_id, "refresh_cache", {}, ctx.user["user_id"])


# ───────────── settings (guild_config, shared with the bot) ─────────────
def _read_settings(cfg: dict) -> dict:
    out = {}
    for f in SETTING_FIELDS:
        v = cfg.get(f["key"])
        out[f["key"]] = [str(x) for x in v] if isinstance(v, list) else (str(v) if v is not None and f["type"] in ("role", "channel") else v)
    return out


@router.get("/api/guilds/{gid}/settings")
async def get_settings(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    return {"settings": _read_settings(cfg)}


@router.put("/api/guilds/{gid}/settings")
async def put_settings(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    roles = {r["id"] for r in ctx.guild.get("roles", [])}
    chans = {c["id"] for c in ctx.guild.get("channels", [])}
    sets, unsets, errors = {}, {}, {}
    for f in SETTING_FIELDS:
        k = f["key"]
        if k not in body:
            continue
        v = body[k]
        try:
            if v in (None, "", []):
                unsets[k] = ""
                continue
            if f["type"] == "role":
                v = clean_value(F(k, "snowflake"), v)
                if v not in roles:
                    raise ValueError("That role isn't in this server")
                if k == "verify_role":
                    cached = next(r for r in ctx.guild["roles"] if r["id"] == v)
                    if cached.get("managed") or int(cached.get("permissions", 0)) & DANGEROUS_BITS:
                        raise ValueError("A verification role can't have moderator/admin permissions")
                sets[k] = int(v)  # the bot stores ids as integers
            elif f["type"] == "channel":
                v = clean_value(F(k, "snowflake"), v)
                if v not in chans:
                    raise ValueError("That channel isn't in this server")
                sets[k] = int(v)
            elif f["type"] == "channels":
                items = clean_value(F(k, "snowflakes", max_items=50), v)
                if any(i not in chans for i in items):
                    raise ValueError("A channel isn't in this server")
                sets[k] = [int(i) for i in items]
            elif f["type"] == "bool":
                sets[k] = clean_value(F(k, "bool"), v)
            elif f["type"] == "timezone":
                if v not in available_timezones():
                    raise ValueError("Unknown timezone")
                sets[k] = v
        except ValueError as e:
            errors[k] = str(e)
    if errors:
        raise HTTPException(422, {"errors": errors})
    op = {}
    if sets:
        op["$set"] = {**sets, "guild_id": ctx.guild_id}
    if unsets:
        op["$unset"] = unsets
    if op:
        await coll("guild_config").update_one({"guild_id": ctx.guild_id}, op, upsert=True)
    await audit.log(ctx, "settings.update", detail={"keys": list({**sets, **unsets})})
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    return {"settings": _read_settings(cfg)}


# ───────────── notifications + search ─────────────
@router.get("/api/guilds/{gid}/notifications")
async def notifications(ctx: Ctx = Depends(guild_ctx)):
    gid, t = ctx.guild_id, now()
    items = []
    n = await coll("suggestions").count_documents({"guild_id": gid, "status": "pending"})
    if n:
        items.append({"kind": "suggestions", "text": f"{n} suggestion(s) waiting for a decision", "page": "suggestions"})
    if ctx.level != "viewer":
        n = await coll("appeals").count_documents({"guild_id": gid, "status": "pending"})
        if n:
            items.append({"kind": "appeals", "text": f"{n} pending appeal(s)", "page": "appeals"})
    n = await coll("giveaways").count_documents({"guild_id": gid, "status": "open", "ends_at": {"$lte": t + timedelta(hours=24)}})
    if n:
        items.append({"kind": "giveaways", "text": f"{n} giveaway(s) ending within 24h", "page": "giveaways"})
    n = await coll("events").count_documents({"guild_id": gid, "starts_at": {"$gte": t, "$lte": t + timedelta(hours=24)}})
    if n:
        items.append({"kind": "events", "text": f"{n} event(s) in the next 24h", "page": "events"})
    if ctx.level in ("manager", "admin"):
        n = await coll("bot_jobs").count_documents({"guild_id": gid, "status": "failed", "created_at": {"$gte": t - timedelta(hours=24)}})
        if n:
            items.append({"kind": "jobs", "text": f"{n} dashboard action(s) failed in the bot in the last 24h", "page": "audit_logs"})
    if not await bridge.bot_online():
        items.insert(0, {"kind": "bot", "text": "The bot looks offline — Discord actions will wait until it is back.", "page": "overview"})
    return {"items": items}


SEARCH = [("teams", "name", "teams", "name"), ("player_profiles", "name", "player_profiles", "name"), ("team_members", "username", "team_members", "username"),
          ("suggestions", "text", "suggestions", "text"), ("tryouts", "player_name", "tryouts", "player_name"), ("hall_of_fame", "name", "hall_of_fame", "name"),
          ("polls", "question", "polls", "question"), ("giveaways", "prize", "giveaways", "prize")]


@router.get("/api/guilds/{gid}/search")
async def search(q: str = "", ctx: Ctx = Depends(guild_ctx)):
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    out = []
    for collection, field, page, label in SEARCH:
        spec = RESOURCES.get(page)
        if not spec or not has_level(ctx.level, spec["read"]):  # never search things the user may not open
            continue
        rows = await coll(collection).find({"guild_id": ctx.guild_id, field: regex_contains(q)}).limit(4).to_list(None)
        out += [{"page": page, "label": str(r.get(label, ""))[:80], "id": str(r["_id"])} for r in rows]
    return {"results": out[:24]}


# ───────────── per-user preferences (themes, personal timezone) ─────────────
@router.get("/api/me/preferences")
async def get_prefs(user: dict = Depends(current_user)):
    return user.get("preferences") or {"accent": "#FF7A1A", "timezone": "UTC"}


@router.put("/api/me/preferences")
async def put_prefs(body: dict = Body(...), user: dict = Depends(current_user)):
    prefs = dict(user.get("preferences") or {})
    if "accent" in body:
        try:
            prefs["accent"] = "#%06X" % clean_value(F("accent", "color"), body["accent"])
        except ValueError as e:
            raise HTTPException(422, {"errors": {"accent": str(e)}})
    if "timezone" in body:
        if body["timezone"] not in available_timezones():
            raise HTTPException(422, {"errors": {"timezone": "Unknown timezone"}})
        prefs["timezone"] = body["timezone"]
    await coll("users").update_one({"user_id": user["user_id"]}, {"$set": {"preferences": prefs}})
    return prefs
