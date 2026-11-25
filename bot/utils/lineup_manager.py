# utils/lineup_manager.py - Lineup helpers (TeamCore)
import database

POSITION_ORDER = {
    "qb": 1, "ol": 2, "te": 3, "streak": 4, "fold": 5, "los": 6,
    "short": 7, "deep": 8, "mlb": 9, "de": 10, "fs": 11,
    "flex": 12, "sub": 13, "backup": 13, "coach": 14, "manager": 15,
}


async def validate_lineup_exists(guild_id: str, lineup_name: str) -> bool:
    lineup = await database.get_lineup(guild_id, lineup_name)
    return lineup is not None


def format_lineup_players(players: list) -> str:
    if not players:
        return "No players added yet."
    return "\n".join(f"**{p['position']}:** <@{p['user_id']}>" for p in players)


def sort_players_by_position(players: list) -> list:
    return sorted(players, key=lambda p: POSITION_ORDER.get(p["position"].lower(), 99))


def is_lineup_full(lineup: dict, max_players: int = 15) -> bool:
    return len(lineup.get("players", [])) >= max_players
