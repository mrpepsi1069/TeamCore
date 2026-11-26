"""shared/fusion.py — parse a Football Fusion "Game Report" into a match's structured game stats.

Football Fusion copies a plain-text report, e.g.:

    Game Report • PFG || S2 Week 2
    Game Identifier: 1434428378617876482-216A1BBD
    Score: Penn State Nittany Lions 45 - 51 UCLA Bruins
    Reporter: v0lumezz (Zone6Volumez) (914705157139353701)

    Player Stats:
    1. DrakioAlt (Not linked)
      [QB]
        attempts: 1
    2. blobbybobby2168 (<@1238658294206431263>)
      [WR]
        catches: 1
    ...
    Linked players: 7/17

`parse_report` turns that into one dict (score, reporter, players with per-position stats) that a match
stores and the dashboard renders. It tolerates missing extras: the score and at least one player are required.
The parser is pure (no database or Discord imports), so the bot can reuse it.
"""

import json
import re

MAX_REPORT = 20_000

# The sports a match can carry. Only Football Fusion exports structured stats; the other two keep a screenshot.
SPORTS = {"ff": "Football Fusion", "pb": "Practical Basketball", "uf": "Ultimate Football"}
DEFAULT_SPORT = "ff"

_TITLE = re.compile(r"^\s*Game Report\s*[•·]\s*(.+?)\s*$", re.I)
_IDENT = re.compile(r"^\s*Game Identifier:\s*(\S+)\s*$", re.I)
_SCORE = re.compile(r"^\s*Score:\s*(.+?)\s+(\d{1,3})\s*[-–]\s*(\d{1,3})\s+(.+?)\s*$", re.I)
_REPORTER = re.compile(r"^\s*Reporter:\s*(.+?)\s*\((.*?)\)\s*\((\d{15,21})\)\s*$")
_REPORTER_SIMPLE = re.compile(r"^\s*Reporter:\s*(.+?)\s*\((\d{15,21})\)\s*$")
_PLAYER = re.compile(r"^\s*\d+\.\s+(.+?)\s+\((?:<@!?(\d{15,21})>|Not linked)\)\s*$", re.I)
_POSITION = re.compile(r"^\s*\[([A-Za-z0-9]{1,6})\]\s*$")
_STAT = re.compile(r"^\s{4,}([a-z_]+):\s*(-?\d+)\s*$", re.I)
_LINKED = re.compile(r"^\s*Linked players:\s*(\d+)\s*/\s*(\d+)\s*$", re.I)


def _unwrap(text: str) -> str:
    """Some FF exports wrap the report in JSON — accept {"report": "…"} (or a few common key names) too."""
    t = (text or "").strip()
    if not t.startswith("{"):
        return t
    try:
        data = json.loads(t)
    except ValueError:
        return t
    if isinstance(data, dict):
        for key in ("report", "text", "content", "message", "description"):
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key]
    return t


def parse_report(text: str) -> dict:
    """Text report -> {game, identifier, score, reporter, players, linked, total}. Raises ValueError with a
    user-facing message when the score or player list is missing."""
    raw = _unwrap(text)
    if not raw:
        raise ValueError("Paste a Football Fusion game report first.")
    if len(raw) > MAX_REPORT:
        raise ValueError(f"That report is too long (max {MAX_REPORT:,} characters).")

    out = {"game": None, "identifier": None, "score": None, "reporter": None, "players": [], "linked": None, "total": None}
    current, position = None, None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if (m := _TITLE.match(line)):
            out["game"] = m.group(1).strip()
        elif (m := _IDENT.match(line)):
            out["identifier"] = m.group(1).strip()
        elif (m := _SCORE.match(line)):
            out["score"] = {"home": m.group(1).strip(), "home_score": int(m.group(2)),
                            "away": m.group(4).strip(), "away_score": int(m.group(3))}
        elif (m := _REPORTER.match(line)):
            out["reporter"] = {"name": m.group(1).strip(), "display": m.group(2).strip() or None, "user_id": m.group(3)}
        elif (m := _REPORTER_SIMPLE.match(line)):
            out["reporter"] = {"name": m.group(1).strip(), "display": None, "user_id": m.group(2)}
        elif (m := _LINKED.match(line)):
            out["linked"], out["total"] = int(m.group(1)), int(m.group(2))
        elif (m := _PLAYER.match(line)):
            current = {"name": m.group(1).strip(), "user_id": m.group(2), "positions": {}}
            out["players"].append(current)
            position = None
        elif (m := _POSITION.match(line)):
            if current is not None:
                position = m.group(1).upper()
                current["positions"].setdefault(position, {})
        elif (m := _STAT.match(line)) and current is not None and position is not None:
            current["positions"][position][m.group(1).lower()] = int(m.group(2))

    if not out["score"]:
        raise ValueError("Couldn't find the score — expected “Score: Team A 45 - 51 Team B”.")
    if not out["players"]:
        raise ValueError("Couldn't find any player stats under “Player Stats:”.")
    out["total"] = out["total"] if out["total"] is not None else len(out["players"])
    out["linked"] = out["linked"] if out["linked"] is not None else sum(1 for p in out["players"] if p["user_id"])
    return out


def stat_line(stats: dict) -> str:
    """{'catches': 2, 'yards': 63} -> 'catches 2 · yards 63' (shared by the dashboard and any Discord embed)."""
    return " · ".join(f"{(k or '').replace('_', ' ')} {v}" for k, v in (stats or {}).items()) or "—"
