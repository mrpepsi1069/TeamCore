"""cogs/recordsdigest.py — the week's parsed Football Fusion leaders, posted where the team already looks.

Members shouldn't need the dashboard to see who led the week. Once a week this posts the latest week that has parsed
games — the top players for the stats most of that week's players have — from the same stored stat sheets the Records
page totals (`shared.fusion.player_rows`), so the channel and the page can't disagree.

Each season/week goes out once per server: the posted period is remembered in the guild config, so restarts and
reconnects can't double-post. `/records post` sends it by hand (without using up the week's automatic post), and
`/records digest` shows the schedule and switches it off.
"""

from datetime import datetime, time, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from shared.fusion import match_period, player_rows
from utils.embeds import error_embed
from utils.permissions import has_manager_perms
from utils.ui import brand

MATCH_LIMIT = 500          # the same newest-matches cap the dashboard's Records page reads
STATS_PER_POST = 4         # headline stats in one digest
PLAYERS_PER_STAT = 3
POST_HOUR_UTC = 9          # Monday morning
MARK = "records_digest"    # guild_config key holding the season/week already posted
MEDALS = ["🥇", "🥈", "🥉"]


# ── the numbers (pure, so the digest is the same calculation the page does) ──

def period_name(season: int | None, week: int | None) -> str:
    if season is not None and week is not None:
        return f"Season {season} · Week {week}"
    if season is not None:
        return f"Season {season}"
    if week is not None:
        return f"Week {week}"
    return "All games"


def stat_name(key: str) -> str:
    """A stat key as the dashboard's Records page labels it (“pass_yds” → “Pass yds”), so a field matches its column."""
    text = str(key or "").replace("_", " ")
    return text[:1].upper() + text[1:]


def latest_period(matches: list) -> tuple[int | None, int | None]:
    """The newest period with a parsed game. Matches come newest first; a report whose title names no season or
    week is skipped rather than ending the search."""
    for match in matches:
        season, week = match_period(match.get("ff"))
        if season is not None or week is not None:
            return season, week
    return None, None


def in_period(match: dict, season: int | None, week: int | None) -> bool:
    s, w = match_period(match.get("ff"))
    return (season is None or s == season) and (week is None or w == week)


def headline_stats(rows: list, limit: int = STATS_PER_POST) -> list[str]:
    """The stats worth ranking: the ones most of the week's players have a real number for, widest first."""
    counts: dict = {}
    for row in rows:
        for key, value in row["totals"].items():
            if value:
                counts[key] = counts.get(key, 0) + 1
    return sorted(counts, key=lambda k: (-counts[k], k))[:limit]


def leaders(rows: list, stat: str, limit: int = PLAYERS_PER_STAT) -> list[dict]:
    ranked = [row for row in rows if row["totals"].get(stat)]
    ranked.sort(key=lambda row: (-row["totals"][stat], row["name"].lower()))
    return ranked[:limit]


def digest_embed(season: int | None, week: int | None, rows: list, games: int,
                 guild_name: str) -> discord.Embed | None:
    """The post itself: one field per headline stat, top three players each. None when the week has no numbers."""
    stats = headline_stats(rows)
    if not stats:
        return None
    e = brand(f"🏆 {period_name(season, week)} leaders",
              f"The week's parsed stat leaders · {games} game{'s' if games != 1 else ''}")
    for stat in stats:
        lines = []
        for i, row in enumerate(leaders(rows, stat)):
            who = f"**{row['name']}**"
            if row.get("position") and row["position"] != "—":
                who += f" ({row['position']})"
            lines.append(f"{MEDALS[i] if i < len(MEDALS) else f'`{i + 1}.`'} {who} — **{row['totals'][stat]}** · {row['games']} GP")
        e.add_field(name=stat_name(stat), value="\n".join(lines), inline=True)
    e.set_footer(text=f"{guild_name} · TeamCore · Records")
    return e


def next_run_ts() -> int:
    """When the next Monday post is due, as a Unix timestamp for Discord's relative timestamp."""
    now = datetime.now(timezone.utc)
    at = (now + timedelta(days=(0 - now.weekday()) % 7)).replace(hour=POST_HOUR_UTC, minute=0, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=7)
    return int(at.timestamp())


# ── reading the stored stat sheets ──────────────────────────────────────

async def load_guild(guild_id: str) -> tuple[list, list, dict]:
    """Every parsed Football Fusion match (newest first) plus the teams and roster links its stat sheets resolve against."""
    q = {"guild_id": str(guild_id), "ff": {"$type": "object"},
         "$or": [{"ff.sport": {"$exists": False}}, {"ff.sport": "ff"}]}
    matches = await db.col("matches").find(q).sort("played_at", -1).limit(MATCH_LIMIT).to_list(None)
    teams = await db.col("teams").find({"guild_id": str(guild_id)}).to_list(None)
    names = {t["team_id"]: t.get("name") for t in teams}
    roster: dict = {}
    for m in await db.col("team_members").find({"guild_id": str(guild_id)}).to_list(None):
        roster.setdefault(str(m.get("user_id") or ""), names.get(m.get("team_id")))
    return matches, teams, roster


