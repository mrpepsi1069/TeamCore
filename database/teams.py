"""database/teams.py — teams, rosters, competitions and matches: ONE implementation for the bot and the website.

Every function takes the database handle first (`db`), so it works with the bot's Motor client and the backend's PyMongo
async client alike (only operations both drivers share are used). Everything is scoped by `guild_id`.
"""

from datetime import datetime, timezone

from pymongo import ReturnDocument

from shared.records import match_outcome

MAX_TEAMS = 100
ROSTER_MAX = 25          # a roster fits active players plus backups for a league
DEFAULT_COMPETITION = "General"
ROSTER_STATUSES = ("active", "reserve", "injured", "inactive")


class TeamError(ValueError):
    """A rule was broken (duplicate name, unknown team…). The message is safe to show to users."""


def _now():
    return datetime.now(timezone.utc)


def snap(team: dict) -> dict:
    return {k: team[k] for k in ("wins", "losses", "ties")}


async def next_seq(db, guild_id, name: str) -> int:
    doc = await db.counters.find_one_and_update({"_id": f"{guild_id}:{name}"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return int(doc["seq"])


# ───────── lookups ─────────
async def get_team(db, guild_id, key) -> dict | None:
    """By team_id (int / numeric str) or by name (case-insensitive)."""
    g = str(guild_id)
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit() and len(key) < 9):
        t = await db.teams.find_one({"guild_id": g, "team_id": int(key)})
        if t:
            return t
    return await db.teams.find_one({"guild_id": g, "name_lower": str(key).strip().lower()}) if key is not None else None


async def list_teams(db, guild_id, competition: str | None = None, division: str | None = None) -> list:
    q = {"guild_id": str(guild_id)}
    if competition:
        q["competition_lower"] = competition.strip().lower()
    if division:
        q["division"] = division
    return await db.teams.find(q).to_list(None)


# ───────── competitions ─────────
async def ensure_competition(db, guild_id, name: str, actor: str = "") -> None:
    """Make sure a `competitions` document exists for a name used by a team (so dashboard pickers list it)."""
    n = (name or DEFAULT_COMPETITION).strip()
    await db.competitions.update_one(
        {"guild_id": str(guild_id), "name_lower": n.lower()},
        {"$setOnInsert": {"guild_id": str(guild_id), "name": n, "name_lower": n.lower(), "status": "active", "season": "", "description": "",
                          "created_at": _now(), "created_by": actor}}, upsert=True)


# ───────── teams & records ─────────
async def add_history(db, guild_id, team: dict, action: str, actor, before: dict, after: dict, note: str = "") -> None:
    await db.team_history.insert_one({"guild_id": str(guild_id), "team_id": team["team_id"], "team_name": team["name"], "action": action,
                                      "by": str(actor), "before": snap(before) if "wins" in before else before, "after": snap(after) if "wins" in after else after,
                                      "note": note, "timestamp": _now()})


async def create_team(db, guild_id, name: str, competition: str | None = None, division: str | None = None,
                      logo: str | None = None, actor="", color: int | None = None) -> dict:
    g, name = str(guild_id), name.strip()
    if not name:
        raise TeamError("The team needs a name.")
    if await db.teams.find_one({"guild_id": g, "name_lower": name.lower()}):
        raise TeamError(f"A team named {name} already exists.")
    if await db.teams.count_documents({"guild_id": g}) >= MAX_TEAMS:
        raise TeamError(f"A server can have up to {MAX_TEAMS} teams.")
    comp = (competition or DEFAULT_COMPETITION).strip()
    doc = {"guild_id": g, "team_id": await next_seq(db, g, "team"), "name": name, "name_lower": name.lower(), "competition": comp, "competition_lower": comp.lower(),
           "division": division or None, "logo": logo or None, "wins": 0, "losses": 0, "ties": 0, "created_at": _now(), "created_by": str(actor)}
    if color:
        doc["color"] = int(color)   # the dashboard picks it; the bot turns it into the team's role colour
    doc["_id"] = (await db.teams.insert_one(doc)).inserted_id
    await ensure_competition(db, g, comp, str(actor))
    await add_history(db, g, doc, "created", actor, {"wins": 0, "losses": 0, "ties": 0}, doc)
    return doc


async def bump(db, guild_id, team: dict, field: str, count: int, action: str, actor, note: str = "") -> dict:
    """Atomically add to wins/losses/ties and write history."""
    after = await db.teams.find_one_and_update({"guild_id": str(guild_id), "team_id": team["team_id"]}, {"$inc": {field: count}}, return_document=ReturnDocument.AFTER)
    await add_history(db, guild_id, team, action, actor, team, after, note)
    return after


async def set_record(db, guild_id, team: dict, wins: int, losses: int, ties: int, action: str, actor, note: str = "") -> dict:
    after = await db.teams.find_one_and_update({"guild_id": str(guild_id), "team_id": team["team_id"]}, {"$set": {"wins": wins, "losses": losses, "ties": ties}}, return_document=ReturnDocument.AFTER)
    await add_history(db, guild_id, team, action, actor, team, after, note)
    return after


async def delete_team(db, guild_id, team: dict) -> None:
    g, tid = str(guild_id), team["team_id"]
    await db.teams.delete_one({"guild_id": g, "team_id": tid})
    await db.team_history.delete_many({"guild_id": g, "team_id": tid})
    await db.team_members.delete_many({"guild_id": g, "team_id": tid})
    await db.stats_channels.update_many({"guild_id": g, "key": f"team:{tid}"}, {"$set": {"orphaned": True}})


# ───────── rosters ─────────
async def add_roster_member(db, guild_id, team: dict, user_id: str, username: str = "", position: str = "", jersey: int | None = None,
                            status: str = "active", notes: str = "", actor="") -> dict:
    g = str(guild_id)
    if status not in ROSTER_STATUSES:
        raise TeamError("Status must be one of: " + ", ".join(ROSTER_STATUSES))
    if await db.team_members.find_one({"guild_id": g, "team_id": team["team_id"], "user_id": str(user_id)}):
        raise TeamError("That player is already on this roster.")
    if jersey is not None and await db.team_members.find_one({"guild_id": g, "team_id": team["team_id"], "jersey": jersey}):
        raise TeamError(f"Jersey #{jersey} is already taken on {team['name']}.")
    if await db.team_members.count_documents({"guild_id": g, "team_id": team["team_id"]}) >= ROSTER_MAX:
        raise TeamError(f"A roster can hold up to {ROSTER_MAX} players.")
    doc = {"guild_id": g, "team_id": team["team_id"], "team_name": team["name"], "user_id": str(user_id), "username": username, "position": position or "",
           "jersey": jersey, "status": status, "notes": notes, "created_at": _now(), "created_by": str(actor)}
    doc["_id"] = (await db.team_members.insert_one(doc)).inserted_id
    return doc


async def add_roster_batch(db, guild_id, team: dict, players: list[dict], actor="") -> list:
    """Add a whole roster at once (dashboard → Rosters → New roster). The batch is checked as a whole first, so one bad
    row adds nobody. Each row is {user_id, username, position, jersey, status}."""
    g, tid = str(guild_id), team["team_id"]
    current = await db.team_members.find({"guild_id": g, "team_id": tid}).to_list(None)
    if len(current) + len(players) > ROSTER_MAX:
        raise TeamError(f"A roster can hold up to {ROSTER_MAX} players, and {team['name']} already has {len(current)}.")
    seen = {str(m["user_id"]) for m in current}
    jerseys = {m.get("jersey") for m in current if m.get("jersey") is not None}
    errors = []
    for i, p in enumerate(players):
        uid = str(p.get("user_id") or "").strip()
        if uid in seen:
            errors.append(f"Row {i + 1}: that player is already on this roster.")
        elif p.get("jersey") is not None and p["jersey"] in jerseys:
            errors.append(f"Row {i + 1}: jersey #{p['jersey']} is already taken.")
        elif p.get("status") not in ROSTER_STATUSES:
            errors.append(f"Row {i + 1}: status must be one of: " + ", ".join(ROSTER_STATUSES))
        seen.add(uid)
        jerseys.add(p.get("jersey"))
    if errors:
        raise TeamError(" ".join(errors))
    return [await add_roster_member(db, g, team, p["user_id"], p.get("username") or "", p.get("position") or "", p.get("jersey"),
                                    p.get("status") or "active", p.get("notes") or "", actor) for p in players]


async def remove_roster_member(db, guild_id, team_id: int, user_id: str) -> bool:
    r = await db.team_members.delete_one({"guild_id": str(guild_id), "team_id": team_id, "user_id": str(user_id)})
    return r.deleted_count > 0


async def list_roster(db, guild_id, team_id: int) -> list:
    rows = await db.team_members.find({"guild_id": str(guild_id), "team_id": team_id}).to_list(None)
    order = {s: i for i, s in enumerate(ROSTER_STATUSES)}
    return sorted(rows, key=lambda m: (order.get(m.get("status"), 9), m.get("jersey") if m.get("jersey") is not None else 10_000, m.get("username", "").lower()))


async def teams_of_user(db, guild_id, user_id: str) -> list:
    return await db.team_members.find({"guild_id": str(guild_id), "user_id": str(user_id)}).to_list(None)


# ───────── matches ─────────
async def log_match(db, guild_id, home: dict, away: dict | None, home_score: int, away_score: int, competition: str | None = None,
                    played_at: datetime | None = None, notes: str = "", apply_record: bool = False, actor="",
                    away_name: str = "") -> tuple[dict, list]:
    """Store a match; optionally add the win/loss/tie to the teams involved. `away` is None when the opponent isn't a
    team in the dashboard — `away_name` then names it, and a record update can only touch the home team. Returns
    (match doc, [updated team docs])."""
    if away and home["team_id"] == away["team_id"]:
        raise TeamError("A team can't play itself.")
    opponent = away["name"] if away else (away_name or "").strip()
    g = str(guild_id)
    doc = {"guild_id": g, "competition": competition or home.get("competition"), "home_team_id": home["team_id"],
           "away_team_id": away["team_id"] if away else None,
           "home_score": home_score, "away_score": away_score, "played_at": played_at or _now(), "notes": notes, "record_applied": False,
           "created_at": _now(), "created_by": str(actor)}
    if not away:
        doc["away_name"] = opponent
    doc["_id"] = (await db.matches.insert_one(doc)).inserted_id
    updated = []
    if apply_record:
        h, a = match_outcome(home_score, away_score)
        label = lambda f: "tie" if f == "ties" else f[:-1]  # noqa: E731
        note = f"Match: {home['name']} {home_score}–{away_score} {opponent}"
        updated.append(await bump(db, g, home, h, 1, f"match {label(h)}", actor, note))
        if away:
            updated.append(await bump(db, g, away, a, 1, f"match {label(a)}", actor, note))
        await db.matches.update_one({"_id": doc["_id"]}, {"$set": {"record_applied": True}})
        doc["record_applied"] = True
    return doc, updated
