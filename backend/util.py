"""backend/util.py — serialisation and small helpers."""

import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

HIDDEN = {"name_lower", "competition_lower", "division_lower", "url_enc", "token_hash", "access_token_enc", "text_lower"}


def ser(value):
    """Mongo document -> JSON-safe dict (ObjectId -> id, datetimes -> ISO-8601 UTC, internal fields removed)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in HIDDEN:
                continue
            out["id" if k == "_id" else k] = ser(v)
        return out
    if isinstance(value, list):
        return [ser(v) for v in value]
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(404, "Not found")


def now() -> datetime:
    return datetime.now(timezone.utc)


def regex_contains(text: str) -> dict:
    return {"$regex": re.escape(text.strip()[:60]), "$options": "i"}


class RateLimiter:
    """Tiny in-memory sliding-window limiter (per key). Protects the free-tier database from a runaway client."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, {}

    def allow(self, key: str, t: float) -> bool:
        q = [x for x in self.hits.get(key, []) if t - x < self.window]
        ok = len(q) < self.limit
        if ok:
            q.append(t)
        self.hits[key] = q
        if len(self.hits) > 5000:  # bound memory
            self.hits = {k: v for k, v in self.hits.items() if v and t - v[-1] < self.window}
        return ok
