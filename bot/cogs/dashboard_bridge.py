"""cogs/dashboard_bridge.py — the bot's side of the website: publishes server info, runs dashboard jobs.

The website never has the bot token. It writes jobs into `bot_jobs`; this cog (the only thing holding the token) claims
and executes them, then writes the result back. Jobs are woken by a MongoDB change stream (no polling); if change streams
aren't available it falls back to a slow poll. The bot works fine without the website — this cog is purely additive.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
from discord.ext import commands, tasks

import database as db
from shared.jobs import DONE, FAILED, PENDING, RUNNING


class JobError(Exception):
    """A failure whose message is safe to show to the dashboard user."""


def _now():
    return datetime.now(timezone.utc)


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
        self.heartbeat.start()

    async def cog_load(self):
        self._worker_task = asyncio.create_task(self._worker())

    def cog_unload(self):
        self.heartbeat.cancel()
        if self._worker_task:
            self._worker_task.cancel()

    # ── server cache (so the website can show channel/role pickers without the bot token) ──
    async def publish(self, guild: discord.Guild):
        if db.is_connected():
            doc = build_cache(guild)
            await db.col("servers").replace_one({"guild_id": doc["guild_id"]}, doc, upsert=True)

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
            return
        for g in self.bot.guilds:  # one write per server per start
            try:
                await self.publish(g)
            except Exception as e:
                print(f"⚠️ cache publish failed for {g.id}: {e}")

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

    # ── jobs ─────────────────────────────────────────────────
    async def job_refresh_cache(self, guild, p, job):
        await self.publish(guild)
        return {}

    async def job_record_changed(self, guild, p, job):
        self.bot.dispatch("team_record_changed", str(guild.id), p["team_id"])
        return {}

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
