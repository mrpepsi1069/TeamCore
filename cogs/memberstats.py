"""cogs/memberstats.py — Membership growth: joins/leaves, daily snapshots, history and charts."""

from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import is_manager_or_mod
from utils.ui import bar, brand


def day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def growth(snapshots: list, current_total: int, days: int, today: datetime) -> int | None:
    """Net member change over `days`, from the stored snapshot closest to (and not after) that date."""
    target = day(today - timedelta(days=days))
    older = [s for s in snapshots if s["date"] <= target]
    base = max(older, key=lambda s: s["date"]) if older else None
    return current_total - base["total"] if base else None


def sparkline_chart(snaps: list, width: int = 12) -> str:
    """Text bar chart of daily totals (scaled between the min and max of the window)."""
    if not snaps:
        return "No data yet."
    lo, hi = min(s["total"] for s in snaps), max(s["total"] for s in snaps)
    span = max(hi - lo, 1)
    return "\n".join(f"`{s['date'][5:]}` `{bar((s['total'] - lo) / span if hi != lo else 1, width)}` {s['total']:,}" for s in snaps)


class MemberStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snapshot_loop.start()

    def cog_unload(self):
        self.snapshot_loop.cancel()

    group = app_commands.Group(name="memberstats", description="Server growth statistics", guild_only=True)

    # ── tracking ─────────────────────────────────────────────
    async def _bump(self, guild_id: int, field: str):
        if db.is_connected():
            await db.col("member_daily").update_one(
                {"guild_id": str(guild_id), "date": day(datetime.now(timezone.utc))}, {"$inc": {field: 1}}, upsert=True
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._bump(member.guild.id, "joined")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._bump(member.guild.id, "left")

    def counts(self, guild: discord.Guild) -> tuple:
        bots = sum(1 for m in guild.members if m.bot)
        total = guild.member_count or len(guild.members)
        return total, max(total - bots, 0), bots

    async def take_snapshot(self, guild: discord.Guild) -> dict:
        total, humans, bots = self.counts(guild)
        snap = {"total": total, "humans": humans, "bots": bots}
        await db.col("member_snapshots").update_one({"guild_id": str(guild.id), "date": day(datetime.now(timezone.utc))}, {"$set": snap}, upsert=True)
        return snap

    @tasks.loop(hours=1)
    async def snapshot_loop(self):
        """Keeps today's snapshot current; the first run of a new UTC day also posts the optional summary."""
        if not db.is_connected():
            return
        for g in self.bot.guilds:
            try:
                today = day(datetime.now(timezone.utc))
                first_today = not await db.col("member_snapshots").find_one({"guild_id": str(g.id), "date": today})
                await self.take_snapshot(g)
                cfg = await db.get_guild_config(str(g.id)) or {}
                if first_today and cfg.get("memberstats_channel"):
                    ch = g.get_channel(int(cfg["memberstats_channel"]))
                    if ch:
                        await ch.send(embed=await self.summary_embed(g, "📆 Daily Member Report"))
            except Exception as e:
                print(f"⚠️ memberstats snapshot failed for {g.id}: {e}")

    @snapshot_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ── reporting ────────────────────────────────────────────
    async def _window(self, gid: str, days: int, today: datetime) -> tuple:
        start = day(today - timedelta(days=days - 1))
        rows = await db.col("member_daily").find({"guild_id": gid, "date": {"$gte": start}}).to_list(None)
        return sum(r.get("joined", 0) for r in rows), sum(r.get("left", 0) for r in rows)

    async def summary_embed(self, guild: discord.Guild, title: str = "📈 Member Statistics") -> discord.Embed:
        gid, now = str(guild.id), datetime.now(timezone.utc)
        total, humans, bots = self.counts(guild)
        snaps = await db.col("member_snapshots").find({"guild_id": gid}).to_list(None)
        e = brand(title)
        e.add_field(name="👥 Members", value=f"**{total:,}** total\n🙋 {humans:,} humans · 🤖 {bots:,} bots", inline=False)
        for label, d in (("Today", 1), ("7 days", 7), ("30 days", 30)):
            j, l = await self._window(gid, d, now)
            g = growth(snaps, total, d, now)
            e.add_field(name=f"📅 {label}", value=f"➕ {j} joined\n➖ {l} left\n📊 Net {j - l:+d}" + (f"\nΔ vs snapshot {g:+d}" if g is not None and d > 1 else ""), inline=True)
        e.set_footer(text="TeamCore • UTC days • tracking starts when the bot joined")
        return e

    @group.command(name="view", description="Member counts, joins/leaves and growth")
    async def view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild.chunked:
            try:
                await interaction.guild.chunk()
            except Exception:
                pass
        await interaction.followup.send(embed=await self.summary_embed(interaction.guild))

    @group.command(name="history", description="Daily member history with a chart")
    @app_commands.describe(days="How many days to show (default 14)")
    async def history(self, interaction: discord.Interaction, days: app_commands.Range[int, 3, 30] = 14):
        gid = str(interaction.guild_id)
        start = day(datetime.now(timezone.utc) - timedelta(days=days - 1))
        snaps = sorted(await db.col("member_snapshots").find({"guild_id": gid, "date": {"$gte": start}}).to_list(None), key=lambda s: s["date"])
        if not snaps:
            return await interaction.response.send_message(embed=error_embed("No History Yet", "Snapshots are taken hourly — check back soon."), ephemeral=True)
        daily = {r["date"]: r for r in await db.col("member_daily").find({"guild_id": gid, "date": {"$gte": start}}).to_list(None)}
        e = brand(f"📈 Member History — last {days} days", sparkline_chart(snaps))
        total_j = sum(r.get("joined", 0) for r in daily.values())
        total_l = sum(r.get("left", 0) for r in daily.values())
        e.add_field(name="Joined / Left", value=f"➕ {total_j} · ➖ {total_l} · Net **{total_j - total_l:+d}**", inline=False)
        await interaction.response.send_message(embed=e)

    @group.command(name="channel", description="Post a daily member report in a channel (Manager)")
    @app_commands.describe(channel="Channel for the daily report (leave empty to turn it off)")
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await is_manager_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        await db.set_guild_config(str(interaction.guild_id), {"memberstats_channel": channel.id if channel else None})
        await interaction.response.send_message(embed=success_embed("Updated", f"Daily report → {channel.mention}" if channel else "Daily report turned off."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberStats(bot))
