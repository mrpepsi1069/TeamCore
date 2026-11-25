"""cogs/poll.py — Interactive polls that survive restarts and close themselves."""

import asyncio
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import has_manager_perms, is_staff_or_mod
from utils.polls import apply_vote, poll_embed, poll_view
from utils.timeparse import aware, format_duration, parse_duration
from utils.validation import sanitize_input

MAX_DURATION_DAYS = 30


class Poll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[tuple, asyncio.Lock] = {}
        self.close_expired.start()

    def cog_unload(self):
        self.close_expired.cancel()

    group = app_commands.Group(name="poll", description="Create and manage polls", guild_only=True)

    def _lock(self, gid, pid) -> asyncio.Lock:
        return self._locks.setdefault((gid, pid), asyncio.Lock())

    async def _votes(self, gid, pid) -> list:
        return await db.col("poll_votes").find({"guild_id": str(gid), "poll_id": pid}).to_list(None)

    async def _get(self, gid, pid):
        return await db.col("polls").find_one({"guild_id": str(gid), "poll_id": pid})

    # ── commands ─────────────────────────────────────────────
    @group.command(name="create", description="Create a poll")
    @app_commands.describe(
        question="The question", options="2-10 choices separated by |  (e.g. Yes | No | Maybe)",
        duration="How long it runs: 30m, 2h, 1d, 1h30m (max 30d)",
        anonymous="Hide who voted for what", multiple="Allow voting for several options",
    )
    async def create(self, interaction: discord.Interaction, question: app_commands.Range[str, 1, 200], options: str,
                     duration: str = "1d", anonymous: bool = False, multiple: bool = False):
        if not await is_staff_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Staff role or Manage Messages."), ephemeral=True)
        opts = [sanitize_input(o, 70) for o in options.replace("\n", "|").split("|")]
        opts = [o for o in opts if o]
        if len(set(o.lower() for o in opts)) != len(opts):
            return await interaction.response.send_message(embed=error_embed("Duplicate Options", "Each choice must be different."), ephemeral=True)
        if not 2 <= len(opts) <= 10:
            return await interaction.response.send_message(embed=error_embed("Invalid Options", "Give between 2 and 10 choices separated by `|`."), ephemeral=True)
        td = parse_duration(duration)
        if not td or td.days > MAX_DURATION_DAYS:
            return await interaction.response.send_message(embed=error_embed("Invalid Duration", "Use something like `30m`, `2h`, `1d` (max 30d)."), ephemeral=True)

        gid = str(interaction.guild_id)
        now = datetime.now(timezone.utc)
        poll = {
            "guild_id": gid, "poll_id": await db.next_seq(gid, "poll"), "channel_id": str(interaction.channel_id), "message_id": None,
            "question": sanitize_input(question, 200), "options": opts, "anonymous": anonymous, "multiple": multiple,
            "created_by": str(interaction.user.id), "created_at": now, "ends_at": now + td, "status": "open",
        }
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await interaction.channel.send(embed=poll_embed(poll, []), view=poll_view(poll))
        except discord.HTTPException:
            return await interaction.followup.send(embed=error_embed("Couldn't Post", "I need Send Messages + Embed Links here."), ephemeral=True)
        poll["message_id"] = str(msg.id)
        await db.col("polls").insert_one(dict(poll))
        await interaction.followup.send(embed=success_embed("Poll Created", f"Poll **#{poll['poll_id']}** runs for {format_duration(td)}."), ephemeral=True)

    @group.command(name="end", description="End a poll early")
    async def end(self, interaction: discord.Interaction, poll_id: int):
        poll = await self._get(interaction.guild_id, poll_id)
        if not poll or poll["status"] != "open":
            return await interaction.response.send_message(embed=error_embed("Not Found", "No open poll with that number."), ephemeral=True)
        if str(interaction.user.id) != poll["created_by"] and not await has_manager_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "Only the creator or a Manager can end it."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.close_poll(poll)
        await interaction.followup.send(embed=success_embed("Poll Ended", f"Poll #{poll_id} is closed."), ephemeral=True)

    @group.command(name="results", description="Show a poll's current results")
    async def results(self, interaction: discord.Interaction, poll_id: int):
        poll = await self._get(interaction.guild_id, poll_id)
        if not poll:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No poll with that number."), ephemeral=True)
        poll["ends_at"] = aware(poll["ends_at"])
        await interaction.response.send_message(
            embed=poll_embed(poll, await self._votes(interaction.guild_id, poll_id), closed=poll["status"] != "open"), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ── voting ───────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild_id is None:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("poll:"):
            return
        try:
            _, pid, idx = cid.split(":")
            pid, idx = int(pid), int(idx)
        except ValueError:
            return
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)

        async with self._lock(gid, pid):
            poll = await self._get(gid, pid)
            if not poll or poll["status"] != "open" or aware(poll["ends_at"]) <= datetime.now(timezone.utc):
                return await interaction.followup.send("🔒 This poll has ended.", ephemeral=True)
            if idx >= len(poll["options"]):
                return await interaction.followup.send("❌ Invalid option.", ephemeral=True)

            coll = db.col("poll_votes")
            key = {"guild_id": gid, "poll_id": pid, "user_id": str(interaction.user.id)}
            existing = await coll.find_one(key)
            choices = apply_vote(existing["choices"] if existing else [], idx, poll["multiple"])
            if choices:
                await coll.update_one(key, {"$set": {"choices": choices}}, upsert=True)
            elif existing:
                await coll.delete_one(key)

            poll["ends_at"] = aware(poll["ends_at"])
            try:
                await interaction.message.edit(embed=poll_embed(poll, await self._votes(gid, pid)))
            except discord.HTTPException:
                pass

        if choices:
            await interaction.followup.send("✅ Your vote: " + ", ".join(f"**{poll['options'][c]}**" for c in choices), ephemeral=True)
        else:
            await interaction.followup.send("↩️ Your vote was removed.", ephemeral=True)

    # ── closing ──────────────────────────────────────────────
    async def close_poll(self, poll: dict) -> None:
        # Atomic open->closed flip: the background loop and /poll end can race, only one may post results
        r = await db.col("polls").update_one({"_id": poll["_id"], "status": "open"}, {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc)}})
        if getattr(r, "modified_count", 1) == 0:
            return
        poll["ends_at"] = aware(poll["ends_at"])
        embed = poll_embed(poll, await self._votes(poll["guild_id"], poll["poll_id"]), closed=True)
        guild = self.bot.get_guild(int(poll["guild_id"]))
        channel = guild.get_channel(int(poll["channel_id"])) if guild else None
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(int(poll["message_id"]))
            await msg.edit(embed=embed, view=None)
            await msg.reply(content="📊 **Poll closed!** Final results above.", allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @tasks.loop(seconds=20)
    async def close_expired(self):
        if not db.is_connected():
            return
        for poll in await db.col("polls").find({"status": "open", "ends_at": {"$lte": datetime.now(timezone.utc)}}).to_list(None):
            try:
                await self.close_poll(poll)
            except Exception as e:
                print(f"⚠️ poll close failed: {e}")

    @close_expired.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Poll(bot))
