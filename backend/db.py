"""backend/db.py — MongoDB access via PyMongo's async API (same database the bot uses)."""

from pymongo import AsyncMongoClient

from shared import settings

_client: AsyncMongoClient | None = None
_db = None


async def connect() -> None:
    global _client, _db
    if not settings.MONGODB_URI:
        raise RuntimeError("MONGODB_URI (or DATABASE_URL) is not set")
    _client = AsyncMongoClient(settings.MONGODB_URI, tz_aware=True, serverSelectionTimeoutMS=8000, maxPoolSize=10)
    _db = _client[settings.DB_NAME]
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
