"""cogs/activity.py — Message activity tracking, rankings and an optional live leaderboard."""

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import is_manager_or_mod
from utils.ui import brand

PERIODS = [
    app_commands.Choice(name="Today", value="day"),
    app_commands.Choice(name="Last 7 days", value="week"),
    app_commands.Choice(name="All time", value="all"),
]
PERIOD_LABEL = {"day": "Today", "week": "Last 7 Days", "all": "All Time"}
MEDALS = ["🥇", "🥈", "🥉"]


def today_str(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def period_start(period: str, now: datetime | None = None) -> str | None:
    """Inclusive start date (UTC) for a period, or None for all-time."""
    now = now or datetime.now(timezone.utc)
    if period == "day":
        return today_str(now)
    if period == "week":
        return today_str(now - timedelta(days=6))  # today + previous 6 days
    return None


class Activity(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._buf: Counter = Counter()           # (guild_id, user_id, date) -> messages not yet written
        self._cfg: dict[str, tuple] = {}         # guild_id -> (fetched_at, settings)
        self._names: dict[tuple, str] = {}       # (guild_id, user_id) -> latest display name (shown on the website)
        self.flush_loop.start()
        self.panel_loop.start()

    def cog_unload(self):
        self.flush_loop.cancel()
        self.panel_loop.cancel()

    group = app_commands.Group(name="activity", description="Member activity tracking", guild_only=True)

    # ── tracking ─────────────────────────────────────────────
    async def _settings(self, gid: str) -> dict:
        hit = self._cfg.get(gid)
        if hit and time.monotonic() - hit[0] < 60:  # 1 DB read/min/server instead of 1 per message
            return hit[1]
        cfg = await db.get_guild_config(gid) or {}
        s = {"enabled": bool(cfg.get("activity_enabled")), "channels": [str(c) for c in cfg.get("activity_channels") or []]}
        self._cfg[gid] = (time.monotonic(), s)
        return s

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id or not db.is_connected():
            return
        s = await self._settings(str(message.guild.id))
        if not s["enabled"]:
            return
        if s["channels"]:
            cid = str(getattr(message.channel, "parent_id", None) or message.channel.id)  # threads count for their parent
            if cid not in s["channels"]:
                return
        self._buf[(str(message.guild.id), str(message.author.id), today_str())] += 1
        self._names[(str(message.guild.id), str(message.author.id))] = message.author.display_name

    async def flush(self):
        if not self._buf or not db.is_connected():
            return
        pending, self._buf = self._buf, Counter()
        for (gid, uid, date), n in pending.items():
            try:
                await db.col("activity_daily").update_one({"guild_id": gid, "user_id": uid, "date": date}, {"$inc": {"count": n}, "$set": {"user_name": self._names.get((gid, uid), "")}}, upsert=True)
            except Exception:
                self._buf[(gid, uid, date)] += n  # keep it for the next flush

    @tasks.loop(seconds=30)
    async def flush_loop(self):
        await self.flush()

    # ── queries ──────────────────────────────────────────────
    async def ranking(self, guild: discord.Guild, period: str, limit: int = 10) -> list:
        match = {"guild_id": str(guild.id)}
        start = period_start(period)
        if start:
            match["date"] = {"$gte": start}
        rows = await db.col("activity_daily").aggregate([
            {"$match": match}, {"$group": {"_id": "$user_id", "count": {"$sum": "$count"}}}, {"$sort": {"count": -1}},
        ]).to_list(None)
        out = []
        for r in rows:  # skip people who left, and bots (never recorded, but be safe)
            m = guild.get_member(int(r["_id"]))
            if m and not m.bot:
                out.append((r["_id"], r["count"]))
        return out[:limit] if limit else out

    def board_embed(self, rows: list, period: str, guild: discord.Guild) -> discord.Embed:
        lines = [f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{uid}> — **{n:,}** message(s)" for i, (uid, n) in enumerate(rows)]
        e = brand(f"💬 Most Active — {PERIOD_LABEL[period]}", "\n".join(lines) or "No activity recorded yet.", 0x5865F2)
        e.set_footer(text=f"TeamCore • {guild.name} • UTC days")
        return e

    # ── commands ─────────────────────────────────────────────
    @group.command(name="top", description="Most active members")
    @app_commands.choices(period=PERIODS)
    async def top(self, interaction: discord.Interaction, period: str = "week"):
        s = await self._settings(str(interaction.guild_id))
        if not s["enabled"]:
            return await interaction.response.send_message(embed=error_embed("Tracking Is Off", "A manager can turn it on with `/activity config`."), ephemeral=True)
        await interaction.response.defer()
        await self.flush()
        await interaction.followup.send(embed=self.board_embed(await self.ranking(interaction.guild, period), period, interaction.guild), allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="user", description="See a member's activity")
    async def user(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        await interaction.response.defer()
        await self.flush()
        e = brand(f"💬 Activity — {member.display_name}")
        e.set_thumbnail(url=member.display_avatar.url)
        for period in ("day", "week", "all"):
            ranks = await self.ranking(interaction.guild, period, limit=0)
            pos = next((i + 1 for i, (uid, _) in enumerate(ranks) if uid == str(member.id)), None)
            n = next((c for uid, c in ranks if uid == str(member.id)), 0)
            e.add_field(name=PERIOD_LABEL[period], value=f"**{n:,}** msgs" + (f"\nRank #{pos}" if pos else ""), inline=True)
        await interaction.followup.send(embed=e)

    @group.command(name="config", description="Change activity tracking settings (Manager)")
    @app_commands.describe(action="What to do", channel="Channel for add/remove")
    @app_commands.choices(action=[
        app_commands.Choice(name="Show settings", value="show"), app_commands.Choice(name="Enable tracking", value="enable"),
        app_commands.Choice(name="Disable tracking", value="disable"), app_commands.Choice(name="Track only this channel (add)", value="add"),
        app_commands.Choice(name="Stop tracking this channel (remove)", value="remove"), app_commands.Choice(name="Track all channels", value="all"),
    ])
    async def config(self, interaction: discord.Interaction, action: str, channel: discord.TextChannel = None):
        if not await is_manager_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        gid = str(interaction.guild_id)
        cfg = await db.get_guild_config(gid) or {}
        channels = [str(c) for c in cfg.get("activity_channels") or []]
        if action in ("add", "remove") and channel is None:
            return await interaction.response.send_message(embed=error_embed("Channel Needed", "Pick a `channel`."), ephemeral=True)
        if action == "enable":
            await db.set_guild_config(gid, {"activity_enabled": True})
        elif action == "disable":
            await db.set_guild_config(gid, {"activity_enabled": False})
        elif action == "add":
            await db.set_guild_config(gid, {"activity_channels": sorted(set(channels) | {str(channel.id)})})
        elif action == "remove":
            await db.set_guild_config(gid, {"activity_channels": [c for c in channels if c != str(channel.id)]})
        elif action == "all":
            await db.set_guild_config(gid, {"activity_channels": []})
        self._cfg.pop(gid, None)
        s = await self._settings(gid)
        where = " ".join(f"<#{c}>" for c in s["channels"]) or "all channels"
        await interaction.response.send_message(embed=success_embed(
            "Activity Settings", f"Tracking: **{'on' if s['enabled'] else 'off'}**\nChannels: {where}\nBots are never counted."), ephemeral=True)

    @group.command(name="leaderboard", description="Post a self-updating activity leaderboard (Manager)")
    @app_commands.choices(period=PERIODS)
    async def leaderboard(self, interaction: discord.Interaction, period: str = "week", channel: discord.TextChannel = None):
        if not await is_manager_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        channel = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await channel.send(embed=self.board_embed(await self.ranking(interaction.guild, period), period, interaction.guild), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return await interaction.followup.send(embed=error_embed("Couldn't Post", f"I can't post in {channel.mention}."), ephemeral=True)
        await db.set_guild_config(str(interaction.guild_id), {"activity_panel": {"channel_id": str(channel.id), "message_id": str(msg.id), "period": period}, "activity_enabled": True})
        self._cfg.pop(str(interaction.guild_id), None)
        await interaction.followup.send(embed=success_embed("Leaderboard Posted", "It refreshes every 15 minutes. Tracking is enabled."), ephemeral=True)

    @tasks.loop(minutes=15)
    async def panel_loop(self):
        if not db.is_connected():
            return
        await self.flush()
        for g in self.bot.guilds:
            panel = (await db.get_guild_config(str(g.id)) or {}).get("activity_panel")
            if not panel:
                continue
            ch = g.get_channel(int(panel["channel_id"]))
            try:
                msg = await ch.fetch_message(int(panel["message_id"])) if ch else None
                if msg:
                    await msg.edit(embed=self.board_embed(await self.ranking(g, panel["period"]), panel["period"], g))
            except discord.NotFound:
                await db.set_guild_config(str(g.id), {"activity_panel": None})
            except discord.HTTPException:
                pass

    @flush_loop.before_loop
    @panel_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Activity(bot))