class RecordsDigest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly.start()

    def cog_unload(self):
        self.weekly.cancel()

    # ── the weekly post ──────────────────────────────────────
    @tasks.loop(time=time(hour=POST_HOUR_UTC, tzinfo=timezone.utc))
    async def weekly(self):
        """Runs every morning and only acts on Mondays, so a restart later in the week can't shift the schedule."""
        if datetime.now(timezone.utc).weekday() != 0:
            return
        await self.post_due()

    @weekly.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        if now.weekday() == 0 and now.hour >= POST_HOUR_UTC:
            await self.post_due()  # the bot was offline when Monday's tick was due

    async def post_due(self) -> int:
        """Post the latest unposted week in every server that has a results channel. Returns how many went out."""
        if not db.is_connected():
            return 0
        posted = 0
        for guild in list(self.bot.guilds):
            try:
                if await self.post_guild(guild):
                    posted += 1
            except discord.Forbidden:
                print(f"⚠️ records digest: missing permissions in {guild.name}")
            except Exception as e:
                print(f"⚠️ records digest failed for {guild.name}: {e}")
        return posted

    async def digest_for(self, guild: discord.Guild):
        """(embed, season, week, games) for the latest week with parsed games — (None, …) when there is nothing to rank."""
        matches, teams, roster = await load_guild(str(guild.id))
        season, week = latest_period(matches)
        if season is None and week is None:
            return None, season, week, 0
        games = [m for m in matches if in_period(m, season, week)]
        rows = player_rows(games, teams, roster)
        return digest_embed(season, week, rows, len(games), guild.name), season, week, len(games)

    async def post_guild(self, guild: discord.Guild, force: bool = False) -> bool:
        """Post one server's digest. `force` (the manual command) skips the already-posted check and leaves the
        marker alone, so testing on a Friday never swallows that week's scheduled post."""
        cfg = await db.get_guild_config(str(guild.id)) or {}
        if cfg.get("records_digest_enabled") is False:
            return False
        cid = cfg.get("results_channel")
        channel = guild.get_channel(int(cid)) if cid else None
        if not isinstance(channel, discord.TextChannel):
            return False
        embed, season, week, games = await self.digest_for(guild)
        if embed is None:
            return False
        last = cfg.get(MARK) or {}
        if not force and last.get("season") == season and last.get("week") == week:
            return False
        await channel.send(embed=embed)
        if not force:
            await db.set_guild_config(str(guild.id), {MARK: {
                "season": season, "week": week, "games": games, "channel_id": str(channel.id),
                "at": datetime.now(timezone.utc).isoformat()}})
        return True

    # ── commands ─────────────────────────────────────────────
    group = app_commands.Group(name="records", description="The week's parsed stat leaders", guild_only=True)

    @group.command(name="post", description="Post this week's Records leaders to the results channel now (Manager only)")
    async def post(self, interaction: discord.Interaction):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher to post the leaders."), ephemeral=True)
        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        channel = interaction.guild.get_channel(int(cfg["results_channel"])) if cfg.get("results_channel") else None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                embed=error_embed("No Results Channel", "Pick one first: `/setup` → Results Channel."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            posted = await self.post_guild(interaction.guild, force=True)
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=error_embed("Can't Post", "I need **Send Messages** and **Embed Links** in the results channel."), ephemeral=True)
        if not posted:
            return await interaction.followup.send(
                embed=error_embed("Nothing to Post", "No parsed Football Fusion stats yet."), ephemeral=True)
        embed, season, week, games = await self.digest_for(interaction.guild)
        await interaction.followup.send(
            embed=brand("📊 Leaders Posted", f"{period_name(season, week)} · {games} game{'s' if games != 1 else ''} → {channel.mention}\n"
                                              "The week's scheduled post still goes out on Monday."), ephemeral=True)

    @group.command(name="digest", description="The weekly leaders post: schedule, channel, and on/off (Manager only)")
    @app_commands.describe(state="Turn the weekly post on or off")
    @app_commands.choices(state=[app_commands.Choice(name="On", value="on"),
                                 app_commands.Choice(name="Off", value="off")])
    async def digest(self, interaction: discord.Interaction, state: str = None):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher to change the weekly post."), ephemeral=True)
        gid = str(interaction.guild_id)
        if state:
            await db.set_guild_config(gid, {"records_digest_enabled": state == "on"})
        cfg = await db.get_guild_config(gid) or {}
        on = cfg.get("records_digest_enabled") is not False
        cid = cfg.get("results_channel")
        last = cfg.get(MARK) or {}
        lines = [f"**Weekly post:** {'on' if on else 'off'}",
                 f"**Channel:** {f'<#{cid}>' if cid else 'none yet — `/setup` → Results Channel'}",
                 f"**When:** Mondays at {POST_HOUR_UTC:02d}:00 UTC · next <t:{next_run_ts()}:R>",
                 f"**Covers:** the latest week with parsed games, posted once per season/week"]
        if last.get("season") is None and last.get("week") is None:
            lines.append("**Last posted:** nothing yet")
        else:
            when = ""
            try:
                when = f" · <t:{int(datetime.fromisoformat(last['at']).timestamp())}:R>" if last.get("at") else ""
            except (TypeError, ValueError):
                when = ""
            lines.append(f"**Last posted:** {period_name(last.get('season'), last.get('week'))}{when}")
        await interaction.response.send_message(embed=brand("📊 Records leaders", "\n".join(lines)), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RecordsDigest(bot))
