"""backend/routes_members.py — the member directory.

One page that answers "who is in this server, what can they do here, and which team are they on?".

Member data is published by the bot on demand (job `members_sync`) so the backend still never holds the bot token.
Everything the directory changes is written here first — dashboard_permissions and team_members update immediately —
and only Discord role changes are handed to the bot as a job.
"""

from fastapi import APIRouter, Body, Depends, HTTPException

from shared.economy import STARTING_CASH, format_cash
from shared.resources import F, clean_value
from database.economy import balances as cash_balances
from backend import audit, bridge
from backend.db import coll, db as db_handle
from backend.deps import Ctx, guild_ctx
from backend.resources import err, finish, team_of
from backend.util import now, ser

router = APIRouter(prefix="/api/guilds/{gid}/members", tags=["members"])

GRANT_LEVELS = ("viewer", "staff", "manager")
ROSTER_STATUSES = ("active", "reserve", "injured", "inactive")


def _row(user_id: str, base: dict, grant: dict, roster: list, owner_id: str) -> dict:
    return {
        "user_id": user_id,
        "username": base.get("username") or grant.get("username") or user_id,
        "display_name": base.get("display_name") or base.get("username") or grant.get("username") or user_id,
        "avatar": base.get("avatar"),
        "joined_at": ser(base.get("joined_at")),
        "role_ids": [str(r) for r in (base.get("role_ids") or [])],
        "level": grant.get("level"),
        "owns_server": owner_id == user_id,
        "teams": [{"id": str(r.get("_id")), "team_id": r.get("team_id"), "team_name": r.get("team_name") or "—",
                   "position": r.get("position") or "", "jersey": r.get("jersey"), "status": r.get("status") or "active"} for r in roster],
    }


async def _rows(ctx: Ctx) -> list[dict]:
    """The bot's roster (if it has been synced) merged with dashboard access and team membership."""
    gid = ctx.guild_id
    members = await coll("members").find({"guild_id": gid, "bot": {"$ne": True}}).limit(5000).to_list(None)
    grants = {str(g["user_id"]): g for g in await coll("dashboard_permissions").find({"guild_id": gid}).to_list(None)}
    roster: dict[str, list] = {}
    for r in await coll("team_members").find({"guild_id": gid}).to_list(None):
        roster.setdefault(str(r.get("user_id")), []).append(r)
    owner_id = str(ctx.guild.get("owner_id") or "")
    out = [_row(str(m["user_id"]), m, grants.pop(str(m["user_id"]), {}), roster.get(str(m["user_id"]), []), owner_id) for m in members]
    # Anyone the dashboard already knows about keeps showing up before the first sync.
    seen = {r["user_id"] for r in out}
    for uid, grant in grants.items():
        if uid in seen:
            continue
        out.append(_row(uid, {}, grant, roster.get(uid, []), owner_id))
    for uid, rows in roster.items():
        if uid not in seen and uid not in grants:
            out.append(_row(uid, {"username": rows[0].get("username")}, {}, rows, owner_id))
    out.sort(key=lambda r: (r["display_name"] or "").lower())
    # Cash in one query; a member who never touched the economy still has the starting balance.
    held = await cash_balances(db_handle(), gid, [r["user_id"] for r in out])
    for r in out:
        r["cash"] = held.get(r["user_id"], STARTING_CASH)
        r["cash_display"] = format_cash(r["cash"])
    return out


