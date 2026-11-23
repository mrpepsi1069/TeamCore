"""utils/timeparse.py — duration/date parsing shared by polls, giveaways and countdowns."""

import re
from datetime import datetime, timedelta, timezone

_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
_PART = re.compile(r"(\d+)\s*([wdhms])", re.I)


def parse_duration(text: str) -> timedelta | None:
    """'90m', '1h30m', '2d', '1w 2d' -> timedelta. A bare number means minutes. None if invalid/zero."""
    text = (text or "").strip().lower().replace(",", " ")
    if not text:
        return None
    if text.isdigit():
        secs = int(text) * 60
    else:
        parts = _PART.findall(text)
        if not parts or _PART.sub("", text).strip():
            return None
        secs = sum(int(n) * _UNITS[u.lower()] for n, u in parts)
    return timedelta(seconds=secs) if secs > 0 else None


def format_duration(td: timedelta) -> str:
    secs = int(td.total_seconds())
    out = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if secs >= size:
            out.append(f"{secs // size}{name}")
            secs %= size
    return " ".join(out) or "0s"


def parse_local_datetime(date_str: str, time_str: str, tz_name: str) -> datetime | None:
    """'2026-12-25', '18:30', 'America/New_York' -> aware UTC datetime. None if anything is invalid."""
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name)
        local = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%Y-%m-%d %H:%M")
        return local.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
