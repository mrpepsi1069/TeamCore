"""database.py — Async MongoDB wrapper using Motor."""

import os
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId


_client: AsyncIOMotorClient | None = None
_db = None


async def initialize() -> bool:
    """Connect to MongoDB. Returns True on success.

    On failure `_db` is reset to None so every helper below degrades cleanly
    (and the bot can tell users the database is unavailable) instead of each
    call blocking for the full server-selection timeout and then raising.
    """
    global _client, _db
    url = os.getenv("DATABASE_URL")
    if not url:
        print("❌ DATABASE_URL is not set")
        print("⚠️  Continuing without database")
        _client = _db = None
        return False

    kwargs = {"tz_aware": True, "serverSelectionTimeoutMS": 5000}
    # Only relax certificate validation if explicitly asked to (e.g. a host with a
    # broken CA bundle). The old code always disabled it, which allows MITM attacks.
    if os.getenv("MONGO_ALLOW_INVALID_CERTS", "").lower() in ("1", "true", "yes"):
        kwargs.update(tls=True, tlsAllowInvalidCertificates=True)
        print("⚠️  MONGO_ALLOW_INVALID_CERTS is on — TLS certificates are NOT being verified")

    try:
        client = AsyncIOMotorClient(url, **kwargs)
        await client.admin.command("ping")  # Motor connects lazily; force a real round-trip
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        print("⚠️  Continuing without database")
        _client = _db = None
        return False

    _client = client
    _db = client["lockerroom_bot"]
    await _create_indexes()
    print("✅ MongoDB connected")
    return True


async def _safe_index(collection, keys, **kwargs) -> None:
    """Create one index; a failure (e.g. pre-existing duplicate data) must not
    prevent the remaining indexes from being created."""
    try:
        await collection.create_index(keys, **kwargs)
    except Exception as e:
        print(f"⚠️  Could not create index on {collection.name} {keys}: {e}")


async def _create_indexes() -> None:
    if _db is None:
        return
    await _safe_index(_db.guilds, "guild_id", unique=True)
    await _safe_index(_db.leagues, [("guild_id", 1), ("league_abbr", 1)], unique=True)
    await _safe_index(_db.lineups, [("guild_id", 1), ("lineup_name", 1)], unique=True)
    await _safe_index(_db.users, "user_id", unique=True)
    await _safe_index(
        _db.championship_rings,
        [("guild_id", 1), ("league_id", 1), ("user_id", 1), ("season", 1)], unique=True,
    )
    await _safe_index(
        _db.awards,
        [("guild_id", 1), ("league_id", 1), ("user_id", 1), ("award_name", 1), ("season", 1)],
        unique=True,
    )
    await _safe_index(_db.gametimes, "message_id")
    await _safe_index(_db.depth_charts, [("guildId", 1), ("abbreviation", 1)], unique=True)
    await _safe_index(_db.contracts, [("guildId", 1), ("userId", 1)], unique=True)
    await _safe_index(_db.contracts, "messageId")
    await _safe_index(_db.activity_checks, [("is_active", 1), ("expires_at", 1)])
    # Matchmaking is pooled across every server, so these are NOT keyed by guild.
    await _safe_index(_db.matchmaking_queue, [("game", 1), ("size", 1), ("created_at", 1)])
    await _safe_index(_db.matchmaking_queue, "user_id")
    await _safe_index(_db.matchmaking_queue, "created_at")


def _check() -> bool:
    return _db is not None


def is_connected() -> bool:
    """True if a database connection is available."""
    return _db is not None


# ──────────────────────────────────────────────
# GUILD
# ──────────────────────────────────────────────

async def create_guild(guild_id: str, guild_name: str):
    if not _check():
        return None
    await _db.guilds.update_one(
        {"guild_id": guild_id},
        {
            "$set": {"guild_name": guild_name},
            "$setOnInsert": {"premium": False, "setup_completed": False},
        },
        upsert=True,
    )
    return await _db.guilds.find_one({"guild_id": guild_id})


async def get_guild(guild_id: str):
    if not _check():
        return None
    return await _db.guilds.find_one({"guild_id": guild_id})


