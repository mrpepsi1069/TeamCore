"""utils/validation.py — Input validation helpers."""

import re
from urllib.parse import urlparse


def is_valid_hex_color(color: str) -> bool:
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", color))


def is_valid_url(string: str) -> bool:
    try:
        r = urlparse(string)
        return r.scheme in ("http", "https") and bool(r.netloc)
    except Exception:
        return False


def sanitize_input(text: str, max_length: int = 200) -> str:
    return (text or "").strip()[:max_length]


def is_valid_league_abbr(abbr: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{2,10}", abbr))


def validate_season(season: str) -> bool:
    return bool(season) and 1 <= len(season) <= 20
