"""cogs/dashboard_bridge.py — the bot's side of the website: publishes server info, runs dashboard jobs.

The website never has the bot token. It writes jobs into `bot_jobs`; this cog (the only thing holding the token) claims
and executes them, then writes the result back. Jobs are woken by a MongoDB change stream (no polling); if change streams
aren't available it falls back to a slow poll. The bot works fine without the website — this cog is purely additive.
"""

import asyncio
import base64
import io
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
from discord.ext import commands, tasks

import database as db
from shared.jobs import DONE, FAILED, PENDING, RUNNING
from utils.schedule import record_schedule


class JobError(Exception):
    """A failure whose message is safe to show to the dashboard user."""


def _now():
    return datetime.now(timezone.utc)


def _label(value, limit: int = 80) -> str:
    """One line, no doubled spaces — a time or league name goes straight into the poll text."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def build_cache(guild: discord.Guild) -> dict:
    kinds = {discord.ChannelType.text: "text", discord.ChannelType.news: "news", discord.ChannelType.voice: "voice",
             discord.ChannelType.stage_voice: "stage", discord.ChannelType.forum: "forum"}
    return {
        "guild_id": str(guild.id), "name": guild.name, "icon": guild.icon.url if guild.icon else None, "owner_id": str(guild.owner_id),
        "member_count": guild.member_count, "boosts": guild.premium_subscription_count, "tier": guild.premium_tier, "updated_at": _now(),
        "channels": [{"id": str(c.id), "name": c.name, "type": kinds[c.type], "category": c.category.name if c.category else None}
                     for c in sorted(guild.channels, key=lambda c: c.position) if c.type in kinds],
        "roles": [{"id": str(r.id), "name": r.name, "color": r.color.value, "position": r.position, "managed": r.managed, "permissions": r.permissions.value}
                  for r in guild.roles],
    }


class DashboardBridge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._debounce: dict[int, asyncio.Task] = {}
        self._worker_task: asyncio.Task | None = None
        self._ticks = 0
        self._bg: set[asyncio.Task] = set()   # background work that must outlive the job (mass DMs)
        self.heartbeat.start()
        self.resync.start()

    async def cog_load(self):
        self._worker_task = asyncio.create_task(self._worker())

    def cog_unload(self):
        self.heartbeat.cancel()
        self.resync.cancel()
        if self._worker_task:
            self._worker_task.cancel()

    # ── server cache (so the website can show channel/role pickers without the bot token) ──
    async def publish(self, guild: discord.Guild) -> bool:
        """Write this server's name/channels/roles to the shared database. Returns True on success."""
        if not db.is_connected():
            return False
        doc = build_cache(guild)
        await db.col("servers").replace_one({"guild_id": doc["guild_id"]}, doc, upsert=True)
        return True

    async def publish_all(self, only_missing: bool = False) -> tuple[int, int]:
        """Publish every server the bot is in. Returns (published, total)."""
        guilds = list(self.bot.guilds)
        if only_missing:
            known = set(await db.col("servers").distinct("guild_id"))
            guilds = [g for g in guilds if str(g.id) not in known]
        done = 0
        for g in guilds:
            try:
                done += 1 if await self.publish(g) else 0
            except Exception as e:
                print(f"⚠️ Dashboard sync failed for {g.name} ({g.id}): {e}")
        return done, len(guilds)

    @tasks.loop(minutes=10)
    async def resync(self):
        """Safety net: the website only lists servers that exist in `servers`. If the bot started before the database
        was reachable, or a write was lost, this repairs it within 10 minutes (and refreshes everything every 6 hours)."""
        self._ticks += 1
        if not db.is_connected():
            try:
                await db.initialize()  # database was down at startup — try again
            except Exception as e:
                print(f"⚠️ Database still unavailable: {e}")
                return
        done, total = await self.publish_all(only_missing=(self._ticks % 36 != 0))
        if total:
            print(f"📡 Dashboard resync: published {done}/{total} server(s)")

    @resync.before_loop
    async def _resync_ready(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)  # on_ready already did the first full publish

    def schedule_publish(self, guild: discord.Guild):
        """Coalesce bursts of channel/role events into one write (keeps the free-tier database quiet)."""
        if guild.id in self._debounce and not self._debounce[guild.id].done():
            return

        async def later():
            await asyncio.sleep(20)
            try:
                await self.publish(guild)
            except Exception as e:
                print(f"⚠️ cache publish failed for {guild.id}: {e}")
        self._debounce[guild.id] = asyncio.create_task(later())

    @tasks.loop(seconds=60)
    async def heartbeat(self):
        if db.is_connected():
            await db.col("servers").update_one({"guild_id": "_bot"}, {"$set": {"heartbeat": _now()}}, upsert=True)

    @heartbeat.before_loop
    async def _hb_ready(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not db.is_connected():
            print("⚠️ Dashboard sync skipped: database not connected (will retry automatically).")
            return
        done, total = await self.publish_all()  # one write per server per start
        print(f"📡 Dashboard: published {done}/{total} server(s) to the shared database")

    @commands.Cog.listener()
    async def on_guild_available(self, guild):
        self.schedule_publish(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self.schedule_publish(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        if db.is_connected():
            await db.col("servers").delete_one({"guild_id": str(guild.id)})

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        self.schedule_publish(after)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch):
        self.schedule_publish(ch.guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, ch):
        self.schedule_publish(ch.guild)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if before.name != after.name or before.position != after.position:
            self.schedule_publish(after.guild)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        self.schedule_publish(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        self.schedule_publish(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        if (before.name, before.permissions, before.position) != (after.name, after.permissions, after.position):
            self.schedule_publish(after.guild)

    # ── job worker ───────────────────────────────────────────
    async def _worker(self):
        await self.bot.wait_until_ready()
        backoff = 5
        while not self.bot.is_closed():
            try:
                if not db.is_connected():
                    await asyncio.sleep(30)
                    continue
                await self._drain()
                async with db.col("bot_jobs").watch([{"$match": {"operationType": "insert"}}]) as stream:
                    backoff = 5
                    async for change in stream:  # woken by the database — zero polling while idle
                        await self._run(change["fullDocument"])
            except asyncio.CancelledError:
                raise
            except Exception as e:  # change streams unsupported / connection blip: poll slowly instead
                print(f"ℹ️ job watcher fell back to polling ({type(e).__name__}: {e})")
                try:
                    await asyncio.sleep(min(backoff, 15))
                    await self._drain()
                except Exception:
                    pass
                backoff = min(backoff * 2, 60)

    async def _drain(self):
        col = db.col("bot_jobs")
        await col.update_many({"status": RUNNING, "started_at": {"$lt": _now() - timedelta(minutes=3)}},
                              {"$set": {"status": FAILED, "error": "The bot restarted while running this action."}})
        for job in await col.find({"status": PENDING, "created_at": {"$gte": _now() - timedelta(hours=1)}}).sort("created_at", 1).to_list(100):
            await self._run(job)

    async def _run(self, job: dict):
        col = db.col("bot_jobs")
        claimed = await col.find_one_and_update({"_id": job["_id"], "status": PENDING}, {"$set": {"status": RUNNING, "started_at": _now()}})
        if not claimed:  # another worker already took it
            return
        try:
            guild = self.bot.get_guild(int(job["guild_id"]))
            if guild is None:
                raise JobError("The bot isn't in that server.")
            handler = getattr(self, f"job_{job['type']}", None)
            if handler is None:
                raise JobError(f"Unknown job type {job['type']}")
            result = await handler(guild, job["payload"], job) or {}
            await col.update_one({"_id": job["_id"]}, {"$set": {"status": DONE, "result": result, "finished_at": _now()}})
        except JobError as e:
            await col.update_one({"_id": job["_id"]}, {"$set": {"status": FAILED, "error": str(e)[:300], "finished_at": _now()}})
        except discord.Forbidden:
            await col.update_one({"_id": job["_id"]}, {"$set": {"status": FAILED, "error": "Discord says the bot lacks permission for that (check channel permissions / role order).", "finished_at": _now()}})
        except Exception as e:
            print(f"❌ job {job['type']} failed: {e!r}")
            await col.update_one({"_id": job["_id"]}, {"$set": {"status": FAILED, "error": "The bot hit an unexpected error running that.", "finished_at": _now()}})

    # ── helpers ──────────────────────────────────────────────
    @staticmethod
    def _channel(guild, channel_id, kinds=(discord.TextChannel, discord.abc.GuildChannel)):
        ch = guild.get_channel(int(channel_id)) if channel_id else None
        if ch is None or ch.guild.id != guild.id:  # a channel id from another server must never be honoured
            raise JobError("That channel doesn't exist in this server.")
        return ch

    @staticmethod
    def _text(guild, channel_id):
        ch = DashboardBridge._channel(guild, channel_id)
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            raise JobError("That isn't a text channel.")
        if not ch.permissions_for(guild.me).send_messages:
            raise JobError(f"I can't send messages in #{ch.name}.")
        return ch

    async def _doc(self, collection, guild, key, value):
        d = await db.col(collection).find_one({"guild_id": str(guild.id), key: value})
        if not d:
            raise JobError("That item no longer exists.")
        return d

    # ── gametimes / times ────────────────────────────────────
    async def _schedule_channel(self, guild):
        """Same channel /gametime and /times use — without a fallback, because the dashboard has no current channel."""
        cfg = await db.get_guild_config(str(guild.id)) or {}
        cid = cfg.get("gametime_channel")
        if not cid:
            raise JobError("No gametime channel is set — pick one in the dashboard under Settings → Gametime channel.")
        return self._text(guild, cid)

    @staticmethod
    def _ping_role(guild, role_id):
        role = guild.get_role(int(role_id)) if str(role_id or "").isdigit() else None
        if role is None or role.is_default():
            raise JobError("The role to ping no longer exists.")
        return role

    async def _record_schedule(self, guild, kind, league, times, role, channel, msg, job):
        """What the dashboard page lists — the same row the slash commands write."""
        await record_schedule(guild, kind, league, times, role, channel, msg, job.get("created_by") or "")

    def _later(self, coro) -> None:
        """Run something after the job has answered — mass DMs are far too slow to make the browser wait."""
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def job_gametime_create(self, guild, p, job):
        from cogs.gametime import GametimeView, dm_gametime_voters, gametime_embed
        channel = await self._schedule_channel(guild)
        role = self._ping_role(guild, p["role_id"])
        league, time = _label(p["league"]), _label(p["time"])
        if not league or not time:
            raise JobError("Give this gametime a league and a time.")
        msg = await channel.send(content=role.mention, embed=gametime_embed(league, time), view=GametimeView(),
                                 allowed_mentions=discord.AllowedMentions(roles=True))
        await db.create_gametime(str(guild.id), league, time, str(msg.id), str(channel.id), str(role.id),
                                 str(job.get("created_by") or ""))
        await self._record_schedule(guild, "gametime", league, [time], role, channel, msg, job)
        if p.get("dm"):
            self._later(dm_gametime_voters(guild, role, league, time, msg.jump_url, str(msg.id)))
        return {"message": msg.jump_url, "channel": channel.name, "dm": bool(p.get("dm"))}

    async def job_times_create(self, guild, p, job):
        from cogs.times import TimesView, cleaned_times, times_embed
        channel = await self._schedule_channel(guild)
        role = self._ping_role(guild, p["role_id"])
        league = _label(p["league"])
        if not league:
            raise JobError("Give these times a league.")
        options = cleaned_times([str(t) for t in (p.get("times") or [])])
        if len(options) < 2:
            raise JobError("Give at least two different time options.")
        if len(options) > 6:
            raise JobError("Six time options is the maximum.")
        msg = await channel.send(content=role.mention, embed=times_embed(league, options),
                                 view=TimesView(options, league), allowed_mentions=discord.AllowedMentions(roles=True))
        await self._record_schedule(guild, "times", league, options, role, channel, msg, job)
        return {"message": msg.jump_url, "channel": channel.name, "options": len(options)}

    # ── jobs ─────────────────────────────────────────────────
    async def job_refresh_cache(self, guild, p, job):
        await self.publish(guild)
        return {}

    async def job_record_changed(self, guild, p, job):
        self.bot.dispatch("team_record_changed", str(guild.id), p["team_id"])
        return {}

    async def job_post_result(self, guild, p, job):
        return await self.post_result(guild, p["match_id"])

    async def post_result(self, guild, match_id) -> dict:
        """Post one match's summary (and its game stats, when it has them) to the server's results channel."""
        from bson import ObjectId
        from shared.fusion import SPORTS, stat_line
        cfg = await db.get_guild_config(str(guild.id)) or {}
        cid = cfg.get("results_channel")
        if not cid:
            return {"posted": False}   # posting results is opt-in — no channel, nothing to do
        try:
            d = await db.col("matches").find_one({"guild_id": str(guild.id), "_id": ObjectId(str(match_id))})
        except Exception:
            d = None
        if not d:
            raise JobError("That match no longer exists.")
        names = {t["team_id"]: t["name"] for t in await db.col("teams").find({"guild_id": str(guild.id)}).to_list(None)}
        ff = d.get("ff") or {}
        side = ff.get("score") or {}
        home = names.get(d.get("home_team_id")) or side.get("home") or "Home"
        away = names.get(d.get("away_team_id")) or d.get("away_name") or side.get("away") or "Away"
        e = discord.Embed(title=f"🏟️ {_label(ff.get('game'), 240) or 'Match result'}", colour=0x57F287,
                          description=f"**{home}** {d['home_score']} – {d['away_score']} **{away}**",
                          timestamp=d.get("played_at") or _now())
        if ff.get("sport") in SPORTS:
            e.add_field(name="Sport", value=SPORTS[ff["sport"]], inline=True)
        if d.get("competition"):
            e.add_field(name="Competition", value=str(d["competition"])[:100], inline=True)
        if d.get("record_applied"):
            e.add_field(name="Records", value="Updated ✅", inline=True)
        rep = ff.get("reporter")
        if rep:
            value = str(rep.get("name") or "—")
            if rep.get("display") and rep["display"] != value:
                value += f" ({rep['display']})"
            e.add_field(name="Reported by", value=value[:100], inline=True)
        lines = []
        for pl in (ff.get("players") or [])[:8]:
            blocks = [f"**{pos}** {stat_line(st)}" for pos, st in (pl.get("positions") or {}).items() if st]
            if not blocks and pl.get("stats"):
                blocks = [stat_line(pl["stats"])]
            if blocks:
                lines.append(f"**{pl.get('name') or '?'}** — " + " · ".join(blocks))
        if lines:
            e.add_field(name="Player stats", value="\n".join(lines)[:1024], inline=False)
        if ff.get("total"):
            e.set_footer(text=f"{guild.name} · {ff.get('linked') or 0}/{ff.get('total')} players linked")
        else:
            e.set_footer(text=guild.name)
        # The stat sheet itself: every screenshot attached on import (or added later from the match's edit dialog).
        shot = await db.col("match_shots").find_one({"guild_id": str(guild.id), "match_id": d["_id"]})
        files = []
        if shot:
            images = [i for i in (shot.get("images") or []) if isinstance(i, dict) and i.get("data")]
            if not images and shot.get("data"):   # documents written before a match could hold several images
                images = [{"mime": shot.get("mime"), "data": shot["data"]}]
            for n, image in enumerate(images[:8], 1):
                ext = {"image/png": "png", "image/webp": "webp"}.get(image.get("mime"), "jpg")
                try:
                    raw = base64.b64decode(image["data"])
                except (ValueError, TypeError):
                    continue
                if not raw or len(raw) > 8 * 1024 * 1024:
                    continue
                name = f"stat-sheet.{ext}" if len(images) == 1 else f"stat-sheet-{n}.{ext}"
                files.append(discord.File(io.BytesIO(raw), filename=name))
            if files:
                e.set_image(url=f"attachment://{files[0].filename}")
        ch = self._text(guild, cid)
        msg = await ch.send(embed=e, files=files) if files else await ch.send(embed=e)
        return {"posted": True, "message": msg.jump_url}

    async def job_stats_refresh(self, guild, p, job):
        cog = self.bot.get_cog("Stats")
        if cog:
            await cog.update_guild(guild)
        return {}

    async def job_stats_remove(self, guild, p, job):
        cfg = await db.col("stats_channels").find_one({"guild_id": str(guild.id), "key": p["key"]})
        if cfg:
            ch = guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
            if ch:
                try:
                    await ch.delete(reason="Removed from the TeamCore dashboard")
                except discord.HTTPException:
                    pass
            await db.col("stats_channels").delete_one({"_id": cfg["_id"]})
        return {}

    async def job_publish(self, guild, p, job):
        kind, ref = p["kind"], p["ref"]
        gid = str(guild.id)
        if kind == "poll":
            from utils.polls import poll_embed, poll_view
            d = await self._doc("polls", guild, "poll_id", ref)
            ch = self._text(guild, d["channel_id"])
            msg = await ch.send(embed=poll_embed(d, []), view=poll_view(d))
            await db.col("polls").update_one({"_id": d["_id"]}, {"$set": {"message_id": str(msg.id)}})
            return {"message": msg.jump_url}
        if kind == "giveaway":
            from utils.giveaways import giveaway_embed, giveaway_view
            d = await self._doc("giveaways", guild, "giveaway_id", ref)
            ch = self._text(guild, d["channel_id"])
            msg = await ch.send(embed=giveaway_embed(d, 0), view=giveaway_view(d))
            await db.col("giveaways").update_one({"_id": d["_id"]}, {"$set": {"message_id": str(msg.id)}})
            return {"message": msg.jump_url}
        if kind == "countdown":
            from cogs.countdown import countdown_embed
            d = await self._doc("countdowns", guild, "countdown_id", ref)
            ch = self._text(guild, d["channel_id"])
            msg = await ch.send(embed=countdown_embed(d))
            await db.col("countdowns").update_one({"_id": d["_id"]}, {"$set": {"message_id": str(msg.id)}})
            return {"message": msg.jump_url}
        if kind in ("role_panel", "role_panel_delete"):
            from cogs.rolepanel import panel_embed, panel_view, unsafe_reason
            d = await self._doc("role_panels", guild, "panel_id", ref)
            ch = self._channel(guild, d["channel_id"])
            if kind == "role_panel_delete":
                try:
                    if d.get("message_id"):
                        await (await ch.fetch_message(int(d["message_id"]))).delete()
                except discord.HTTPException:
                    pass
                return {}
            warnings = []
            for r in d["roles"]:
                role = guild.get_role(int(r["role_id"]))
                why = unsafe_reason(role, guild) if role else "role no longer exists"
                if why:
                    warnings.append(f"@{r['name']}: {why}")
            msg = None
            if d.get("message_id"):
                try:
                    msg = await ch.fetch_message(int(d["message_id"]))
                    await msg.edit(embed=panel_embed(d), view=panel_view(d))
                except discord.HTTPException:
                    msg = None
            if msg is None:
                self._text(guild, d["channel_id"])
                msg = await ch.send(embed=panel_embed(d), view=panel_view(d))
                await db.col("role_panels").update_one({"_id": d["_id"]}, {"$set": {"message_id": str(msg.id)}})
            return {"message": msg.jump_url, "warnings": warnings}
        if kind == "verification":
            cfg = await db.get_guild_config(gid) or {}
            role = guild.get_role(int(cfg.get("verify_role", 0)))
            if role is None:
                raise JobError("The verification role no longer exists.")
            ch = self._text(guild, cfg.get("verify_channel"))
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"verify:{gid}"))
            e = discord.Embed(title="✅ Verification", description=f"Press the button below to get {role.mention} and unlock the server.", color=0xFF7A1A)
            msg = await ch.send(embed=e, view=view)
            return {"message": msg.jump_url}
        raise JobError("Unknown publish kind.")

    async def job_poll_close(self, guild, p, job):
        d = await self._doc("polls", guild, "poll_id", p["poll_id"])
        cog = self.bot.get_cog("Poll")
        if not cog or d["status"] != "open":
            raise JobError("That poll isn't open.")
        await cog.close_poll(d)
        return {}

    async def job_giveaway_end(self, guild, p, job):
        d = await self._doc("giveaways", guild, "giveaway_id", p["giveaway_id"])
        cog = self.bot.get_cog("Giveaway")
        if not cog or d["status"] != "open":
            raise JobError("That giveaway isn't running.")
        await cog.finish(d)
        return {}

    async def job_giveaway_reroll(self, guild, p, job):
        d = await self._doc("giveaways", guild, "giveaway_id", p["giveaway_id"])
        cog = self.bot.get_cog("Giveaway")
        if not cog or d["status"] != "ended":
            raise JobError("Only ended giveaways can be rerolled.")
        new = await cog._draw(guild, d, int(p.get("count") or 1), set(d.get("winners", [])) | set(d.get("reroll_winners", [])))
        if not new:
            raise JobError("Nobody else qualifies.")
        await db.col("giveaways").update_one({"_id": d["_id"]}, {"$push": {"reroll_winners": {"$each": new}}})
        ch = guild.get_channel(int(d["channel_id"]))
        if ch:
            await ch.send(f"🔄 **Reroll!** New winner(s) of **{d['prize']}**: " + " ".join(f"<@{u}>" for u in new), allowed_mentions=discord.AllowedMentions(users=True))
        return {"winners": new}

    async def job_countdown_delete(self, guild, p, job):
        d = await db.col("countdowns").find_one({"guild_id": str(guild.id), "countdown_id": p["countdown_id"]})
        ch = guild.get_channel(int(d["channel_id"])) if d else None
        if d and ch and d.get("message_id"):
            try:
                await (await ch.fetch_message(int(d["message_id"]))).delete()
            except discord.HTTPException:
                pass
        return {}

    async def job_suggestion_decide(self, guild, p, job):
        from cogs.suggestion import STATUS, suggestion_embed
        s = await self._doc("suggestions", guild, "suggestion_id", p["suggestion_id"])
        status = p["status"]
        upd = {"status": status, "response": (p.get("response") or None), "decided_by": p.get("by_id"), "decided_at": _now()}
        await db.col("suggestions").update_one({"_id": s["_id"]}, {"$set": upd})
        s.update(upd)
        votes = await db.col("suggestion_votes").find({"guild_id": str(guild.id), "suggestion_id": s["suggestion_id"]}).to_list(None)
        up, down = sum(1 for v in votes if v["vote"] == 1), sum(1 for v in votes if v["vote"] == -1)
        ch = guild.get_channel(int(s["channel_id"])) if s.get("channel_id") else None
        try:
            if ch:
                await (await ch.fetch_message(int(s["message_id"]))).edit(embed=suggestion_embed(s, up, down), view=None)
        except discord.HTTPException:
            pass
        try:
            user = guild.get_member(int(s["author_id"])) or await self.bot.fetch_user(int(s["author_id"]))
            dm = discord.Embed(title=f"Your suggestion was {status}", description=f"> {s['text'][:300]}" + (f"\n\n**Staff response:** {s['response']}" if s.get("response") else ""), color=STATUS[status][1])
            dm.set_footer(text=f"{guild.name} • Suggestion #{s['suggestion_id']}")
            await user.send(embed=dm)
        except (discord.HTTPException, ValueError):
            pass
        return {}

    async def job_send_embed(self, guild, p, job):
        ch = self._text(guild, p["channel_id"])
        d = p.get("embed") or {}
        e = discord.Embed(title=d.get("title"), description=(d.get("description") or "").replace("\\n", "\n"), color=d.get("color") or 0xFF7A1A, timestamp=_now())
        if d.get("author_name"):
            e.set_author(name=d["author_name"])
        if d.get("image"):
            e.set_image(url=d["image"])
        if d.get("thumbnail"):
            e.set_thumbnail(url=d["thumbnail"])
        e.set_footer(text=d.get("footer") or guild.name)
        for f in d.get("fields") or []:
            e.add_field(name=f["name"], value=f["value"], inline=bool(f.get("inline")))
        if len(e) > 6000:
            raise JobError("That embed is over Discord's 6000-character limit.")
        role = guild.get_role(int(p["ping_role_id"])) if p.get("ping_role_id") else None
        msg = await ch.send(content=role.mention if role else None, embed=e, allowed_mentions=discord.AllowedMentions(roles=[role]) if role else discord.AllowedMentions.none())
        if p.get("announcement_id"):
            await db.col("announcements").update_one({"guild_id": str(guild.id), "announcement_id": p["announcement_id"]}, {"$set": {"message_id": str(msg.id)}})
        return {"message": msg.jump_url}

    async def job_channel_lock(self, guild, p, job):
        from utils.locks import lock_channel, unlock_channel
        ch = self._channel(guild, p["channel_id"])
        if not isinstance(ch, discord.TextChannel):
            raise JobError("Only text channels can be locked.")
        if p["locked"]:
            r = await lock_channel(ch, guild.me, f"{p.get('reason', 'Dashboard')} (by {p.get('by', 'dashboard')})")
            if r == "failed":
                raise JobError("I couldn't change that channel's permissions.")
        else:
            r = await unlock_channel(ch, guild.me)
            if r == "not_locked":
                raise JobError("TeamCore didn't lock that channel, so there is nothing to restore.")
            if r == "failed":
                raise JobError("I couldn't change that channel's permissions.")
        return {"result": r}

    async def job_backup_create(self, guild, p, job):
        from utils.backup_io import serialize, summarize
        name = p["name"][:50]
        if await db.col("backups").count_documents({"guild_id": str(guild.id)}) >= 10:
            raise JobError("Backup limit reached (10). Delete one first.")
        data = serialize(guild)
        await db.col("backups").insert_one({"guild_id": str(guild.id), "name": name, "name_lower": name.lower(), "data": data, "stats": summarize(data),
                                            "created_at": _now(), "created_by": str(p.get("by", ""))})
        return summarize(data)

    async def job_backup_restore(self, guild, p, job):
        from utils.backup_io import restore
        b = await db.col("backups").find_one({"guild_id": str(guild.id), "name_lower": p["name"].lower()})
        if not b:
            raise JobError("Backup not found.")
        need = discord.Permissions(manage_channels=True, manage_roles=True)
        if not guild.me.guild_permissions.is_superset(need):
            raise JobError("I need Manage Channels and Manage Roles.")
        report = await restore(guild, b["data"], p["mode"] == "full")
        return {k: (v if not isinstance(v, list) else v[:20]) for k, v in report.items()}

    async def job_members_sync(self, guild, p, job):
        """Publish a compact member roster (no bots) so the dashboard directory can list everyone.
        Asked for on demand — the website never holds the bot token itself."""
        from pymongo import ReplaceOne
        gid, at = str(guild.id), _now()
        members = list(guild.members)
        if guild.member_count and len(members) < guild.member_count * 0.9:  # cache looks incomplete → ask Discord
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except discord.HTTPException:
                pass
        rows = []
        for m in members:
            if m.bot:
                continue
            rows.append({"guild_id": gid, "user_id": str(m.id), "username": m.name,
                         "display_name": getattr(m, "display_name", None) or m.name,
                         "avatar": m.display_avatar.url if m.display_avatar else None,
                         "joined_at": m.joined_at, "role_ids": [str(r.id) for r in m.roles if not r.is_default()],
                         "bot": False, "synced_at": at})
        col = db.col("members")
        for i in range(0, len(rows), 400):  # one round trip per 400 members, so the free tier stays quiet
            await col.bulk_write([ReplaceOne({"guild_id": gid, "user_id": r["user_id"]}, r, upsert=True) for r in rows[i:i + 400]], ordered=False)
        await col.delete_many({"guild_id": gid, "user_id": {"$nin": [r["user_id"] for r in rows]}})
        await db.col("member_sync").update_one({"guild_id": gid}, {"$set": {"synced_at": at, "members": len(rows)}}, upsert=True)
        return {"members": len(rows)}

    async def job_member_roles(self, guild, p, job):
        """Add/remove Discord roles for one member, with the same hierarchy checks the bot's own commands use."""
        member = guild.get_member(int(p["user_id"]))
        if member is None:
            try:
                member = await guild.fetch_member(int(p["user_id"]))
            except discord.NotFound:
                raise JobError("That member isn't in this server anymore.")
        add = [guild.get_role(int(r)) for r in (p.get("add") or [])]
        remove = [guild.get_role(int(r)) for r in (p.get("remove") or [])]
        for r in add + remove:
            if r is None:
                raise JobError("One of those roles no longer exists.")
            if r.is_default() or r.managed or r >= guild.me.top_role:
                raise JobError(f"@{r.name} is managed by Discord or above my highest role, so I can't change it.")
        reason = f"TeamCore dashboard ({p.get('by') or 'admin'})"
        if add:
            await member.add_roles(*add, reason=reason)
        if remove:
            await member.remove_roles(*remove, reason=reason)
        await db.col("members").update_one({"guild_id": str(guild.id), "user_id": str(member.id)},
                                          {"$set": {"role_ids": [str(r.id) for r in member.roles if not r.is_default()]}})
        return {"added": [r.name for r in add], "removed": [r.name for r in remove]}

    async def job_team_role_create(self, guild, p, job):
        """Every team is a role: @Team name, in the team's colour. Safe to re-run — an existing role is edited."""
        d = await self._doc("teams", guild, "team_id", int(p["team_id"]))
        if not guild.me.guild_permissions.manage_roles:
            raise JobError("I need the Manage Roles permission to create team roles.")
        name, color = _label(d["name"], 100), int(d.get("color") or 0)
        rid = str(d.get("role_id") or "")
        existing = guild.get_role(int(rid)) if rid.isdigit() else None
        try:
            if existing is not None:
                kwargs = {"name": name, "reason": "TeamCore: team updated"}
                if color:
                    kwargs["colour"] = discord.Colour(color)
                await existing.edit(**kwargs)
                return {"role_id": str(existing.id), "name": existing.name, "updated": True}
            kwargs = {"name": name, "reason": f"TeamCore: team created by {job.get('created_by') or 'dashboard'}"}
            if color:
                kwargs["colour"] = discord.Colour(color)
            role = await guild.create_role(**kwargs)
        except discord.Forbidden:
            raise JobError("I'm not allowed to manage roles here — my role needs to be above the team roles.")
        await db.col("teams").update_one({"guild_id": str(guild.id), "team_id": d["team_id"]},
                                          {"$set": {"role_id": str(role.id)}})
        return {"role_id": str(role.id), "name": role.name}

    async def job_team_role_delete(self, guild, p, job):
        """A deleted team must not leave its role behind."""
        rid = str(p["role_id"] or "")
        role = guild.get_role(int(rid)) if rid.isdigit() else None
        if role is None:
            return {"deleted": False}   # already gone, nothing to do
        try:
            await role.delete(reason="TeamCore: team deleted")
        except discord.Forbidden:
            raise JobError("I'm not allowed to delete that role.")
        return {"deleted": True, "name": role.name}

    async def job_unban(self, guild, p, job):
        try:
            await guild.unban(discord.Object(id=int(p["user_id"])), reason=p.get("reason") or "Appeal approved")
        except discord.NotFound:
            raise JobError("That user isn't banned.")
        return {}

    # ── verification button ──────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        if not (interaction.data or {}).get("custom_id", "").startswith("verify:"):
            return
        await interaction.response.defer(ephemeral=True)
        cfg = await db.get_guild_config(str(interaction.guild.id)) or {}
        role = interaction.guild.get_role(int(cfg.get("verify_role", 0)))
        from cogs.rolepanel import unsafe_reason
        if role is None or unsafe_reason(role, interaction.guild):  # never hand out a role with admin/mod permissions
            return await interaction.followup.send("❌ Verification isn't set up correctly. Please tell a staff member.", ephemeral=True)
        if interaction.user.get_role(role.id):
            return await interaction.followup.send("✅ You're already verified.", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="Verification button")
        except discord.HTTPException:
            return await interaction.followup.send("❌ I couldn't give you the role. Please tell a staff member.", ephemeral=True)
        await interaction.followup.send(f"✅ Verified! You now have {role.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DashboardBridge(bot))