async def update_guild_setup(guild_id: str, completed: bool) -> None:
    if not _check():
        return
    await _db.guilds.update_one(
        {"guild_id": guild_id}, {"$set": {"setup_completed": completed}}
    )


async def set_premium(guild_id: str, is_premium: bool, expires_at=None) -> None:
    if not _check():
        return
    await _db.guilds.update_one(
        {"guild_id": guild_id},
        {"$set": {"premium": is_premium, "premium_expires_at": expires_at}},
    )


# ──────────────────────────────────────────────
# CHANNELS & ROLES
# ──────────────────────────────────────────────

async def set_guild_channel(guild_id: str, channel_type: str, channel_id: str) -> None:
    if not _check():
        return
    await _db.guild_channels.update_one(
        {"guild_id": guild_id, "channel_type": channel_type},
        {"$set": {"channel_id": channel_id}},
        upsert=True,
    )


async def get_guild_channels(guild_id: str) -> dict:
    if not _check():
        return {}
    docs = await _db.guild_channels.find({"guild_id": guild_id}).to_list(None)
    return {d["channel_type"]: d["channel_id"] for d in docs}


async def set_guild_role(guild_id: str, role_type: str, role_id: str) -> None:
    if not _check():
        return
    await _db.guild_roles.update_one(
        {"guild_id": guild_id, "role_type": role_type},
        {"$set": {"role_id": role_id}},
        upsert=True,
    )


async def get_guild_roles(guild_id: str) -> dict:
    if not _check():
        return {}
    docs = await _db.guild_roles.find({"guild_id": guild_id}).to_list(None)
    return {d["role_type"]: d["role_id"] for d in docs}


# ──────────────────────────────────────────────
# GUILD CONFIG (setup)
# ──────────────────────────────────────────────

async def get_guild_config(guild_id: str) -> dict | None:
    if not _check():
        return None
    return await _db.guild_config.find_one({"guild_id": guild_id})


async def set_guild_config(guild_id: str, updates: dict) -> None:
    if not _check():
        return
    set_fields = {k: v for k, v in updates.items() if v is not None}
    unset_fields = {k: "" for k, v in updates.items() if v is None}

    op = {}
    if set_fields:
        op["$set"] = {**set_fields, "guild_id": guild_id}
    if unset_fields:
        op["$unset"] = unset_fields

    if op:
        await _db.guild_config.update_one(
            {"guild_id": guild_id},
            op,
            upsert=True
        )


# ──────────────────────────────────────────────
# LEAGUES
# ──────────────────────────────────────────────

async def create_league(guild_id, name, abbr, signup_link=None, role_id=None):
    if not _check():
        return None
    doc = {
        "guild_id": guild_id,
        "league_name": name,
        "league_abbr": abbr.upper(),
        "signup_link": signup_link,
        "role_id": role_id,
        "is_active": True,
    }
    result = await _db.leagues.insert_one(doc)
    return {**doc, "_id": result.inserted_id}


async def get_leagues(guild_id: str):
    if not _check():
        return []
    return await _db.leagues.find({"guild_id": guild_id, "is_active": True}).to_list(None)


async def get_league_by_abbr(guild_id: str, abbr: str):
    if not _check():
        return None
    return await _db.leagues.find_one({"guild_id": guild_id, "league_abbr": abbr.upper()})


async def get_league_by_role_id(guild_id: str, role_id: str):
    if not _check():
        return None
    return await _db.leagues.find_one({"guild_id": guild_id, "role_id": role_id})


async def delete_league(guild_id: str, abbr: str) -> bool:
    if not _check():
        return False
    r = await _db.leagues.delete_one({"guild_id": guild_id, "league_abbr": abbr.upper()})
    return r.deleted_count > 0


# ──────────────────────────────────────────────
# USERS
# ──────────────────────────────────────────────

async def create_or_update_user(user_id: str, username: str):
    if not _check():
        return None
    await _db.users.update_one(
        {"user_id": user_id},
        {"$set": {"username": username}, "$setOnInsert": {"custom_color": None}},
        upsert=True,
    )
    return await _db.users.find_one({"user_id": user_id})


