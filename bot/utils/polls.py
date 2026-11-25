"""utils/polls.py — poll rendering and tallying (pure functions, easy to test)."""

import discord
from utils.ui import bar, brand


def tally(poll: dict, votes: list) -> tuple[list, int]:
    """Returns (count per option, number of distinct voters)."""
    counts = [0] * len(poll["options"])
    for v in votes:
        for i in v.get("choices", []):
            if 0 <= i < len(counts):
                counts[i] += 1
    return counts, len(votes)


def poll_embed(poll: dict, votes: list, closed: bool = False) -> discord.Embed:
    counts, voters = tally(poll, votes)
    total_votes = sum(counts)
    top = max(counts) if counts and total_votes else None
    e = brand(f"📊 {poll['question']}", color=0x95A5A6 if closed else 0x5865F2)
    for i, opt in enumerate(poll["options"]):
        frac = counts[i] / total_votes if total_votes else 0
        crown = " 👑" if closed and top and counts[i] == top else ""
        value = f"`{bar(frac)}` **{counts[i]}** ({frac * 100:.0f}%)"
        if not poll["anonymous"] and counts[i]:
            names = [f"<@{v['user_id']}>" for v in votes if i in v.get("choices", [])]
            value += "\n" + " ".join(names[:8]) + (f" +{len(names) - 8} more" if len(names) > 8 else "")
        e.add_field(name=f"{i + 1}. {opt}{crown}", value=value, inline=False)
    flags = ["🕵️ Anonymous" if poll["anonymous"] else "👁️ Public votes", "☑️ Multiple choice" if poll["multiple"] else "1️⃣ One vote"]
    status = "**Final results**" if closed else f"Ends {discord.utils.format_dt(poll['ends_at'], 'R')}"
    e.description = f"{status} · {voters} voter(s) · " + " · ".join(flags) + f"\nPoll #{poll['poll_id']}"
    return e


def poll_view(poll: dict) -> discord.ui.View:
    """Buttons only carry a custom_id; clicks are handled by the cog's listener so polls keep working after restarts."""
    view = discord.ui.View(timeout=None)
    for i, opt in enumerate(poll["options"]):
        view.add_item(discord.ui.Button(
            label=f"{i + 1}. {opt}"[:80], style=discord.ButtonStyle.primary, custom_id=f"poll:{poll['poll_id']}:{i}", row=i // 5
        ))
    return view


def apply_vote(choices: list, idx: int, multiple: bool) -> list:
    """Toggle semantics: clicking your current choice removes it; single-choice replaces, multiple adds."""
    if idx in choices:
        return [c for c in choices if c != idx]
    return sorted(choices + [idx]) if multiple else [idx]
