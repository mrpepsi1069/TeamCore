"""utils/records.py — team record storage + formatting shared by /record, /leaderboard and /stats."""

from datetime import datetime, timezone
import discord
from discord import app_commands
import database as db
from utils.ui import brand
from utils.validation import is_valid_url

MAX_TEAMS = 100
DEFAULT_COMPETITION = "General"


def win_pct(wins: int, losses: int, ties: int) -> float:
    """Winning percentage; a tie counts as half a win (the usual convention)."""
    games = wins + losses + ties
    return (wins + 0.5 * ties) / games if games else 0.0


def fmt_pct(team: dict) -> str:
    return f"{win_pct(team['wins'], team['losses'], team['ties']):.3f}".lstrip("0") if team else ".000"


def fmt_record(team: dict) -> str:
    return f"{team['wins']}-{team['losses']}-{team['ties']}"


def sort_teams(teams: list, mode: str) -> list:
    """mode: wins | percentage | losses (fewest losses first)."""
    p = lambda t: win_pct(t["wins"], t["losses"], t["ties"])  # noqa: E731
    if mode == "percentage":
        return sorted(teams, key=lambda t: (-p(t), -t["wins"], t["name_lower"]))
    if mode == "losses":
        return sorted(teams, key=lambda t: (t["losses"], -t["wins"], t["name_lower"]))
    return sorted(teams, key=lambda t: (-t["wins"], -p(t), t["name_lower"]))


async def get_team(guild_id, name: str):
    c = db.col("teams")
    if c is None or not name:
        return None
    return await c.find_one({"guild_id": str(guild_id), "name_lower": name.strip().lower()})


async def list_teams(guild_id, competition: str | None = None) -> list:
    c = db.col("teams")
    if c is None:
        return []
    q = {"guild_id": str(guild_id)}
    if competition:
        q["competition_lower"] = competition.strip().lower()
    return await c.find(q).to_list(None)


async def competitions(guild_id) -> list:
    return sorted({t["competition"] for t in await list_teams(guild_id)})


async def add_history(guild_id, team: dict, action: str, actor_id, before: dict, after: dict, note: str = "") -> None:
    c = db.col("team_history")
    if c is None:
        return
    await c.insert_one({
        "guild_id": str(guild_id), "team_id": team["team_id"], "team_name": team["name"], "action": action,
        "by": str(actor_id), "before": before, "after": after, "note": note,
        "timestamp": datetime.now(timezone.utc),
    })


def snapshot(team: dict) -> dict:
    return {k: team[k] for k in ("wins", "losses", "ties")}


def team_embed(team: dict, rank: int | None = None, total: int | None = None) -> discord.Embed:
    e = brand(f"🏆 {team['name']}", color=0x5865F2)
    e.add_field(name="Record (W-L-T)", value=f"**{fmt_record(team)}**", inline=True)
    e.add_field(name="Win %", value=fmt_pct(team), inline=True)
    e.add_field(name="Games", value=str(team["wins"] + team["losses"] + team["ties"]), inline=True)
    e.add_field(name="Competition", value=team.get("competition") or DEFAULT_COMPETITION, inline=True)
    if rank:
        e.add_field(name="Rank", value=f"#{rank} of {total}", inline=True)
    if team.get("logo"):
        e.set_thumbnail(url=team["logo"])
    return e


def valid_logo(url: str | None) -> bool:
    return not url or is_valid_url(url)


async def team_autocomplete(interaction: discord.Interaction, current: str):
    teams = await list_teams(interaction.guild_id)
    cur = current.lower()
    return [
        app_commands.Choice(name=f"{t['name']} ({t.get('competition') or DEFAULT_COMPETITION})"[:100], value=t["name"])
        for t in sorted(teams, key=lambda t: t["name_lower"]) if cur in t["name_lower"]
    ][:25]


async def competition_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=c, value=c) for c in await competitions(interaction.guild_id) if current.lower() in c.lower()
    ][:25]