# ──────────────────────────────────────────────
# AWARDS & RINGS
# ──────────────────────────────────────────────

async def add_championship_ring(guild_id, league_id, user_id, season, opponent, awarded_by):
    if not _check():
        return None
    try:
        doc = {
            "guild_id": guild_id,
            "league_id": str(league_id),
            "user_id": user_id,
            "season": season,
            "opponent": opponent,
            "awarded_by": awarded_by,
        }
        r = await _db.championship_rings.insert_one(doc)
        return {**doc, "_id": r.inserted_id}
    except Exception as e:
        if getattr(e, "code", None) == 11000:
            return None
        raise


async def add_award(guild_id, league_id, user_id, award_name, season, awarded_by):
    if not _check():
        return None
    try:
        doc = {
            "guild_id": str(guild_id),
            "league_id": str(league_id),
            "user_id": str(user_id),
            "award_name": str(award_name),
            "season": str(season),
            "awarded_by": str(awarded_by),
        }
        r = await _db.awards.insert_one(doc)
        return {**doc, "_id": r.inserted_id}
    except Exception as e:
        if getattr(e, "code", None) == 11000:
            return None
        raise


async def get_user_awards(guild_id: str, user_id: str):
    if not _check():
        return None
    user = await _db.users.find_one({"user_id": user_id})
    if not user:
        return None

    # league_id is stored as a *string*, but leagues._id is an ObjectId, so a
    # $lookup on them never matched and every league showed up as "Unknown".
    # Resolve the names in Python instead.
    leagues = await _db.leagues.find({"guild_id": guild_id}).to_list(None)
    league_names = {str(l["_id"]): l["league_name"] for l in leagues}

    rings_raw = await _db.championship_rings.find({"guild_id": guild_id, "user_id": user_id}).to_list(None)
    awards_raw = await _db.awards.find({"guild_id": guild_id, "user_id": user_id}).to_list(None)

    rings = [
        {"league": league_names.get(str(r.get("league_id"))), "season": r.get("season"), "opponent": r.get("opponent")}
        for r in rings_raw
    ]
    awards = [
        {"league": league_names.get(str(r.get("league_id"))), "award": r.get("award_name"), "season": r.get("season")}
        for r in awards_raw
    ]
    return {"username": user["username"], "rings": rings, "awards": awards}


async def remove_award(guild_id, league_id, user_id, award_name, season) -> bool:
    if not _check():
        return False
    r = await _db.awards.delete_one({
        "guild_id": str(guild_id), "league_id": str(league_id), "user_id": str(user_id),
        "award_name": str(award_name), "season": str(season),
    })
    return r.deleted_count > 0


async def remove_championship_ring(guild_id, league_id, user_id, season) -> bool:
    if not _check():
        return False
    r = await _db.championship_rings.delete_one({
        "guild_id": str(guild_id), "league_id": str(league_id),
        "user_id": str(user_id), "season": str(season),
    })
    return r.deleted_count > 0


async def get_leaderboard(guild_id: str, kind: str = "rings", limit: int = 10) -> list:
    """Top players in a server by championship rings or individual awards."""
    if not _check():
        return []
    coll = _db.championship_rings if kind == "rings" else _db.awards
    pipeline = [
        {"$match": {"guild_id": guild_id}},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": limit},
    ]
    return await coll.aggregate(pipeline).to_list(None)


# ──────────────────────────────────────────────
# LINEUPS
# ──────────────────────────────────────────────

async def create_lineup(guild_id, name, description, created_by):
    if not _check():
        return None
    try:
        doc = {"guild_id": guild_id, "lineup_name": name, "description": description, "created_by": created_by, "players": []}
        r = await _db.lineups.insert_one(doc)
        return {**doc, "_id": r.inserted_id}
    except Exception as e:
        if getattr(e, "code", None) == 11000:
            raise ValueError("DUPLICATE_LINEUP")
        raise


async def get_lineups(guild_id: str):
    if not _check():
        return []
    return await _db.lineups.find({"guild_id": guild_id}).to_list(None)


