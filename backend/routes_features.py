"""backend/routes_features.py — records, leaderboard, activity, member stats, backups, locks, verification, exports."""

import base64
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from shared.records import fmt_pct, sort_teams
from shared.resources import F, RESOURCES, clean_value
from backend import audit, bridge
from backend.db import coll
from backend.deps import Ctx, guild_ctx
from database import teams as T
from backend.db import db as handle
from backend.resources import bump_team, channel_ok, finish, notify_records, post_result, role_ok
from backend.util import now, oid, ser
from shared.fusion import SPORTS, parse_report

router = APIRouter(prefix="/api/guilds/{gid}", tags=["features"])


# ───────────── team records ─────────────
@router.post("/teams/{team_id}/record")
async def change_record(team_id: int, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """action: win | loss | tie (+count) · set (wins/losses/ties) · reset. Every change writes record history."""
    ctx.require("staff")
    team = await coll("teams").find_one({"guild_id": ctx.guild_id, "team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    action = body.get("action")
    try:
        count = max(1, min(int(body.get("count", 1)), 50))
    except (TypeError, ValueError):
        raise HTTPException(422, "count must be a number")
    if action in ("win", "loss", "tie"):
        field = {"win": "wins", "loss": "losses", "tie": "ties"}[action]
        new = await bump_team(ctx, team, field, count, f"+{count} {action}", body.get("note", "")[:200])
    elif action in ("set", "reset"):
        vals = {"wins": 0, "losses": 0, "ties": 0} if action == "reset" else {}
        if action == "set":
            for k in ("wins", "losses", "ties"):
                try:
                    vals[k] = int(body[k])
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(422, f"{k} must be a number")
                if not 0 <= vals[k] <= 9999:
                    raise HTTPException(422, f"{k} must be 0–9999")
        new = await T.set_record(handle(), ctx.guild_id, team, vals["wins"], vals["losses"], vals["ties"], action, ctx.user["user_id"], body.get("note", "")[:200])
    else:
        raise HTTPException(422, "Unknown action")
    await notify_records(ctx, team_id)
    await audit.log(ctx, f"record.{action}", team["name"])
    new["record"], new["win_pct"] = f"{new['wins']}-{new['losses']}-{new['ties']}", fmt_pct(new["wins"], new["losses"], new["ties"])
    return {"team": ser(new)}


ROSTER_ROW = [F("user_id", "snowflake", "Discord ID", required=True), F("username", max=80), F("position", max=40),
              F("jersey", "int", min=0, max=999), F("status", "select", choices=list(T.ROSTER_STATUSES), default="active")]


@router.post("/teams/{team_id}/roster")
async def add_roster(team_id: int, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Build a whole roster in one go (Rosters → New roster). The batch is all-or-nothing: one bad row adds nobody."""
    ctx.require("staff")
    team = await coll("teams").find_one({"guild_id": ctx.guild_id, "team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    rows = body.get("players")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(422, {"errors": {"players": "Add at least one player."}})
    if len(rows) > T.ROSTER_MAX:
        raise HTTPException(422, {"errors": {"players": f"A roster holds up to {T.ROSTER_MAX} players."}})
    players = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise HTTPException(422, {"errors": {"players": f"Row {i + 1} is invalid."}})
        try:
            players.append({f["name"]: clean_value(f, row.get(f["name"])) for f in ROSTER_ROW})
        except ValueError as e:
            raise HTTPException(422, {"errors": {"players": f"Row {i + 1}: {e}"}})
    try:
        docs = await T.add_roster_batch(handle(), ctx.guild_id, team, players, ctx.user["user_id"])
    except T.TeamError as e:
        raise HTTPException(422, {"errors": {"players": str(e)}})
    await audit.log(ctx, "roster.create", f"{team['name']} ({len(docs)} players)")
    return {"items": [ser(d) for d in docs], "added": len(docs)}


@router.get("/teams/{team_id}/history")
async def record_history(team_id: int, ctx: Ctx = Depends(guild_ctx)):
    rows = await coll("team_history").find({"guild_id": ctx.guild_id, "team_id": team_id}).sort("timestamp", -1).limit(50).to_list(None)
    return {"items": ser(rows)}


def _note_fields(body: dict) -> dict:
    """Validate a timeline note; returns {note, timestamp?} where timestamp defaults to now."""
    note = str(body.get("note") or "").strip()
    if not note:
        raise HTTPException(422, {"errors": {"note": "Write something first."}})
    if len(note) > 500:
        raise HTTPException(422, {"errors": {"note": "Max 500 characters."}})
    out = {"note": note, "timestamp": now()}
    if body.get("happened_at"):
        try:
            ts = datetime.fromisoformat(str(body["happened_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, {"errors": {"happened_at": "Use a valid date and time."}})
        out["timestamp"] = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return out


async def _note_or_404(team_id: int, item_id: str, ctx: Ctx) -> dict:
    doc = await coll("team_history").find_one({"_id": oid(item_id), "guild_id": ctx.guild_id, "team_id": team_id})
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("action") != "note":
        raise HTTPException(409, "Only timeline notes can be changed — record history stays as it happened.")
    return doc


@router.post("/teams/{team_id}/history")
async def add_team_note(team_id: int, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Add a dated note to a team's timeline (Teams → History) — anything that happened, on the day it happened."""
    ctx.require("staff")
    team = await coll("teams").find_one({"guild_id": ctx.guild_id, "team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    fields = _note_fields(body)
    doc = {"guild_id": ctx.guild_id, "team_id": team_id, "team_name": team["name"], "action": "note",
           "by": ctx.user["user_id"], "by_name": ctx.user.get("username") or "", "created_at": now(), **fields}
    doc["_id"] = (await coll("team_history").insert_one(doc)).inserted_id
    await audit.log(ctx, "team.note", f"{team['name']}: {fields['note'][:60]}")
    return {"item": ser(doc)}


@router.patch("/teams/{team_id}/history/{item_id}")
async def edit_team_note(team_id: int, item_id: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Fix a note's text or date (record history rows are never editable)."""
    ctx.require("staff")
    doc = await _note_or_404(team_id, item_id, ctx)
    updates = {**_note_fields(body), "edited_at": now(), "edited_by": ctx.user["user_id"]}
    if not body.get("happened_at"):
        updates.pop("timestamp", None)  # keep the note's date unless a new one is given
    await coll("team_history").update_one({"_id": doc["_id"]}, {"$set": updates})
    await audit.log(ctx, "team.note.edit", f"{doc.get('team_name', '')}: {updates['note'][:60]}")
    return {"item": ser(await coll("team_history").find_one({"_id": doc["_id"]}))}


@router.delete("/teams/{team_id}/history/{item_id}")
async def delete_team_note(team_id: int, item_id: str, ctx: Ctx = Depends(guild_ctx)):
    ctx.require("staff")
    doc = await _note_or_404(team_id, item_id, ctx)
    await coll("team_history").delete_one({"_id": doc["_id"]})
    await audit.log(ctx, "team.note.delete", f"{doc.get('team_name', '')}: {str(doc.get('note') or '')[:60]}")
    return {"ok": True}


def _report_team(name: str, teams: list) -> dict | None:
    """Match a report's team name (often a full college name) to a dashboard team, exact first, then unique overlap."""
    key = (name or "").strip().lower()
    if not key:
        return None
    exact = [t for t in teams if (t.get("name_lower") or t.get("name", "").lower()) == key]
    if len(exact) == 1:
        return exact[0]
    near = [t for t in teams if t.get("name_lower") and (key in t["name_lower"] or t["name_lower"] in key)]
    return near[0] if len(near) == 1 else None


def _ff_sides(game: dict, teams: list, my_team: str = "", flip: bool = False) -> dict:
    """Resolve a parsed report's two sides to dashboard teams. The manager's team (named by `my_team`, otherwise the
    first side that is a dashboard team) becomes the match's home side; `flip` swaps the scores, for the short export
    that can't say which score belongs to which team."""
    side = game["score"]
    sides = [{"name": side["home"], "team": _report_team(side["home"], teams), "score": side["home_score"]},
             {"name": side["away"], "team": _report_team(side["away"], teams), "score": side["away_score"]}]
    if not (sides[0]["team"] or sides[1]["team"]):
        raise HTTPException(422, {"errors": {"report": f"Neither “{sides[0]['name']}” nor “{sides[1]['name']}” matches a team in the dashboard — add one of them first."}})
    key = (my_team or "").strip().lower()
    mine = next((s for s in sides if key and s["name"].lower() == key), None) or (sides[0] if sides[0]["team"] else sides[1])
    if not mine["team"]:
        raise HTTPException(422, {"errors": {"report": f"“{mine['name']}” isn't a team in the dashboard — pick the team this match is for."}})
    theirs = sides[1] if mine is sides[0] else sides[0]
    hs, as_ = mine["score"], theirs["score"]
    if flip:
        hs, as_ = as_, hs
    return {"home": mine["team"], "away": theirs["team"], "hs": hs, "as": as_,
            "away_name": "" if theirs["team"] else theirs["name"], "sides": sides}


SHOT_MAX = 2_500_000      # base64 characters per image, after the browser shrinks the screenshot
SHOT_MAX_COUNT = 8        # images one match can carry (a box score may need more than one screenshot)
_SHOT = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,(.+)$", re.S)


def _shot(body: dict) -> dict | None:
    """The box-score screenshot the manager attached (a data URL), or None."""
    raw = str(body.get("image") or "").strip()
    if not raw:
        return None
    m = _SHOT.match(raw)
    if not m:
        raise HTTPException(422, {"errors": {"image": "Attach a PNG, JPEG or WebP screenshot."}})
    data = re.sub(r"\s+", "", m.group(2))
    if len(data) > SHOT_MAX:
        raise HTTPException(422, {"errors": {"image": "That screenshot is too big — crop it or take a smaller one."}})
    try:
        base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, {"errors": {"image": "That screenshot couldn't be read — try a PNG or JPEG."}})
    return {"mime": m.group(1), "data": data, "size": len(data)}


def _stamped(ctx: Ctx, shot: dict) -> dict:
    return {"mime": shot["mime"], "data": shot["data"], "size": shot["size"],
            "created_at": now(), "created_by": ctx.user["user_id"]}


def _shot_images(doc: dict | None) -> list:
    """Every image stored with a match. Documents written when a match could hold only one are read as a list of one."""
    if not doc:
        return []
    images = [i for i in (doc.get("images") or []) if isinstance(i, dict) and i.get("data")]
    if not images and doc.get("data"):
        images = [{"mime": doc.get("mime") or "image/jpeg", "data": doc["data"], "size": doc.get("size") or len(doc["data"])}]
    return images


def _image_url(image: dict) -> str:
    return f"data:{image.get('mime') or 'image/jpeg'};base64,{image['data']}"


async def _save_images(ctx: Ctx, match_id, images: list) -> None:
    """One document per match holding all its screenshots. The first image is mirrored in the older fields so a bot
    still on the previous release keeps posting it."""
    first = images[0]
    await coll("match_shots").replace_one(
        {"guild_id": ctx.guild_id, "match_id": match_id},
        {"guild_id": ctx.guild_id, "match_id": match_id, "mime": first["mime"], "data": first["data"], "size": first["size"],
         "images": images, "count": len(images),   # count lets the table show “Screenshots (3)” without reading the data
         "created_at": now(), "created_by": ctx.user["user_id"]}, upsert=True)


@router.get("/matches/{rid}/shot")
async def match_shot(rid: str, ctx: Ctx = Depends(guild_ctx)):
    """One match's first screenshot, as a data URL."""
    doc = await coll("match_shots").find_one({"guild_id": ctx.guild_id, "match_id": oid(rid)})
    images = _shot_images(doc)
    if not images:
        raise HTTPException(404, "This match has no screenshot.")
    return {"data_url": _image_url(images[0]), "size": images[0].get("size")}


@router.get("/matches/{rid}/shots")
async def match_shots(rid: str, ctx: Ctx = Depends(guild_ctx)):
    """Every screenshot stored with a match, for the dashboard's screenshot viewer."""
    doc = await coll("match_shots").find_one({"guild_id": ctx.guild_id, "match_id": oid(rid)})
    images = _shot_images(doc)
    if not images:
        raise HTTPException(404, "This match has no screenshot.")
    return {"count": len(images), "images": [{"data_url": _image_url(i), "size": i.get("size")} for i in images]}


@router.post("/matches/{rid}/shots")
async def add_match_shots(rid: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Attach more box-score screenshots to a match that already exists (the import dialog only covers new matches)."""
    ctx.require("staff")
    mid = oid(rid)
    if not await coll("matches").find_one({"_id": mid, "guild_id": ctx.guild_id}, {"_id": 1}):
        raise HTTPException(404, "That match no longer exists.")
    raw = body.get("images")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise HTTPException(422, {"errors": {"image": "Pick at least one screenshot."}})
    if len(raw) > SHOT_MAX_COUNT:
        raise HTTPException(422, {"errors": {"image": f"Add at most {SHOT_MAX_COUNT} screenshots at a time."}})
    added = [_stamped(ctx, _shot({"image": one})) for one in raw]
    images = _shot_images(await coll("match_shots").find_one({"guild_id": ctx.guild_id, "match_id": mid}))
    if len(images) + len(added) > SHOT_MAX_COUNT:
        raise HTTPException(422, {"errors": {"image": f"This match already has {len(images)} screenshot(s); {SHOT_MAX_COUNT} is the limit."}})
    await _save_images(ctx, mid, images + added)
    await audit.log(ctx, "match.screenshots", f"{len(added)} added to match {rid}")
    total = len(images) + len(added)
    return {"count": total, "notice": f"Added {len(added)} screenshot{'s' if len(added) != 1 else ''} — {total} attached."}


@router.post("/matches/{rid}/stats")
async def attach_stats(rid: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Add (or replace) a Football Fusion stat sheet on a match that already exists — for matches logged with only a
    score. The match keeps its orientation: the report side that is the match's home team supplies the home score."""
    ctx.require("staff")
    mid = oid(rid)
    doc = await coll("matches").find_one({"_id": mid, "guild_id": ctx.guild_id})
    if not doc:
        raise HTTPException(404, "That match no longer exists.")
    try:
        game = parse_report(str(body.get("report") or ""))
    except ValueError as e:
        raise HTTPException(422, {"errors": {"report": str(e)}})
    game["sport"] = "ff"
    teams = await coll("teams").find({"guild_id": ctx.guild_id}).to_list(None)
    names = {t["team_id"]: t["name"] for t in teams}
    res = _ff_sides(game, teams, str(body.get("my_team") or ""), bool(body.get("flip")))
    sides = res["sides"]
    mine = next((s for s in sides if s["team"] and s["team"]["team_id"] == doc.get("home_team_id")), None)
    if not mine:  # the report doesn't name the match's team — fall back to the side that is a dashboard team
        mine = next((s for s in sides if s["team"]), None)
    if not mine:
        raise HTTPException(422, {"errors": {"report": "Neither team in the report is a team in the dashboard — add them first."}})
    theirs = sides[1] if mine is sides[0] else sides[0]
    hs, as_ = mine["score"], theirs["score"]
    home_display = names.get(doc.get("home_team_id")) or mine["name"]
    away_display = names.get(doc.get("away_team_id")) or doc.get("away_name") or theirs["name"]
    game["score"] = {"home": home_display, "home_score": hs, "away": away_display, "away_score": as_}
    change = {"ff": game}
    note = ""
    if (doc.get("home_score"), doc.get("away_score")) != (hs, as_):
        note = f" Score updated from {doc.get('home_score')}–{doc.get('away_score')}."
        change.update({"home_score": hs, "away_score": as_})
    home_team = next((t for t in teams if t["team_id"] == doc.get("home_team_id")), None)
    if not doc.get("competition") and home_team and home_team.get("competition"):
        change["competition"] = home_team["competition"]
    await coll("matches").update_one({"_id": doc["_id"]}, {"$set": {**change, "updated_at": now(), "updated_by": ctx.user["user_id"]}})
    await audit.log(ctx, "match.stats", f"{home_display} {hs}–{as_} {away_display}")
    new = await coll("matches").find_one({"_id": doc["_id"]})
    return {"item": ser(new), "notice": f"Stat sheet added: {home_display} {hs}–{as_} {away_display}.{note}"}


@router.post("/matches/import")
async def import_match(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Import a Football Fusion report, or log any sport's match from a box-score screenshot."""
    ctx.require("staff")
    sport = str(body.get("sport") or "ff").strip().lower()
    if sport not in SPORTS:
        raise HTTPException(422, {"errors": {"sport": "Pick Football Fusion, Practical Basketball or Ultimate Football."}})
    shot = _shot(body)
    teams = await coll("teams").find({"guild_id": ctx.guild_id}).to_list(None)
    picked = now()
    if body.get("played_at"):
        try:
            picked = datetime.fromisoformat(str(body["played_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, {"errors": {"played_at": "Use a valid date and time."}})
        picked = picked.astimezone(timezone.utc) if picked.tzinfo else picked.replace(tzinfo=timezone.utc)

    if sport == "ff":
        try:
            game = parse_report(str(body.get("report") or ""))
        except ValueError as e:
            raise HTTPException(422, {"errors": {"report": str(e)}})
        game["sport"] = "ff"
        sides = _ff_sides(game, teams, str(body.get("my_team") or ""), bool(body.get("flip")))
        home, away, hs, as_, away_name = sides["home"], sides["away"], sides["hs"], sides["as"], sides["away_name"]
        competition, label = body.get("competition") or home.get("competition"), game.get("game") or SPORTS["ff"]
        game["score"] = {"home": home["name"], "home_score": hs,
                         "away": away["name"] if away else away_name, "away_score": as_}
    else:
        # No OCR: the screenshot is the stat sheet, so the manager types the score and picks the teams.
        if not shot:
            raise HTTPException(422, {"errors": {"image": "Attach the box-score screenshot — it's stored as this match's stat sheet."}})
        home = next((t for t in teams if str(t.get("team_id")) == str(body.get("home_team_id"))), None)
        if not home:
            raise HTTPException(422, {"errors": {"home_team_id": "Pick the team the match belongs to."}})
        away = next((t for t in teams if str(t.get("team_id")) == str(body.get("away_team_id"))), None) if body.get("away_team_id") else None
        try:
            hs, as_ = int(body.get("home_score")), int(body.get("away_score"))
        except (TypeError, ValueError):
            hs = as_ = -1
        if not (0 <= hs <= 9999 and 0 <= as_ <= 9999):
            raise HTTPException(422, {"errors": {"home_score": "Enter both teams' scores."}})
        away_name = "" if away else str(body.get("away_name") or "").strip()[:50]
        if not away and not away_name:
            raise HTTPException(422, {"errors": {"away_name": "Pick the opponent or type its name."}})
        competition = body.get("competition") or home.get("competition")
        label = SPORTS[sport]
        game = {"sport": sport, "score": {"home": home["name"], "home_score": hs,
                                          "away": away["name"] if away else away_name, "away_score": as_}}

    try:
        doc, updated = await T.log_match(handle(), ctx.guild_id, home, away, hs, as_, competition,
                                         picked, "", bool(body.get("apply_record")), ctx.user["user_id"], away_name=away_name, ff=game)
    except T.TeamError as e:
        raise HTTPException(422, {"errors": {"report": str(e)}})
    if shot:
        await _save_images(ctx, doc["_id"], [_stamped(ctx, shot)])
    for t in updated:
        await notify_records(ctx, t["team_id"])
    opponent = away["name"] if away else away_name
    await audit.log(ctx, "match.import", f"{home['name']} {hs}–{as_} {opponent} ({label})")
    await post_result(ctx, doc["_id"])  # results channel, if the server has one — never blocks on Discord
    return {"item": ser(doc), "notice": f"Imported {label}: {home['name']} {hs}–{as_} {opponent}" + (" — screenshot attached." if shot else ".")}


@router.post("/matches/ff/preview")
async def ff_preview(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    """Parse an FF report without saving anything, so the import dialog can show “Will submit X vs Y” and let the
    manager fix which team is theirs and flip the score before the match is written."""
    ctx.require("staff")
    try:
        game = parse_report(str(body.get("report") or ""))
    except ValueError as e:
        raise HTTPException(422, {"errors": {"report": str(e)}})
    teams = await coll("teams").find({"guild_id": ctx.guild_id}).to_list(None)
    res = _ff_sides(game, teams, str(body.get("my_team") or ""), bool(body.get("flip")))
    home = res["home"]
    return {"game": game.get("game"), "competition": home.get("competition"),
            "home": home["name"], "home_team_id": home["team_id"],
            "away": res["away"]["name"] if res["away"] else res["away_name"],
            "home_score": res["hs"], "away_score": res["as"],
            "sides": [{"name": s["name"], "team_id": s["team"]["team_id"] if s["team"] else None,
                       "team_name": s["team"]["name"] if s["team"] else None, "score": s["score"]} for s in res["sides"]],
            "sides_confirmed": (game["score"].get("sides") or "report") != "unconfirmed"}


@router.get("/ff")
async def ff_stats(stat: str = "", competition: str = "", ctx: Ctx = Depends(guild_ctx)):
    """Football Fusion only: per-player stat totals from parsed game reports, plus the recent FF games."""
    q = {"guild_id": ctx.guild_id, "ff": {"$type": "object"},
         "$or": [{"ff.sport": {"$exists": False}}, {"ff.sport": "ff"}]}
    if competition:
        q["competition"] = {"$regex": f"^{re.escape(competition)}$", "$options": "i"}  # matches store no lowercase copy
    matches = await coll("matches").find(q).sort("played_at", -1).limit(500).to_list(None)
    teams = {t["team_id"]: t.get("name") for t in await coll("teams").find({"guild_id": ctx.guild_id}).to_list(None)}
    roster: dict = {}
    for m in await coll("team_members").find({"guild_id": ctx.guild_id}).to_list(None):
        if str(m.get("user_id") or "") not in roster:
            roster[str(m.get("user_id"))] = teams.get(m.get("team_id"))

    rows: dict = {}
    seen: dict = {}
    for match in matches:
        for p in (match.get("ff") or {}).get("players") or []:
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            who = str(p.get("user_id")) if p.get("user_id") else "name:" + name.lower()
            positions = p.get("positions") or {}
            if not positions and isinstance(p.get("stats"), dict):
                positions = {"—": p["stats"]}
            for pos, stats in positions.items():
                if not isinstance(stats, dict):
                    continue
                row = rows.setdefault((who, pos), {"name": name, "user_id": p.get("user_id"), "position": pos,
                                                   "team": roster.get(who), "games": set(), "totals": {}})
                row["games"].add(str(match["_id"]))
                for k, v in stats.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        row["totals"][k] = row["totals"].get(k, 0) + v
                        seen[k] = seen.get(k, 0) + 1

    players = []
    for row in rows.values():
        gp = len(row["games"])
        players.append({"name": row["name"], "user_id": row["user_id"], "position": row["position"], "team": row["team"],
                        "games": gp, "totals": row["totals"],
                        "averages": {k: round(v / gp, 1) for k, v in row["totals"].items()} if gp else {}})
    keys = sorted(seen)
    chosen = stat if stat in seen else (max(keys, key=lambda k: (seen[k], k)) if keys else "")
    if chosen:
        players.sort(key=lambda r: (-r["totals"].get(chosen, 0), r["name"].lower()))

    games = []
    for m in matches[:20]:
        ff = m.get("ff") or {}
        side = ff.get("score") or {}
        games.append({"id": str(m["_id"]), "played_at": m.get("played_at"), "competition": m.get("competition"),
                      "home": teams.get(m.get("home_team_id")) or side.get("home") or "?",
                      "away": teams.get(m.get("away_team_id")) or m.get("away_name") or side.get("away") or "?",
                      "home_score": m.get("home_score"), "away_score": m.get("away_score"), "ff": ff})
    return {"stats": keys, "stat": chosen, "players": players[:100], "games": games}


@router.get("/leaderboard")
async def leaderboard(sort: str = "wins", competition: str | None = None, division: str | None = None, ctx: Ctx = Depends(guild_ctx)):
    q = {"guild_id": ctx.guild_id}
    if competition:
        q["competition_lower"] = competition.lower()
    if division:
        q["division"] = division
    teams = sort_teams(await coll("teams").find(q).to_list(None), sort if sort in ("wins", "percentage", "losses") else "wins")
    rows = [{"rank": i + 1, "team_id": t["team_id"], "name": t["name"], "logo": t.get("logo"), "competition": t.get("competition"),
             "division": t.get("division"), "wins": t["wins"], "losses": t["losses"], "ties": t["ties"], "pct": fmt_pct(t["wins"], t["losses"], t["ties"])}
            for i, t in enumerate(teams)]
    return {"items": rows}


# ───────────── activity / member stats / poll archive ─────────────
def _day(d) -> str:
    return d.strftime("%Y-%m-%d")


@router.get("/activity")
async def activity(period: str = "week", ctx: Ctx = Depends(guild_ctx)):
    match = {"guild_id": ctx.guild_id}
    if period in ("day", "week"):
        match["date"] = {"$gte": _day(now() - timedelta(days=0 if period == "day" else 6))}
    top = await (await coll("activity_daily").aggregate([
        {"$match": match}, {"$group": {"_id": "$user_id", "count": {"$sum": "$count"}, "name": {"$max": "$user_name"}}},
        {"$sort": {"count": -1}}, {"$limit": 25}])).to_list(None)
    daily = await (await coll("activity_daily").aggregate([
        {"$match": {"guild_id": ctx.guild_id, "date": {"$gte": _day(now() - timedelta(days=13))}}},
        {"$group": {"_id": "$date", "count": {"$sum": "$count"}}}, {"$sort": {"_id": 1}}])).to_list(None)
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    return {"enabled": bool(cfg.get("activity_enabled")), "top": [{"user_id": r["_id"], "name": r.get("name") or r["_id"], "count": r["count"]} for r in top],
            "daily": [{"date": r["_id"], "count": r["count"]} for r in daily]}


@router.get("/memberstats")
async def memberstats(days: int = 30, ctx: Ctx = Depends(guild_ctx)):
    days = max(3, min(days, 90))
    start = _day(now() - timedelta(days=days - 1))
    snaps = await coll("member_snapshots").find({"guild_id": ctx.guild_id, "date": {"$gte": start}}).sort("date", 1).to_list(None)
    daily = await coll("member_daily").find({"guild_id": ctx.guild_id, "date": {"$gte": start}}).sort("date", 1).to_list(None)
    return {"snapshots": [{"date": s["date"], "total": s["total"], "humans": s.get("humans"), "bots": s.get("bots")} for s in snaps],
            "daily": [{"date": d["date"], "joined": d.get("joined", 0), "left": d.get("left", 0)} for d in daily],
            "joined": sum(d.get("joined", 0) for d in daily), "left": sum(d.get("left", 0) for d in daily)}


@router.get("/poll-archive")
async def poll_archive(page: int = 1, ctx: Ctx = Depends(guild_ctx)):
    per = 10
    q = {"guild_id": ctx.guild_id, "status": "closed"}
    polls = await coll("polls").find(q).sort("poll_id", -1).skip((max(page, 1) - 1) * per).limit(per).to_list(None)
    ids = [p["poll_id"] for p in polls]
    votes = await coll("poll_votes").find({"guild_id": ctx.guild_id, "poll_id": {"$in": ids}}).to_list(None) if ids else []
    out = []
    for p in polls:
        counts = [0] * len(p["options"])
        mine = [v for v in votes if v["poll_id"] == p["poll_id"]]
        for v in mine:
            for i in v.get("choices", []):
                if 0 <= i < len(counts):
                    counts[i] += 1
        out.append({"id": str(p["_id"]), "poll_id": p["poll_id"], "question": p["question"], "closed_at": ser(p.get("closed_at") or p.get("ends_at")),
                    "voters": len(mine), "anonymous": p["anonymous"], "results": [{"option": o, "votes": counts[i]} for i, o in enumerate(p["options"])]})
    return {"items": out, "total": await coll("polls").count_documents(q)}


# ───────────── moderation: channel locks ─────────────
@router.get("/locks")
async def locks(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("staff")
    return {"items": ser(await coll("channel_locks").find({"guild_id": ctx.guild_id}).to_list(None))}


@router.post("/channels/{channel_id}/lock")
async def lock_channel(channel_id: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    channel_ok(ctx, channel_id, "channel")
    locked = bool(body.get("locked"))
    r = await bridge.run_job(ctx.guild_id, "channel_lock", {"channel_id": channel_id, "locked": locked, "reason": str(body.get("reason") or "Dashboard")[:200],
                                                            "by": ctx.user["username"]}, ctx.user["user_id"])
    await audit.log(ctx, "channel.lock" if locked else "channel.unlock", channel_id)
    return finish({"ok": True}, r)


# ───────────── backups ─────────────
@router.get("/backups")
async def backups(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    rows = await coll("backups").find({"guild_id": ctx.guild_id}, {"data": 0}).sort("created_at", -1).to_list(None)
    return {"items": ser(rows)}


@router.post("/backups")
async def backup_create(body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    name = str(body.get("name", "")).strip()[:50]
    if not name:
        raise HTTPException(422, {"errors": {"name": "Required"}})
    if await coll("backups").find_one({"guild_id": ctx.guild_id, "name_lower": name.lower()}):
        raise HTTPException(422, {"errors": {"name": "A backup with that name exists."}})
    r = await bridge.run_job(ctx.guild_id, "backup_create", {"name": name, "by": ctx.user["user_id"]}, ctx.user["user_id"], wait=25)
    await audit.log(ctx, "backup.create", name)
    return finish({"ok": True}, r)


@router.post("/backups/{name}/restore")
async def backup_restore(name: str, body: dict = Body(...), ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    mode = body.get("mode")
    if mode not in ("missing", "full"):
        raise HTTPException(422, "mode must be 'missing' or 'full'")
    if mode == "full" and body.get("confirm") != "RESTORE":
        raise HTTPException(422, {"errors": {"confirm": "Type RESTORE to confirm a full (destructive) restore."}})
    if mode == "full" and ctx.user["user_id"] != ctx.guild.get("owner_id"):
        raise HTTPException(403, "Only the server owner can run a full restore from the dashboard.")
    if not await coll("backups").find_one({"guild_id": ctx.guild_id, "name_lower": name.lower()}, {"_id": 1}):
        raise HTTPException(404, "Backup not found")
    r = await bridge.run_job(ctx.guild_id, "backup_restore", {"name": name, "mode": mode, "by": ctx.user["user_id"]}, ctx.user["user_id"], wait=60)
    await audit.log(ctx, f"backup.restore.{mode}", name)
    return finish({"ok": True, "result": r.get("result")}, r)


@router.delete("/backups/{name}")
async def backup_delete(name: str, ctx: Ctx = Depends(guild_ctx)):
    ctx.require("admin")
    res = await coll("backups").delete_one({"guild_id": ctx.guild_id, "name_lower": name.lower()})
    if not res.deleted_count:
        raise HTTPException(404, "Backup not found")
    await audit.log(ctx, "backup.delete", name)
    return {"ok": True}


# ───────────── verification (a button message that gives a role) ─────────────
@router.post("/verification/publish")
async def verification_publish(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    cfg = await coll("guild_config").find_one({"guild_id": ctx.guild_id}) or {}
    if not cfg.get("verify_role") or not cfg.get("verify_channel"):
        raise HTTPException(422, "Set a verification role and channel in Settings first.")
    channel_ok(ctx, str(cfg["verify_channel"]), "verify_channel")
    role_ok(ctx, str(cfg["verify_role"]), "verify_role")
    r = await bridge.run_job(ctx.guild_id, "publish", {"kind": "verification", "ref": 1}, ctx.user["user_id"])
    await audit.log(ctx, "verification.publish")
    return finish({"ok": True}, r)


# ───────────── data export ─────────────
EXPORTABLE = sorted({s["collection"] for s in RESOURCES.values() if s["collection"] not in ("webhooks",)} | {"team_history", "poll_votes", "giveaway_entries", "member_snapshots", "member_daily"})
SECRET_FIELDS = {"url_enc", "token_hash", "access_token_enc"}


@router.get("/export")
async def export(collections: str = "", fmt: str = "json", ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    wanted = [c for c in collections.split(",") if c] or EXPORTABLE
    bad = [c for c in wanted if c not in EXPORTABLE]
    if bad:
        raise HTTPException(422, f"Not exportable: {', '.join(bad)}")
    data = {}
    for c in wanted:
        rows = await coll(c).find({"guild_id": ctx.guild_id}).limit(20000).to_list(None)  # bounded: free-tier friendly
        data[c] = [{k: v for k, v in ser(r).items() if k not in SECRET_FIELDS} for r in rows]
    await audit.log(ctx, "export", ",".join(wanted), {"format": fmt})
    stamp = now().strftime("%Y%m%d-%H%M")
    if fmt == "csv":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for c, rows in data.items():
                cols = sorted({k for r in rows for k in r})
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
                z.writestr(f"{c}.csv", out.getvalue())
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="teamcore-{ctx.guild_id}-{stamp}.zip"'})
    return Response(json.dumps(data, indent=1), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="teamcore-{ctx.guild_id}-{stamp}.json"'})


@router.get("/export/options")
async def export_options(ctx: Ctx = Depends(guild_ctx)):
    ctx.require("manager")
    return {"collections": EXPORTABLE}
