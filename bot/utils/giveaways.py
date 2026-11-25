"""utils/giveaways.py — giveaway rendering + winner selection (pure functions)."""

import random
from datetime import datetime, timezone
import discord
from utils.ui import brand


def pick_winners(candidates: list, count: int, exclude: set | None = None, rng=random) -> list:
    """Uniformly random, no repeats; never returns anyone in `exclude`."""
    pool = [c for c in dict.fromkeys(candidates) if c not in (exclude or set())]
    return rng.sample(pool, min(count, len(pool)))


def eligible(member: discord.Member | None, gw: dict, now: datetime | None = None) -> tuple[bool, str]:
    """(ok, reason). Used both when joining and again at draw time (people may have left / lost the role)."""
    if member is None:
        return False, "You must be in the server."
    if gw.get("required_role_id") and not member.get_role(int(gw["required_role_id"])):
        return False, f"You need the <@&{gw['required_role_id']}> role to enter."
    min_days = gw.get("min_account_days") or 0
    if min_days:
        age = ((now or datetime.now(timezone.utc)) - member.created_at).days
        if age < min_days:
            return False, f"Your account must be at least {min_days} day(s) old (yours is {age})."
    return True, ""


def giveaway_embed(gw: dict, entries: int, ended: bool = False) -> discord.Embed:
    e = brand(f"🎉 {gw['prize']}", color=0x95A5A6 if ended else 0xFFD700)
    lines = [
        f"**Hosted by:** <@{gw['created_by']}>",
        f"**Winners:** {gw['winners_count']}",
        f"**Entries:** {entries}",
        ("**Ended:** " if ended else "**Ends:** ") + discord.utils.format_dt(gw["ends_at"], "R"),
    ]
    reqs = []
    if gw.get("required_role_id"):
        reqs.append(f"Role: <@&{gw['required_role_id']}>")
    if gw.get("min_account_days"):
        reqs.append(f"Account age: {gw['min_account_days']}+ days")
    if reqs:
        lines.append("**Requirements:** " + " · ".join(reqs))
    if ended:
        w = gw.get("winners") or []
        lines.append("**🏆 Winner(s):** " + (" ".join(f"<@{u}>" for u in w) if w else "No valid entries"))
    else:
        lines.append("\nClick **Enter** below to join!")
    e.description = "\n".join(lines)
    e.set_footer(text=f"TeamCore • Giveaway #{gw['giveaway_id']}")
    return e


def giveaway_view(gw: dict, disabled: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Enter", emoji="🎉", style=discord.ButtonStyle.success, custom_id=f"giveaway:{gw['giveaway_id']}:join", disabled=disabled
    ))
    return view