@router.get("")
async def list_members(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("staff")
    rows = await _rows(ctx)
    sync = await coll("member_sync").find_one({"guild_id": ctx.guild_id}) or {}
    roles = [{"id": r["id"], "name": r["name"], "color": r.get("color", 0), "position": r.get("position", 0)}
             for r in ctx.guild.get("roles", []) if not r.get("managed") and r.get("id") != ctx.guild_id]
    roles.sort(key=lambda r: -r["position"])
    return {
        "items": rows,
        "synced_at": ser(sync.get("synced_at")),
        "assignable_roles": roles,
        "notice": None if sync.get("synced_at") else "This list is built from dashboard grants and rosters. Press “Sync members” to load everyone from Discord.",
    }


@router.post("/sync")
async def sync_members(ctx: Ctx = Depends(guild_ctx)):
    """Ask the bot to publish the current member list (bots excluded, capped at Discord's 5000-member sync)."""
    ctx.require("staff")
    r = await bridge.run_job(ctx.guild_id, "members_sync", {}, ctx.user["user_id"], wait=20)
    await audit.log(ctx, "members.sync")
    out = {"ok": True, "synced": (r.get("result") or {}).get("members")}
    return finish(out, r)


@router.patch("/{user_id}")
async def update_member(user_id: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Set dashboard access (`level`) and/or put the member on a team (`team_id`, `position`, `jersey`, `status`)."""
    ctx.require("manager")
    uid = str(user_id).strip()
    if not uid.isdigit():
        raise HTTPException(404, "Unknown member")
    username = str(body.get("username") or "")[:80]

    if "level" in body:
        ctx.require("admin")
        level = str(body.get("level") or "").strip()
        if level and level not in GRANT_LEVELS:
            err("Pick viewer, staff or manager — or no dashboard access.")
        if level:
            await coll("dashboard_permissions").update_one(
                {"guild_id": ctx.guild_id, "user_id": uid},
                {"$set": {"level": level, "username": username, "granted_by": ctx.user["user_id"], "updated_at": now()},
                 "$setOnInsert": {"created_at": now()}}, upsert=True)
        else:
            await coll("dashboard_permissions").delete_one({"guild_id": ctx.guild_id, "user_id": uid})
        await audit.log(ctx, "member.access", username or uid, {"level": level or "none"})

    if "team_id" in body:
        team_id = body.get("team_id")
        if team_id in (None, "", 0, "0"):
            await coll("team_members").delete_many({"guild_id": ctx.guild_id, "user_id": uid})
            await audit.log(ctx, "member.team", username or uid, {"team": "none"})
        else:
            team = await team_of(ctx, team_id)
            status = str(body.get("status") or "active")
            if status not in ROSTER_STATUSES:
                err("Status must be active, reserve, injured or inactive.")
            jersey = body.get("jersey")
            if jersey in (None, ""):
                jersey = None
            else:
                try:
                    jersey = int(jersey)
                except (TypeError, ValueError):
                    err("Jersey must be a number")
                if not 0 <= jersey <= 999:
                    err("Jersey must be 0–999")
            if jersey is not None:
                taken = await coll("team_members").find_one({"guild_id": ctx.guild_id, "team_id": team["team_id"],
                                                             "jersey": jersey, "user_id": {"$ne": uid}})
                if taken:
                    err(f"Jersey #{jersey} is already taken on {team['name']}.")
            await coll("team_members").delete_many({"guild_id": ctx.guild_id, "user_id": uid,
                                                    "team_id": {"$ne": team["team_id"]}})
            sets = {"team_name": team["name"], "position": str(body.get("position") or "")[:40], "status": status,
                    "username": username or uid, "updated_at": now()}
            if jersey is not None:
                sets["jersey"] = jersey
            await coll("team_members").update_one(
                {"guild_id": ctx.guild_id, "team_id": team["team_id"], "user_id": uid},
                {"$set": sets, "$setOnInsert": {"created_at": now(), "created_by": ctx.user["user_id"]}}, upsert=True)
            await audit.log(ctx, "member.team", username or uid, {"team": team["name"], "status": status})

    return {"ok": True, "item": _row(uid, {}, await coll("dashboard_permissions").find_one({"guild_id": ctx.guild_id, "user_id": uid}) or {},
                                     await coll("team_members").find({"guild_id": ctx.guild_id, "user_id": uid}).to_list(None),
                                     str(ctx.guild.get("owner_id") or ""))}


@router.put("/{user_id}/roles")
async def set_member_roles(user_id: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Replace a member's Discord roles. Changes are applied by the bot (it holds the token and checks role order)."""
    ctx.require("admin")
    uid = str(user_id).strip()
    if not uid.isdigit():
        raise HTTPException(404, "Unknown member")
    try:
        wanted = clean_value(F("role_ids", "snowflakes", max_items=25), body.get("role_ids") or [])
    except ValueError as e:
        err(str(e))
    cache = {r["id"]: r for r in ctx.guild.get("roles", [])}
    missing = [r for r in wanted if r not in cache or r == ctx.guild_id or cache[r].get("managed")]
    if missing:
        err("One of those roles is gone, managed by Discord, or is @everyone.")
    known = await coll("members").find_one({"guild_id": ctx.guild_id, "user_id": uid})
    if not known:
        err("Sync members first — without their current roles I can't safely remove any.")
    current = {str(r) for r in (known.get("role_ids") or [])}
    add, remove = [r for r in wanted if r not in current], [r for r in current if r not in wanted]
    if not add and not remove:
        return {"ok": True, "notice": "No role changes needed."}
    r = await bridge.run_job(ctx.guild_id, "member_roles",
                             {"user_id": uid, "add": add, "remove": remove, "by": ctx.user["username"]},
                             ctx.user["user_id"])
    await audit.log(ctx, "member.roles", uid, {"add": add, "remove": remove})
    if r["ok"]:
        await coll("members").update_one({"guild_id": ctx.guild_id, "user_id": uid},
                                         {"$set": {"role_ids": sorted((current | set(add)) - set(remove)), "roles_synced_at": now()}})
    names = lambda ids: [cache[i]["name"] for i in ids if i in cache]  # noqa: E731
    return finish({"ok": True, "add": names(add), "remove": names(remove)}, r)