async def get_lineup(guild_id: str, name: str):
    if not _check():
        return None
    lineup = await _db.lineups.find_one({"guild_id": guild_id, "lineup_name": name})
    if not lineup:
        return None
    if lineup.get("players"):
        ids = [p["user_id"] for p in lineup["players"]]
        users = await _db.users.find({"user_id": {"$in": ids}}).to_list(None)
        user_map = {u["user_id"]: u["username"] for u in users}
        for p in lineup["players"]:
            p["username"] = user_map.get(p["user_id"], "Unknown")
    return lineup


async def add_player_to_lineup(lineup_id, user_id: str, position: str):
    if not _check():
        return None
    lineup = await _db.lineups.find_one({"_id": ObjectId(str(lineup_id))})
    if not lineup:
        raise ValueError("Lineup not found")
    players = [p for p in lineup["players"] if p["user_id"] != user_id]
    players.append({"user_id": user_id, "position": position})
    await _db.lineups.update_one({"_id": ObjectId(str(lineup_id))}, {"$set": {"players": players}})
    return {"user_id": user_id, "position": position}


async def remove_player_from_lineup(lineup_id, user_id: str) -> None:
    if not _check():
        return
    lineup = await _db.lineups.find_one({"_id": ObjectId(str(lineup_id))})
    if not lineup:
        raise ValueError("Lineup not found")
    players = [p for p in lineup["players"] if p["user_id"] != user_id]
    await _db.lineups.update_one({"_id": ObjectId(str(lineup_id))}, {"$set": {"players": players}})


async def delete_lineup(guild_id: str, name: str) -> None:
    if not _check():
        return
    await _db.lineups.delete_one({"guild_id": guild_id, "lineup_name": name})


# ──────────────────────────────────────────────
# GAMETIMES
# ──────────────────────────────────────────────

async def create_gametime(guild_id, league, game_time, message_id, channel_id, ping_role_id, created_by):
    if not _check():
        return None
    doc = {
        "guild_id": guild_id, "guildId": guild_id,
        "league": league, "game_time": game_time,
        "message_id": message_id, "messageId": message_id,
        "channel_id": channel_id, "channelId": channel_id,
        "ping_role_id": ping_role_id, "created_by": created_by,
        "is_active": True, "responses": [],
    }
    r = await _db.gametimes.insert_one(doc)
    return {**doc, "_id": r.inserted_id}


async def get_gametime_by_message_id(message_id: str):
    if not _check():
        return None
    return await _db.gametimes.find_one(
        {"$or": [{"message_id": message_id}, {"messageId": message_id}]}
    )


# ──────────────────────────────────────────────
# ACTIVITY CHECKS
# ──────────────────────────────────────────────

async def create_activity_check(guild_id, message_id, channel_id, expires_at, created_by, role_id=None):
    if not _check():
        return None
    doc = {
        "guild_id": guild_id, "message_id": message_id,
        "channel_id": channel_id, "expires_at": expires_at,
        "created_by": created_by, "role_id": role_id, "is_active": True, "responses": [],
    }
    r = await _db.activity_checks.insert_one(doc)
    return {**doc, "_id": r.inserted_id}


async def get_due_activity_checks(now: datetime) -> list:
    if not _check():
        return []
    return await _db.activity_checks.find({"is_active": True, "expires_at": {"$lte": now}}).to_list(None)


async def close_activity_check(check_id) -> None:
    if not _check():
        return
    await _db.activity_checks.update_one({"_id": ObjectId(str(check_id))}, {"$set": {"is_active": False}})


# ──────────────────────────────────────────────
# SUGGESTIONS
# ──────────────────────────────────────────────

async def create_suggestion(guild_id, user_id, text):
    if not _check():
        return None
    doc = {"guild_id": guild_id, "user_id": user_id, "suggestion_text": text, "status": "pending"}
    r = await _db.suggestions.insert_one(doc)
    return {**doc, "_id": r.inserted_id}


# ──────────────────────────────────────────────
# STATS & LOGGING
# ──────────────────────────────────────────────

