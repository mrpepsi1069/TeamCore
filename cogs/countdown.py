"""cogs/countdown.py — Countdowns that tick live (Discord timestamps) and announce when they finish."""

from datetime import datetime, timezone as _tz
from zoneinfo import ZoneInfo, available_timezones
import discord
from discord import app_commands
from discord.ext import commands, tasks
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import is_staff_or_mod
from utils.timeparse import aware, parse_local_datetime
from utils.ui import brand
from utils.validation import sanitize_input

UTC = _tz.utc  # (command parameters are named `timezone`, so avoid shadowing)
COMMON_TZ = ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London", "Europe/Paris", "Asia/Tokyo", "Australia/Sydney"]


async def tz_autocomplete(interaction: discord.Interaction, current: str):
    try:
        zones = sorted(available_timezones())
    except Exception:
        zones = COMMON_TZ
    cur = current.lower().replace(" ", "_")
    pool = [z for z in COMMON_TZ if cur in z.lower()] + [z for z in zones if cur in z.lower() and z not in COMMON_TZ]
    return [app_commands.Choice(name=z, value=z) for z in pool[:25]]


def countdown_embed(cd: dict, done: bool = False) -> discord.Embed:
    ends = aware(cd["ends_at"])
    if done:
        e = brand(f"✅ {cd['title']}", f"This countdown finished {discord.utils.format_dt(ends, 'R')}.", 0x57F287)
    else:
        # Discord renders <t:..:R> as a live ticking countdown on every client — no bot edits needed.
        e = brand(f"⏳ {cd['title']}", f"{discord.utils.format_dt(ends, 'R')}\n{discord.utils.format_dt(ends, 'F')}", 0x5865F2)
    e.add_field(name="Timezone", value=cd.get("timezone", "UTC"), inline=True)
    e.set_footer(text=f"TeamCore • Countdown #{cd['countdown_id']}")
    return e


