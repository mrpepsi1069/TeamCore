"""backend/deps.py — authentication + per-server authorisation (runs server-side on EVERY request)."""

from dataclasses import dataclass
from datetime import timedelta

import httpx
from fastapi import Depends, HTTPException, Request

from shared import settings as env
from shared.access import has_level, level_from_discord
from shared.security import hash_token
from backend.db import coll
from backend.util import now

CHANNEL_TYPES = {0: "text", 2: "voice", 4: "category", 5: "news", 13: "stage", 15: "forum"}


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer ") or len(h) < 20:
        raise HTTPException(401, "Not signed in")
    return h[7:].strip()


async def current_session(request: Request) -> dict:
    token = _bearer(request)
    s = await coll("sessions").find_one({"token_hash": hash_token(token)})
    if not s or s["expires_at"] <= now():
        raise HTTPException(401, "Session expired — please sign in again")
    return s


async def current_user(session: dict = Depends(current_session)) -> dict:
    u = await coll("users").find_one({"user_id": session["user_id"]})
    if not u:
        raise HTTPException(401, "Unknown user")
    return u


async def _bootstrap_server_from_discord(gid: str) -> dict | None:
    """If the bot cog hasn't synced this guild into MongoDB yet, fetch it via DISCORD_BOT_TOKEN and cache it."""
    token = getattr(env, "DISCORD_BOT_TOKEN", None) or getattr(env, "BOT_TOKEN", None)
    if not token:
        return None
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=10) as c:
        gr = await c.get(f"https://discord.com/api/v10/guilds/{gid}?with_counts=true", headers=headers)
        if gr.status_code != 200:
            return None
        cr = await c.get(f"https://discord.com/api/v10/guilds/{gid}/channels", headers=headers)
    g = gr.json()
    raw_channels = cr.json() if cr.status_code == 200 else []
    cats = {str(ch["id"]): ch["name"] for ch in raw_channels if ch.get("type") == 4}
    channels = [
        {"id": str(ch["id"]), "name": ch["name"], "type": CHANNEL_TYPES.get(ch.get("type"), "other"), "category": cats.get(str(ch.get("parent_id")))}
        for ch in raw_channels
    ]
    roles = [
        {"id": str(r["id"]), "name": r["name"], "color": r.get("color", 0), "managed": bool(r.get("managed")), "position": r.get("position", 0), "permissions": str(r.get("permissions", "0"))}
        for r in g.get("roles", [])
    ]
    icon = f"https://cdn.discordapp.com/icons/{gid}/{g['icon']}.png?size=128" if g.get("icon") else None
    doc = {
        "guild_id": gid, "name": g.get("name"), "icon": icon, "owner_id": str(g.get("owner_id", "")),
        "member_count": g.get("approximate_member_count"), "boosts": g.get("premium_subscription_count", 0),
        "tier": g.get("premium_tier", 0), "channels": channels, "roles": roles, "updated_at": now(),
    }
    await coll("servers").update_one({"guild_id": gid}, {"$set": doc}, upsert=True)
    return doc


@dataclass
class Ctx:
    user: dict
    session: dict
    guild_id: str
    level: str
    guild: dict          # the bot's cached `servers` document (name, icon, channels, roles…)

    def require(self, need: str) -> None:
        if not has_level(self.level, need):
            raise HTTPException(403, f"This needs the '{need}' dashboard level (you have '{self.level}').")


async def guild_ctx(gid: str, session: dict = Depends(current_session), user: dict = Depends(current_user)) -> Ctx:
    """Who is this user in this server?
    1) they must be a member (Discord says so: the guild is in their OAuth guild list),
    2) the bot must be in the server,
    3) Discord owner / Administrator / Manage Server => admin; otherwise an explicit dashboard_permissions grant."""
    if not gid.isdigit():
        raise HTTPException(404, "Unknown server")
    g = next((x for x in session.get("guilds", []) if x["id"] == gid), None)
    if g is None:
        raise HTTPException(403, "You are not a member of this server.")
    query_ids = [gid, int(gid)]
    server = await coll("servers").find_one({"guild_id": {"$in": query_ids}})
    if not server:
        server = await _bootstrap_server_from_discord(gid)
    if not server:
        raise HTTPException(404, "TeamCore is not in this server (or hasn't synced yet).")
    level = level_from_discord(g.get("owner"), g.get("permissions"))
    if level is None:
        grant = await coll("dashboard_permissions").find_one({"guild_id": {"$in": query_ids}, "user_id": session["user_id"]})
        level = grant["level"] if grant else None
    if level is None:
        raise HTTPException(403, "You don't have dashboard access to this server. Ask an admin to grant it.")
    return Ctx(user=user, session=session, guild_id=gid, level=level, guild=server)


def ttl_stale(session: dict, seconds: int) -> bool:
    fetched = session.get("guilds_fetched_at")
    return fetched is None or now() - fetched > timedelta(seconds=seconds)
