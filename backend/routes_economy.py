"""backend/routes_economy.py — the dashboard's Economy page.

Read-only in the money sense: it reports Cash in circulation, what created or destroyed it in the
ledger, and each casino game's observed payback. Nothing here can move a balance.
"""

from fastapi import APIRouter, Depends

from backend.db import db as db_handle
from backend.deps import Ctx, guild_ctx
from database.economy import health

router = APIRouter(prefix="/api/guilds/{gid}/economy", tags=["economy"])


@router.get("")
async def economy_report(ctx: Ctx = Depends(guild_ctx), days: int = 30):
    """Supply, faucet vs sink and per-game RTP. `days` bounds the flow/RTP window (1–365)."""
    ctx.require("manager")
    days = max(1, min(int(days), 365))
    return await health(db_handle(), ctx.guild_id, days)
