"""backend/main.py — TeamCore dashboard API (FastAPI).  Run:  uvicorn backend.main:app --host 0.0.0.0 --port $PORT"""

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared import settings
from shared.security import hash_token
from database.schema import apply_indexes
from backend import auth, db, resources, routes_features, routes_guild
from backend.util import RateLimiter

limiter = RateLimiter(limit=180, window=60)  # requests / minute / session-or-IP


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    for e in await apply_indexes(db.db()):
        print(f"⚠️ index: {e}")
    yield
    await db.close()


app = FastAPI(title="TeamCore Dashboard API", version="1.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    # Origin check on every write: a browser always sends Origin on cross-site requests, so a hostile page is rejected
    # even if it somehow obtained a token. (Non-browser clients send no Origin and still need the bearer token.)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in settings.ALLOWED_ORIGINS:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    auth_h = request.headers.get("authorization", "")
    key = hash_token(auth_h)[:16] if auth_h else (request.client.host if request.client else "anon")
    if not limiter.allow(key, time.monotonic()):
        return JSONResponse({"detail": "Too many requests — slow down."}, status_code=429, headers={"Retry-After": "10"})
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# Added last = outermost, so even 403/429 answers from `guard` carry CORS headers and the browser can show the message.
# CORS: only the configured frontend origin(s). No wildcard, and no cookies are used, so credentials stay off.
app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"], max_age=600)

app.include_router(auth.router)
app.include_router(routes_guild.router)
app.include_router(routes_features.router)
app.include_router(resources.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/db")
async def health_db():
    """Which database is this API reading, and has the bot written to it? (No secrets: only the database name,
    the first two labels of the cluster address, and counts.) Use it to confirm the bot and website share one database."""
    from urllib.parse import urlparse
    from backend.util import ser
    d = db.db()
    host = (urlparse(settings.MONGODB_URI or "").hostname or "").split(".")
    hb = await d.servers.find_one({"guild_id": "_bot"}, {"heartbeat": 1})
    return {
        "database": settings.DB_NAME,
        "cluster_hint": ".".join(host[:2]),
        "servers_synced": await d.servers.count_documents({"guild_id": {"$ne": "_bot"}}),
        "bot_last_seen": ser(hb["heartbeat"]) if hb and hb.get("heartbeat") else None,
    }


# Optional single-host mode: SERVE_FRONTEND=1 serves /frontend from this same server (set API_BASE to "" and FRONTEND_URL = BACKEND_URL).
if os.getenv("SERVE_FRONTEND") == "1":
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True), name="frontend")
