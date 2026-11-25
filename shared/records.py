"""shared/records.py — win% and ranking logic used by both the bot and the dashboard (single implementation)."""


def win_pct(wins: int, losses: int, ties: int) -> float:
    """A tie counts as half a win."""
    games = wins + losses + ties
    return (wins + 0.5 * ties) / games if games else 0.0


def fmt_pct(w: int, l: int, t: int) -> str:
    return f"{win_pct(w, l, t):.3f}".lstrip("0") if (w + l + t) else ".000"


def sort_teams(teams: list, mode: str) -> list:
    """mode: wins | percentage | losses (fewest losses first)."""
    p = lambda t: win_pct(t["wins"], t["losses"], t["ties"])  # noqa: E731
    key = lambda t: t.get("name_lower") or t.get("name", "").lower()  # noqa: E731
    if mode == "percentage":
        return sorted(teams, key=lambda t: (-p(t), -t["wins"], key(t)))
    if mode == "losses":
        return sorted(teams, key=lambda t: (t["losses"], -t["wins"], key(t)))
    return sorted(teams, key=lambda t: (-t["wins"], -p(t), key(t)))


def match_outcome(home: int, away: int) -> tuple[str, str]:
    """(home result, away result) as record fields: wins / losses / ties."""
    if home > away:
        return "wins", "losses"
    if home < away:
        return "losses", "wins"
    return "ties", "ties"
