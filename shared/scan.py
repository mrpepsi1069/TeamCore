"""shared/scan.py — read OCR text from a screenshot into a match's game stats.

Practical Basketball and Ultimate Football don't export a report, so the dashboard reads a screenshot with an OCR
provider and this module turns the loose text into the same shape a Football Fusion report has (score, reporter,
players with stat blocks). OCR output is messy, so the parse is forgiving: find a score line, find a stat-table header,
then read rows of “Name 12 4 3” underneath. Anything it can't read is left out — the manager reviews the text in the
import dialog and can correct it before the match is saved.

The parser is pure (no database or Discord imports), so the bot can reuse it too.
"""

import re

from shared.fusion import parse_report

SPORTS = {"ff": "Football Fusion", "pb": "Practical Basketball", "uf": "Ultimate Football"}
DEFAULT_SPORT = "ff"
MAX_TEXT = 20_000

# Column abbreviations a box score prints above the players. Matching a header is how the parser knows a table starts.
STAT_KEYS = {
    "pb": ("PTS", "REB", "AST", "STL", "BLK", "TO", "PF", "FG", "3PT", "FT", "MIN"),
    "uf": ("GLS", "TDS", "AST", "TKL", "SACK", "INT", "YDS", "REC", "SV", "SHO", "ATT"),
}
_EXTRA_KEYS = ("OVR", "GP", "RATING")

_SCORE = re.compile(r"^(?:(?:final|score|result)\s*:?\s*)?(.{2,60}?)\s+(\d{1,3})\s*[-\u2013\u2014:]\s*(\d{1,3})\s+(.{2,60}?)$", re.I)
_REPORTER = re.compile(r"^(?:reporter|reported by|submitted by)\s*:\s*(.+?)$", re.I)
_STAT_TOKEN = re.compile(r"^(?:\d{1,4}(?:\.\d{1,3})?|\d{1,3}-\d{1,3}|\d{1,3}%)$")
_SKIP_NAMES = {"total", "totals", "team", "team totals", "bench", "player", "players", "name"}


def label(sport: str) -> str:
    return SPORTS.get((sport or "").lower(), SPORTS[DEFAULT_SPORT])


def _keys(sport: str) -> tuple:
    if sport in STAT_KEYS:
        return STAT_KEYS[sport] + _EXTRA_KEYS
    return tuple(dict.fromkeys(STAT_KEYS["pb"] + STAT_KEYS["uf"] + _EXTRA_KEYS))


def _lines(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    return [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]


def _number(token: str):
    if re.fullmatch(r"\d{1,4}", token):
        return int(token)
    if re.fullmatch(r"\d{1,4}\.\d{1,3}", token):
        return float(token)
    return None


def _score(lines: list[str]) -> dict | None:
    """The first line that reads “Team A 45 - 51 Team B” (the word Score/Final is optional)."""
    for line in lines:
        m = _SCORE.match(line)
        if not m:
            continue
        home, hs, as_, away = m.group(1).strip(" :-"), int(m.group(2)), int(m.group(3)), m.group(4).strip(" :-")
        if not home or not away or home.isdigit() or away.isdigit():
            continue
        return {"home": home, "home_score": hs, "away": away, "away_score": as_}
    return None


def _reporter(lines: list[str]) -> dict | None:
    for line in lines:
        m = _REPORTER.match(line)
        if m and m.group(1).strip():
            value = m.group(1).strip()
            uid = re.search(r"\((\d{15,21})\)", value)
            name = re.sub(r"\s*\(\d{15,21}\)\s*$", "", value).strip()
            return {"name": name or value, "display": None, "user_id": uid.group(1) if uid else None}
    return None


def _header(line: str, keys: tuple) -> list[str] | None:
    """A table header is a line with at least two known column names (PTS REB AST …)."""
    tokens = [t for t in re.split(r"[^A-Za-z0-9%]+", line.upper()) if t]
    found = [t for t in tokens if t in keys]
    return found if len(found) >= 2 else None


def _row(line: str, header: list[str]) -> dict | None:
    """„DrakioAlt 12 4 3” -> a player row; the trailing tokens are the header's columns in order."""
    parts = line.split()
    if len(parts) < 2:
        return None
    tail = []
    while parts and len(tail) < len(header) and _STAT_TOKEN.match(parts[-1]):
        tail.insert(0, parts[-1])
        parts.pop()
    if not tail or not parts:
        return None
    name = " ".join(parts).strip(" .")
    if not re.search(r"[A-Za-z]", name) or name.lower() in _SKIP_NAMES:
        return None
    stats = {}
    for col, token in zip(header, tail):
        value = _number(token)
        stats[col] = value if value is not None else token
    return {"name": name[:80], "user_id": None, "positions": {}, "stats": stats}


def parse_scan(text: str, sport: str = DEFAULT_SPORT) -> dict:
    """OCR text -> {game, score, reporter, players, linked, total, sport}. Only the score is required."""
    sport = (sport or DEFAULT_SPORT).lower()
    if sport not in SPORTS:
        raise ValueError("Pick Football Fusion, Practical Basketball or Ultimate Football.")
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Scan a screenshot or paste its text first.")
    if len(raw) > MAX_TEXT:
        raise ValueError(f"That text is too long (max {MAX_TEXT:,} characters).")
    lines = _lines(raw)
    score = _score(lines)
    if not score:
        raise ValueError("Couldn't find the score — expected a line like “Dimers 45 - 51 Bruins”.")

    keys = _keys(sport)
    header, players = None, []
    for line in lines:
        if not line:
            continue
        cols = _header(line, keys)
        if cols:
            header = cols
            continue
        if header and len(players) < 60:
            row = _row(line, header)
            if row:
                players.append(row)

    return {"game": SPORTS[sport], "identifier": None, "score": score, "reporter": _reporter(lines),
            "players": players, "linked": None, "total": len(players), "sport": sport}


def parse_any(text: str, sport: str = DEFAULT_SPORT) -> dict:
    """Import entry point: the FF report parser when that fits, the screenshot parser otherwise."""
    sport = (sport or DEFAULT_SPORT).lower()
    if sport not in SPORTS:
        raise ValueError("Pick Football Fusion, Practical Basketball or Ultimate Football.")
    if sport == "ff":
        try:
            game = parse_report(text)
        except ValueError:
            game = parse_scan(text, sport)  # an OCR'd FF screenshot can lose the report's labels
    else:
        game = parse_scan(text, sport)
    game["sport"] = sport
    return game