async def log_command(command_name: str, guild_id: str, user_id: str) -> None:
    if not _check():
        return
    await _db.command_usage.insert_one(
        {"command_name": command_name, "guild_id": guild_id, "user_id": user_id,
         "timestamp": datetime.now(timezone.utc)}
    )


async def get_bot_stats() -> dict:
    if not _check():
        return {"total_guilds": 0, "total_users": 0, "total_commands_used": 0, "premium_guilds": 0}
    return {
        "total_guilds": await _db.guilds.count_documents({}),
        "total_users": await _db.users.count_documents({}),
        "total_commands_used": await _db.command_usage.count_documents({}),
        "premium_guilds": await _db.guilds.count_documents({"premium": True}),
    }


# ──────────────────────────────────────────────
# DEPTH CHARTS
# ──────────────────────────────────────────────

async def create_depth_chart(guild_id, name, abbreviation):
    if not _check():
        return None
    doc = {"guildId": guild_id, "name": name, "abbreviation": abbreviation.upper(), "players": []}
    r = await _db.depth_charts.insert_one(doc)
    return {**doc, "_id": r.inserted_id}


async def get_depth_chart(guild_id: str, abbreviation: str):
    if not _check():
        return None
    return await _db.depth_charts.find_one({"guildId": guild_id, "abbreviation": abbreviation.upper()})


async def get_all_depth_charts(guild_id: str):
    if not _check():
        return []
    return await _db.depth_charts.find({"guildId": guild_id}).to_list(None)


async def delete_depth_chart(guild_id: str, abbreviation: str) -> bool:
    if not _check():
        return False
    r = await _db.depth_charts.delete_one({"guildId": guild_id, "abbreviation": abbreviation.upper()})
    return r.deleted_count > 0


async def add_player_to_depth_chart(guild_id: str, abbreviation: str, user_id: str) -> bool:
    if not _check():
        return False
    # The filter makes this atomic: two simultaneous /depthchart add calls can't
    # both pass the cog's "already added?" check and insert the same player twice.
    r = await _db.depth_charts.update_one(
        {"guildId": guild_id, "abbreviation": abbreviation.upper(), "players.userId": {"$ne": user_id}},
        {"$push": {"players": {"userId": user_id}}},
    )
    return r.modified_count > 0


async def remove_player_from_depth_chart(guild_id: str, abbreviation: str, user_id: str) -> bool:
    if not _check():
        return False
    await _db.depth_charts.update_one(
        {"guildId": guild_id, "abbreviation": abbreviation.upper()},
        {"$pull": {"players": {"userId": user_id}}},
    )
    return True


async def swap_depth_chart_players(guild_id: str, abbreviation: str, i1: int, i2: int) -> bool:
    if not _check():
        return False
    dc = await get_depth_chart(guild_id, abbreviation)
    if not dc:
        raise ValueError("Depth chart not found")
    players = list(dc["players"])
    if not (0 <= i1 < len(players) and 0 <= i2 < len(players)):
        return False
    players[i1], players[i2] = players[i2], players[i1]
    await _db.depth_charts.update_one(
        {"guildId": guild_id, "abbreviation": abbreviation.upper()},
        {"$set": {"players": players}},
    )
    return True


# ──────────────────────────────────────────────
# CONTRACTS
# ──────────────────────────────────────────────

async def add_contract(guild_id, user_id, position, amount, due, terms, paid, message_id, created_by):
    if not _check():
        return None
    doc = {
        "guildId": guild_id, "userId": user_id,
        "position": position, "amount": amount,
        "due": due, "terms": terms, "paid": paid,
        "messageId": message_id, "createdBy": created_by,
    }
    r = await _db.contracts.insert_one(doc)
    return {**doc, "_id": r.inserted_id}


async def get_player_contract(guild_id: str, user_id: str):
    if not _check():
        return None
    return await _db.contracts.find_one({"guildId": guild_id, "userId": user_id})


async def get_all_contracts(guild_id: str):
    if not _check():
        return []
    return await _db.contracts.find({"guildId": guild_id}).to_list(None)


