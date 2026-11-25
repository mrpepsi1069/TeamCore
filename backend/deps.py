"""backend/deps.py — authentication + per-server authorisation (runs server-side on EVERY request)."""

from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request

from shared.access import has_level, level_from_discord
from shared.security import hash_token
from backend.db import coll
from backend.util import now


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
    server = await coll("servers").find_one({"guild_id": gid})
    if not server:
        raise HTTPException(404, "TeamCore is not in this server (or hasn't synced yet).")
    level = level_from_discord(g.get("owner"), g.get("permissions"))
    if level is None:
        grant = await coll("dashboard_permissions").find_one({"guild_id": gid, "user_id": session["user_id"]})
        level = grant["level"] if grant else None
    if level is None:
        raise HTTPException(403, "You don't have dashboard access to this server. Ask an admin to grant it.")
    return Ctx(user=user, session=session, guild_id=gid, level=level, guild=server)


def ttl_stale(session: dict, seconds: int) -> bool:
    fetched = session.get("guilds_fetched_at")
    return fetched is None or now() - fetched > timedelta(seconds=seconds)
