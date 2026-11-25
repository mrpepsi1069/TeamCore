"""backend/auth.py — Discord OAuth2 login, sessions, sign-out.

Flow: /auth/login -> Discord -> /auth/callback -> redirect to FRONTEND/dashboard/#token=<session token>.
The token is delivered in the URL *fragment* (never sent to any server or written to access logs) and then sent by the
frontend in an `Authorization: Bearer` header. Because the browser never attaches it automatically, cross-site request
forgery isn't possible; the OAuth `state` parameter protects the login itself, and writes also pass an Origin check.
"""

from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from shared import settings
from shared.security import Vault, hash_token, new_token
from backend.db import coll
from backend.deps import current_session, current_user, ttl_stale
from backend.util import now, ser

router = APIRouter(prefix="/auth", tags=["auth"])
DISCORD = "https://discord.com/api/v10"
_vault: Vault | None = None


def vault() -> Vault:
    global _vault
    if _vault is None:
        _vault = Vault(settings.DATA_ENCRYPTION_KEY)
    return _vault


def avatar_url(user: dict) -> str:
    if user.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=128"
    return f"https://cdn.discordapp.com/embed/avatars/{(int(user['id']) >> 22) % 6}.png"


def icon_url(g: dict) -> str | None:
    return f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png?size=128" if g.get("icon") else None


async def fetch_guilds(access_token: str) -> list:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{DISCORD}/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200:
        raise HTTPException(502, "Discord refused the guild list request.")
    return [{"id": g["id"], "name": g["name"], "icon": icon_url(g), "owner": bool(g.get("owner")), "permissions": str(g.get("permissions", "0"))}
            for g in r.json()]


@router.get("/login")
async def login():
    if not settings.DISCORD_CLIENT_ID:
        raise HTTPException(500, "DISCORD_CLIENT_ID is not configured")
    state = new_token()
    await coll("oauth_states").insert_one({"state": state, "expires_at": now() + timedelta(minutes=10)})
    q = urlencode({"client_id": settings.DISCORD_CLIENT_ID, "response_type": "code", "scope": "identify guilds", "state": state,
                   "redirect_uri": f"{settings.BACKEND_URL}/auth/callback", "prompt": "none"})
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{q}")


@router.get("/callback")
async def callback(code: str = "", state: str = "", error: str = ""):
    dash = f"{settings.FRONTEND_URL}/dashboard/"
    if error or not code:
        return RedirectResponse(f"{dash}#error=login_cancelled")
    # one-time state: prevents login CSRF / code injection
    st = await coll("oauth_states").find_one_and_delete({"state": state})
    if not st or st["expires_at"] <= now():
        return RedirectResponse(f"{dash}#error=bad_state")
    async with httpx.AsyncClient(timeout=10) as c:
        tok = await c.post(f"{DISCORD}/oauth2/token", data={
            "client_id": settings.DISCORD_CLIENT_ID, "client_secret": settings.DISCORD_CLIENT_SECRET, "grant_type": "authorization_code",
            "code": code, "redirect_uri": f"{settings.BACKEND_URL}/auth/callback"}, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if tok.status_code != 200:
            return RedirectResponse(f"{dash}#error=token_exchange_failed")
        access = tok.json()["access_token"]
        me = await c.get(f"{DISCORD}/users/@me", headers={"Authorization": f"Bearer {access}"})
    if me.status_code != 200:
        return RedirectResponse(f"{dash}#error=profile_failed")
    u = me.json()
    guilds = await fetch_guilds(access)

    await coll("users").update_one(
        {"user_id": u["id"]},
        {"$set": {"username": u.get("global_name") or u["username"], "avatar_url": avatar_url(u), "last_login": now()},
         "$setOnInsert": {"created_at": now(), "preferences": {"accent": "#FF7A1A", "timezone": "UTC"}}}, upsert=True)

    token = new_token()
    await coll("sessions").insert_one({
        "token_hash": hash_token(token), "user_id": u["id"], "access_token_enc": vault().encrypt(access), "guilds": guilds,
        "guilds_fetched_at": now(), "created_at": now(), "expires_at": now() + timedelta(days=settings.SESSION_TTL_DAYS)})
    return RedirectResponse(f"{dash}#token={token}")


@router.post("/logout")
async def logout(session: dict = Depends(current_session)):
    await coll("sessions").delete_one({"_id": session["_id"]})
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return ser({k: user.get(k) for k in ("user_id", "username", "avatar_url", "preferences")})


async def refreshed_guilds(session: dict, force: bool = False) -> list:
    """The user's guild list, re-fetched from Discord at most every GUILD_LIST_TTL_SECONDS (rate-limit friendly)."""
    if not force and not ttl_stale(session, settings.GUILD_LIST_TTL_SECONDS):
        return session["guilds"]
    token = vault().decrypt(session.get("access_token_enc", ""))
    if not token:
        return session["guilds"]
    try:
        guilds = await fetch_guilds(token)
    except HTTPException:
        return session["guilds"]  # keep the last good list rather than logging the user out
    await coll("sessions").update_one({"_id": session["_id"]}, {"$set": {"guilds": guilds, "guilds_fetched_at": now()}})
    return guilds