async def remove_contract(guild_id: str, user_id: str) -> bool:
    if not _check():
        return False
    r = await _db.contracts.delete_one({"guildId": guild_id, "userId": user_id})
    return r.deleted_count > 0


async def mark_contract_paid(guild_id: str, user_id: str, paid: bool = True) -> bool:
    if not _check():
        return False
    await _db.contracts.update_one(
        {"guildId": guild_id, "userId": user_id},
        {"$set": {"paid": paid}},
    )
    return True


async def get_contract_by_message_id(message_id: str):
    if not _check():
        return None
    return await _db.contracts.find_one({"messageId": message_id})


# ──────────────────────────────────────────────
# GLOBAL ANNOUNCEMENT SETTINGS
# ──────────────────────────────────────────────

async def get_global_pings_disabled(guild_id: str) -> bool:
    if not _check():
        return False
    doc = await _db.global_settings.find_one({"guild_id": guild_id})
    return bool(doc and doc.get("pings_disabled", False))


async def set_global_pings_disabled(guild_id: str, disabled: bool) -> None:
    if not _check():
        return
    await _db.global_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {"pings_disabled": disabled}},
        upsert=True
    )


# ──────────────────────────────────────────────
# MATCHMAKING (pooled across every server the bot is in)
# ──────────────────────────────────────────────

async def create_matchmaking_request(
    guild_id: str, guild_name: str, user_id: str, game: str, size: str, time_text: str,
    postings: list | None = None,
):
    if not _check():
        return None
    doc = {
        "guild_id": guild_id,
        "guild_name": guild_name,
        "user_id": user_id,
        "game": game,
        "size": size,
        "time_text": time_text,
        "postings": postings or [],  # [{guild_id, channel_id, message_id}, ...]
        "created_at": datetime.now(timezone.utc),
    }
    r = await _db.matchmaking_queue.insert_one(doc)
    return {**doc, "_id": r.inserted_id}


async def get_user_matchmaking_request(user_id: str, game: str, size: str):
    """This user's pending request for this exact game + size (anywhere), if any."""
    if not _check():
        return None
    return await _db.matchmaking_queue.find_one({"user_id": user_id, "game": game, "size": size})


async def find_matchmaking_opponent(game: str, size: str, exclude_user_id: str):
    """Oldest waiting request for this game + size from someone else (read-only)."""
    if not _check():
        return None
    return await _db.matchmaking_queue.find_one(
        {"game": game, "size": size, "user_id": {"$ne": exclude_user_id}},
        sort=[("created_at", 1)],
    )


async def claim_matchmaking_opponent(game: str, size: str, exclude_user_id: str):
    """Atomically find AND remove the oldest waiting opponent.

    Using find_one_and_delete means two people queueing at the same instant can
    never both be handed the same opponent.
    """
    if not _check():
        return None
    return await _db.matchmaking_queue.find_one_and_delete(
        {"game": game, "size": size, "user_id": {"$ne": exclude_user_id}},
        sort=[("created_at", 1)],
    )


async def update_matchmaking_request(request_id, time_text: str = None, postings: list = None) -> None:
    if not _check():
        return
    updates = {}
    if time_text is not None:
        updates["time_text"] = time_text
    if postings is not None:
        updates["postings"] = postings
    if updates:
        await _db.matchmaking_queue.update_one({"_id": ObjectId(str(request_id))}, {"$set": updates})


async def remove_matchmaking_request(request_id) -> None:
    if not _check():
        return
    await _db.matchmaking_queue.delete_one({"_id": ObjectId(str(request_id))})


async def get_matchmaking_queue():
    if not _check():
        return []
    return await _db.matchmaking_queue.find({}).sort("created_at", 1).to_list(None)


async def get_user_matchmaking_requests(user_id: str):
    if not _check():
        return []
    return await _db.matchmaking_queue.find({"user_id": user_id}).to_list(None)


async def get_expired_matchmaking_requests(cutoff: datetime):
    if not _check():
        return []
    return await _db.matchmaking_queue.find({"created_at": {"$lt": cutoff}}).to_list(None)
