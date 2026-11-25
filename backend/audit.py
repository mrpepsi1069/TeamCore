"""backend/audit.py — every dashboard change is recorded (Audit Log page)."""

from backend.db import coll
from backend.util import now


async def log(ctx, action: str, target: str = "", detail: dict | None = None) -> None:
    await coll("audit_logs").insert_one({
        "guild_id": ctx.guild_id, "user_id": ctx.user["user_id"], "user_name": ctx.user.get("username", ""),
        "action": action, "target": target, "detail": detail or {}, "created_at": now(),
    })
