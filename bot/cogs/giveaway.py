"""cogs/giveaway.py — Giveaways with requirements, auto-ending, rerolls and history."""

import asyncio
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
import database as db
from utils.embeds import error_embed, success_embed
from utils.giveaways import eligible, giveaway_embed, giveaway_view, pick_winners
from utils.permissions import is_manager_or_mod
from utils.timeparse import aware, format_duration, parse_duration
from utils.ui import brand, send_paged
from utils.validation import sanitize_input


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[tuple, asyncio.Lock] = {}
        self.end_due.start()

    def cog_unload(self):
        self.end_due.cancel()

    group = app_commands.Group(name="giveaway", description="Run giveaways", guild_only=True)

    async def _get(self, gid, num):
        return await db.col("giveaways").find_one({"guild_id": str(gid), "giveaway_id": num})

    async def _entries(self, gid, num) -> list:
        rows = await db.col("giveaway_entries").find({"guild_id": str(gid), "giveaway_id": num}).to_list(None)
        return [r["user_id"] for r in rows]

    async def _deny(self, interaction):
        await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)

    # ── commands ─────────────────────────────────────────────
    @group.command(name="create", description="Start a giveaway")
    @app_commands.describe(prize="What's being given away", winners="Number of winners", duration="e.g. 30m, 2h, 1d, 1w",
                           required_role="Only members with this role can enter", min_account_age_days="Minimum Discord account age",
                           channel="Where to post (default: here)")
    async def create(self, interaction: discord.Interaction, prize: app_commands.Range[str, 1, 200],
                     winners: app_commands.Range[int, 1, 20], duration: str, required_role: discord.Role = None,
                     min_account_age_days: app_commands.Range[int, 0, 3650] = 0, channel: discord.TextChannel = None):
        if not await is_manager_or_mod(interaction):
            return await self._deny(interaction)
        td = parse_duration(duration)
        if not td or td.days > 60:
            return await interaction.response.send_message(embed=error_embed("Invalid Duration", "Use `30m`, `2h`, `1d`… (max 60d)."), ephemeral=True)
        channel = channel or interaction.channel
        if not channel.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message(embed=error_embed("Missing Permissions", f"I can't post in {channel.mention}."), ephemeral=True)

        gid, now = str(interaction.guild_id), datetime.now(timezone.utc)
        gw = {
            "guild_id": gid, "giveaway_id": await db.next_seq(gid, "giveaway"), "channel_id": str(channel.id), "message_id": None,
            "prize": sanitize_input(prize, 200), "winners_count": winners, "created_by": str(interaction.user.id), "created_at": now,
            "ends_at": now + td, "status": "open", "required_role_id": str(required_role.id) if required_role else None,
            "min_account_days": min_account_age_days, "winners": [],
        }
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await channel.send(embed=giveaway_embed(gw, 0), view=giveaway_view(gw))
        except discord.HTTPException:
            return await interaction.followup.send(embed=error_embed("Couldn't Post", "I need Send Messages + Embed Links there."), ephemeral=True)
        gw["message_id"] = str(msg.id)
        await db.col("giveaways").insert_one(dict(gw))
        await interaction.followup.send(embed=success_embed("Giveaway Started", f"**#{gw['giveaway_id']}** in {channel.mention}, ends in {format_duration(td)}."), ephemeral=True)

    @group.command(name="end", description="End a giveaway now and draw winners")
    async def end(self, interaction: discord.Interaction, giveaway_id: int):
        if not await is_manager_or_mod(interaction):
            return await self._deny(interaction)
        gw = await self._get(interaction.guild_id, giveaway_id)
        if not gw or gw["status"] != "open":
            return await interaction.response.send_message(embed=error_embed("Not Found", "No active giveaway with that number."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.finish(gw)
        await interaction.followup.send(embed=success_embed("Giveaway Ended", f"#{giveaway_id} has been drawn."), ephemeral=True)

    @group.command(name="reroll", description="Pick new winner(s) for an ended giveaway")
    @app_commands.describe(giveaway_id="Giveaway number", count="How many new winners (default 1)")
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int, count: app_commands.Range[int, 1, 20] = 1):
        if not await is_manager_or_mod(interaction):
            return await self._deny(interaction)
        gw = await self._get(interaction.guild_id, giveaway_id)
        if not gw or gw["status"] != "ended":
            return await interaction.response.send_message(embed=error_embed("Not Found", "Only ended giveaways can be rerolled."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        new = await self._draw(interaction.guild, gw, count, exclude=set(gw.get("winners", [])) | set(gw.get("reroll_winners", [])))
        if not new:
            return await interaction.followup.send(embed=error_embed("No Eligible Entrants", "Nobody else qualifies."), ephemeral=True)
        await db.col("giveaways").update_one({"_id": gw["_id"]}, {"$push": {"reroll_winners": {"$each": new}}})
        channel = interaction.guild.get_channel(int(gw["channel_id"]))
        if channel:
            await channel.send(f"🔄 **Reroll!** New winner(s) of **{gw['prize']}**: " + " ".join(f"<@{u}>" for u in new),
                               allowed_mentions=discord.AllowedMentions(users=True))
        await interaction.followup.send(embed=success_embed("Rerolled", " ".join(f"<@{u}>" for u in new)), ephemeral=True)

    @group.command(name="list", description="Show active giveaways")
    async def list_(self, interaction: discord.Interaction):
        rows = await db.col("giveaways").find({"guild_id": str(interaction.guild_id), "status": "open"}).to_list(None)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("None Active", "There are no active giveaways."), ephemeral=True)
        lines = [f"**#{g['giveaway_id']}** {g['prize']} — ends {discord.utils.format_dt(aware(g['ends_at']), 'R')} · <#{g['channel_id']}>" for g in rows]
        await interaction.response.send_message(embed=brand("🎉 Active Giveaways", "\n".join(lines)))

    @group.command(name="history", description="Show past giveaways and their winners")
    async def history(self, interaction: discord.Interaction):
        rows = await db.col("giveaways").find({"guild_id": str(interaction.guild_id), "status": "ended"}).sort("ended_at", -1).to_list(None)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("No History", "No giveaways have ended yet."), ephemeral=True)
        lines = [
            f"**#{g['giveaway_id']}** {g['prize']} — "
            + (" ".join(f"<@{u}>" for u in (g.get("winners") or []) + (g.get("reroll_winners") or [])) or "*no winner*")
            + f" · {discord.utils.format_dt(aware(g['ended_at']), 'd')}"
            for g in rows
        ]
        pages = [brand("📜 Giveaway History", "\n".join(lines[i:i + 8])) for i in range(0, len(lines), 8)]
        await send_paged(interaction, pages, allowed_mentions=discord.AllowedMentions.none())

    # ── entering ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild_id is None:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("giveaway:"):
            return
        try:
            num = int(cid.split(":")[1])
        except (IndexError, ValueError):
            return
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        async with self._locks.setdefault((gid, num), asyncio.Lock()):
            gw = await self._get(gid, num)
            if not gw or gw["status"] != "open" or aware(gw["ends_at"]) <= datetime.now(timezone.utc):
                return await interaction.followup.send("🔒 This giveaway has ended.", ephemeral=True)
            ok, why = eligible(interaction.user if isinstance(interaction.user, discord.Member) else None, gw)
            if not ok:
                return await interaction.followup.send(f"❌ {why}", ephemeral=True)
            key = {"guild_id": gid, "giveaway_id": num, "user_id": str(interaction.user.id)}
            if await db.col("giveaway_entries").find_one(key):  # one entry per person
                return await interaction.followup.send("✅ You're already entered. Good luck!", ephemeral=True)
            await db.col("giveaway_entries").insert_one({**key, "entered_at": datetime.now(timezone.utc)})
            count = len(await self._entries(gid, num))
            gw["ends_at"] = aware(gw["ends_at"])
            try:
                await interaction.message.edit(embed=giveaway_embed(gw, count))
            except discord.HTTPException:
                pass
        await interaction.followup.send("🎉 You're in! Good luck!", ephemeral=True)

    # ── drawing ──────────────────────────────────────────────
    async def _draw(self, guild: discord.Guild, gw: dict, count: int, exclude: set) -> list:
        """Winners must still be in the server and still meet the requirements at draw time."""
        candidates = [u for u in await self._entries(gw["guild_id"], gw["giveaway_id"])
                      if eligible(guild.get_member(int(u)), gw)[0]]  # eligible() rejects people who left
        return pick_winners(candidates, count, exclude)

    async def finish(self, gw: dict) -> None:
        r = await db.col("giveaways").update_one({"_id": gw["_id"], "status": "open"}, {"$set": {"status": "ended", "ended_at": datetime.now(timezone.utc)}})
        if getattr(r, "modified_count", 1) == 0:  # already ended by the loop / another /giveaway end
            return
        guild = self.bot.get_guild(int(gw["guild_id"]))
        if guild is None:
            return
        winners = await self._draw(guild, gw, gw["winners_count"], set())
        await db.col("giveaways").update_one({"_id": gw["_id"]}, {"$set": {"winners": winners}})
        gw.update(winners=winners, ends_at=aware(gw["ends_at"]))
        entries = len(await self._entries(gw["guild_id"], gw["giveaway_id"]))
        channel = guild.get_channel(int(gw["channel_id"]))
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(int(gw["message_id"]))
            await msg.edit(embed=giveaway_embed(gw, entries, ended=True), view=giveaway_view(gw, disabled=True))
            text = (f"🎊 Congratulations " + " ".join(f"<@{u}>" for u in winners) + f"! You won **{gw['prize']}**!") if winners \
                else f"😕 No valid entries for **{gw['prize']}**."
            await msg.reply(text, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException:
            pass

    @tasks.loop(seconds=20)
    async def end_due(self):
        if not db.is_connected():
            return
        for gw in await db.col("giveaways").find({"status": "open", "ends_at": {"$lte": datetime.now(timezone.utc)}}).to_list(None):
            try:
                await self.finish(gw)
            except Exception as e:
                print(f"⚠️ giveaway end failed: {e}")

    @end_due.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