class Countdown(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.finish_due.start()

    def cog_unload(self):
        self.finish_due.cancel()

    group = app_commands.Group(name="countdown", description="Countdown timers", guild_only=True)

    async def _staff(self, interaction) -> bool:
        if await is_staff_or_mod(interaction):
            return True
        await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Staff role or Manage Messages."), ephemeral=True)
        return False

    async def _get(self, gid, num):
        return await db.col("countdowns").find_one({"guild_id": str(gid), "countdown_id": num})

    @group.command(name="create", description="Create a countdown")
    @app_commands.describe(title="What are we counting down to?", date="Date as YYYY-MM-DD", time="24h time as HH:MM",
                           timezone="e.g. America/New_York (default UTC)", ping_role="Role to ping when it ends", channel="Where to post")
    @app_commands.autocomplete(timezone=tz_autocomplete)
    async def create(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 100], date: str, time: str,
                     timezone: str = "UTC", ping_role: discord.Role = None, channel: discord.TextChannel = None):
        if not await self._staff(interaction):
            return
        ends = parse_local_datetime(date, time, timezone)
        if ends is None:
            return await interaction.response.send_message(embed=error_embed("Invalid Date/Time", "Use `YYYY-MM-DD`, `HH:MM` (24h) and a valid timezone like `America/New_York`."), ephemeral=True)
        if ends <= datetime.now(UTC):
            return await interaction.response.send_message(embed=error_embed("In the Past", "That time has already passed."), ephemeral=True)
        channel = channel or interaction.channel
        gid = str(interaction.guild_id)
        cd = {"guild_id": gid, "countdown_id": await db.next_seq(gid, "countdown"), "channel_id": str(channel.id), "message_id": None,
              "title": sanitize_input(title, 100), "ends_at": ends, "timezone": timezone, "role_id": str(ping_role.id) if ping_role else None,
              "created_by": str(interaction.user.id), "status": "open"}
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await channel.send(embed=countdown_embed(cd))
        except discord.HTTPException:
            return await interaction.followup.send(embed=error_embed("Couldn't Post", f"I can't post in {channel.mention}."), ephemeral=True)
        cd["message_id"] = str(msg.id)
        await db.col("countdowns").insert_one(dict(cd))
        await interaction.followup.send(embed=success_embed("Countdown Created", f"**#{cd['countdown_id']}** → {discord.utils.format_dt(ends, 'F')}"), ephemeral=True)

    @group.command(name="edit", description="Change an active countdown")
    @app_commands.autocomplete(timezone=tz_autocomplete)
    async def edit(self, interaction: discord.Interaction, countdown_id: int, title: app_commands.Range[str, 1, 100] = None,
                   date: str = None, time: str = None, timezone: str = None, ping_role: discord.Role = None):
        if not await self._staff(interaction):
            return
        cd = await self._get(interaction.guild_id, countdown_id)
        if not cd or cd["status"] != "open":
            return await interaction.response.send_message(embed=error_embed("Not Found", "No active countdown with that number."), ephemeral=True)
        updates = {}
        if title:
            updates["title"] = sanitize_input(title, 100)
        if ping_role:
            updates["role_id"] = str(ping_role.id)
        if date or time or timezone:
            tz = timezone or cd.get("timezone", "UTC")
            try:  # unspecified parts default to the current local values in the (new) timezone
                local = aware(cd["ends_at"]).astimezone(ZoneInfo(tz))
            except Exception:
                return await interaction.response.send_message(embed=error_embed("Invalid Timezone", f"`{tz}` isn't a valid timezone."), ephemeral=True)
            ends = parse_local_datetime(date or local.strftime("%Y-%m-%d"), time or local.strftime("%H:%M"), tz)
            if ends is None or ends <= datetime.now(UTC):
                return await interaction.response.send_message(embed=error_embed("Invalid Date/Time", "Check the date/time/timezone — it must be in the future."), ephemeral=True)
            updates.update(ends_at=ends, timezone=tz)
        if not updates:
            return await interaction.response.send_message(embed=error_embed("Nothing to Change", "Provide something to edit."), ephemeral=True)
        await db.col("countdowns").update_one({"_id": cd["_id"]}, {"$set": updates})
        cd.update(updates)
        await self._refresh_message(cd)
        await interaction.response.send_message(embed=success_embed("Countdown Updated", f"#{countdown_id} updated."), ephemeral=True)

    @group.command(name="delete", description="Delete a countdown")
    async def delete(self, interaction: discord.Interaction, countdown_id: int):
        if not await self._staff(interaction):
            return
        cd = await self._get(interaction.guild_id, countdown_id)
        if not cd:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No countdown with that number."), ephemeral=True)
        await db.col("countdowns").delete_one({"_id": cd["_id"]})
        msg = await self._message(cd)
        if msg:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=success_embed("Deleted", f"Countdown #{countdown_id} removed."), ephemeral=True)

    @group.command(name="list", description="Show this server's countdowns")
    async def list_(self, interaction: discord.Interaction):
        rows = await db.col("countdowns").find({"guild_id": str(interaction.guild_id)}).to_list(None)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("None", "No countdowns yet."), ephemeral=True)
        rows.sort(key=lambda c: aware(c["ends_at"]))
        lines = [f"{'⏳' if c['status'] == 'open' else '✅'} **#{c['countdown_id']}** {c['title']} — {discord.utils.format_dt(aware(c['ends_at']), 'R')}" for c in rows[:20]]
        await interaction.response.send_message(embed=brand("⏳ Countdowns", "\n".join(lines)))

    # ── helpers / loop ───────────────────────────────────────
    async def _message(self, cd: dict):
        guild = self.bot.get_guild(int(cd["guild_id"]))
        channel = guild.get_channel(int(cd["channel_id"])) if guild else None
        if channel is None or not cd.get("message_id"):
            return None
        try:
            return await channel.fetch_message(int(cd["message_id"]))
        except discord.HTTPException:
            return None

    async def _refresh_message(self, cd: dict, done: bool = False):
        msg = await self._message(cd)
        if msg:
            try:
                await msg.edit(embed=countdown_embed(cd, done))
            except discord.HTTPException:
                pass
        return msg

    @tasks.loop(seconds=20)
    async def finish_due(self):
        if not db.is_connected():
            return
        for cd in await db.col("countdowns").find({"status": "open", "ends_at": {"$lte": datetime.now(UTC)}}).to_list(None):
            r = await db.col("countdowns").update_one({"_id": cd["_id"], "status": "open"}, {"$set": {"status": "completed"}})
            if getattr(r, "modified_count", 1) == 0:
                continue
            msg = await self._refresh_message(cd, done=True)
            guild = self.bot.get_guild(int(cd["guild_id"]))
            channel = guild.get_channel(int(cd["channel_id"])) if guild else None
            if channel:
                ping = f"<@&{cd['role_id']}> " if cd.get("role_id") else ""
                try:
                    await channel.send(f"{ping}🎉 **{cd['title']}** has arrived!", reference=msg, allowed_mentions=discord.AllowedMentions(roles=True))
                except discord.HTTPException:
                    pass

    @finish_due.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Countdown(bot))
