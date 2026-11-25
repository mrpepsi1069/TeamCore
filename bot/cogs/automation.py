"""cogs/automation.py — scheduled messages, reminders and join/leave workflows created on the dashboard."""

import time
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

import database as db

NO_PINGS = discord.AllowedMentions(users=True, roles=False, everyone=False)  # dashboard text can never @everyone
STEP = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def next_time(when: datetime, repeat: str, now: datetime) -> datetime | None:
    """Next run strictly after `now` (skips missed runs rather than spamming catch-up messages)."""
    step = STEP.get(repeat)
    if not step:
        return None
    while when <= now:
        when += step
    return when


def render(template: str, member: discord.Member) -> str:
    return (template.replace("{user}", member.mention).replace("{username}", member.display_name).replace("{server}", member.guild.name))


class Automation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._wf_cache: dict[tuple, tuple] = {}
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    # ── scheduled messages + reminders (one loop, two indexed queries every 30s) ──
    @tasks.loop(seconds=30)
    async def tick(self):
        if not db.is_connected():
            return
        now = datetime.now(timezone.utc)
        for m in await db.col("scheduled_messages").find({"enabled": True, "send_at": {"$lte": now}}).limit(25).to_list(25):
            nxt = next_time(m["send_at"] if m["send_at"].tzinfo else m["send_at"].replace(tzinfo=timezone.utc), m.get("repeat", "none"), now)
            # claim by matching the old send_at, so two bot instances never send the same message twice
            upd = {"send_at": nxt, "last_sent": now} if nxt else {"enabled": False, "last_sent": now}
            claimed = await db.col("scheduled_messages").update_one({"_id": m["_id"], "send_at": m["send_at"]}, {"$set": upd})
            if getattr(claimed, "modified_count", 1) == 0:
                continue
            await self._send_scheduled(m)
        for r in await db.col("reminders").find({"status": "pending", "remind_at": {"$lte": now}}).limit(25).to_list(25):
            nxt = next_time(r["remind_at"] if r["remind_at"].tzinfo else r["remind_at"].replace(tzinfo=timezone.utc), r.get("repeat", "none"), now)
            upd = {"remind_at": nxt, "last_sent": now} if nxt else {"status": "sent", "last_sent": now}
            claimed = await db.col("reminders").update_one({"_id": r["_id"], "remind_at": r["remind_at"]}, {"$set": upd})
            if getattr(claimed, "modified_count", 1) == 0:
                continue
            await self._send_reminder(r)

    @tick.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def _send_scheduled(self, m: dict):
        guild = self.bot.get_guild(int(m["guild_id"]))
        ch = guild.get_channel(int(m["channel_id"])) if guild else None
        if ch is None:
            return
        try:
            if m.get("title"):
                await ch.send(embed=discord.Embed(title=m["title"], description=m["content"], color=0xFF7A1A), allowed_mentions=NO_PINGS)
            else:
                await ch.send(m["content"], allowed_mentions=NO_PINGS)
        except discord.HTTPException as e:
            print(f"⚠️ scheduled message {m['_id']} failed: {e}")

    async def _send_reminder(self, r: dict):
        text = f"⏰ **Reminder:** {r['text']}"
        try:
            if r.get("channel_id"):
                guild = self.bot.get_guild(int(r["guild_id"]))
                ch = guild.get_channel(int(r["channel_id"])) if guild else None
                if ch:
                    await ch.send((f"<@{r['user_id']}> " if r.get("user_id") else "") + text, allowed_mentions=NO_PINGS if not r.get("user_id") else discord.AllowedMentions(users=True))
            elif r.get("user_id"):
                user = self.bot.get_user(int(r["user_id"])) or await self.bot.fetch_user(int(r["user_id"]))
                await user.send(text)
        except discord.HTTPException as e:
            print(f"⚠️ reminder {r['_id']} failed: {e}")

    # ── workflows (member join / leave) ──────────────────────
    async def _workflows(self, guild_id: int, trigger: str) -> list:
        key = (guild_id, trigger)
        hit = self._wf_cache.get(key)
        if hit and time.monotonic() - hit[0] < 60:  # at most one query a minute per server
            return hit[1]
        rows = await db.col("workflows").find({"guild_id": str(guild_id), "trigger": trigger, "enabled": True}).to_list(20) if db.is_connected() else []
        self._wf_cache[key] = (time.monotonic(), rows)
        return rows

    async def _run(self, member: discord.Member, trigger: str):
        for wf in await self._workflows(member.guild.id, trigger):
            try:
                a = wf["action"]
                if a == "send_message" and wf.get("channel_id"):
                    ch = member.guild.get_channel(int(wf["channel_id"]))
                    if ch:
                        await ch.send(render(wf.get("message", ""), member), allowed_mentions=NO_PINGS)
                elif a == "add_role" and wf.get("role_id") and trigger == "member_join":
                    from cogs.rolepanel import unsafe_reason
                    role = member.guild.get_role(int(wf["role_id"]))
                    if role and not unsafe_reason(role, member.guild):  # workflows can't hand out moderator roles
                        await member.add_roles(role, reason=f"Workflow: {wf['name']}")
                elif a == "dm_member" and trigger == "member_join":
                    await member.send(render(wf.get("message", ""), member))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.bot:
            await self._run(member, "member_join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not member.bot:
            await self._run(member, "member_leave")


async def setup(bot: commands.Bot):
    await bot.add_cog(Automation(bot))
