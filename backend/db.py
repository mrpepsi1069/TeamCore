"""backend/db.py — MongoDB access via PyMongo's async API (same database the bot uses)."""

import re

from pymongo import AsyncMongoClient

from shared import settings

_client: AsyncMongoClient | None = None
_db = None

# MongoDB database names can't contain these characters (a hostname such as cluster0.abc.mongodb.net is the usual mistake).
_BAD_NAME = re.compile(r'[/\\. "$*<>:|?\x00]')


async def connect() -> None:
    global _client, _db
    if not settings.MONGODB_URI:
        raise RuntimeError("MONGODB_URI (or DATABASE_URL) is not set")
    name = settings.DB_NAME
    if not name or _BAD_NAME.search(name) or len(name) > 63:
        raise RuntimeError(
            f"MONGODB_DB is set to {name!r}, which is not a valid database name. "
            "Delete the MONGODB_DB variable (the default is 'lockerroom_bot', the database the bot uses), "
            "or set it to exactly that. The cluster address belongs in MONGODB_URI, not here."
        )
    _client = AsyncMongoClient(settings.MONGODB_URI, tz_aware=True, serverSelectionTimeoutMS=8000, maxPoolSize=10)
    _db = _client[name]
    await _client.admin.command("ping")  # fail fast with a clear error if Atlas is unreachable / IP not allowed


async def close() -> None:
    if _client is not None:
        await _client.close()


def db():
    if _db is None:
        raise RuntimeError("database not connected")
    return _db


def coll(name: str):
    return db()[name]
