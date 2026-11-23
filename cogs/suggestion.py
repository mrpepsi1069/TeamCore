"""cogs/suggestion.py — Server suggestions with voting, staff responses and author notifications."""

import asyncio
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import has_manager_perms, is_staff_or_mod
from utils.ui import brand, send_paged
from utils.validation import sanitize_input

STATUS = {"pending": ("🟡 Pending", 0xFEE75C), "approved": ("✅ Approved", 0x57F287), "denied": ("❌ Denied", 0xED4245)}


def suggestion_embed(s: dict, up: int, down: int) -> discord.Embed:
    label, color = STATUS[s["status"]]
    e = brand(f"💡 Suggestion #{s['suggestion_id']}", s["text"], color)
    e.add_field(name="Status", value=label, inline=True)
    e.add_field(name="Votes", value=f"👍 **{up}**  ·  👎 **{down}**", inline=True)
    e.add_field(name="Author", value=f"<@{s['author_id']}>", inline=True)
    if s.get("response"):
        e.add_field(name=f"Response from staff", value=s["response"][:1000], inline=False)
    return e


def vote_view(s: dict, up: int = 0, down: int = 0) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label=str(up), emoji="👍", style=discord.ButtonStyle.success, custom_id=f"suggestion:{s['suggestion_id']}:up"))
    v.add_item(discord.ui.Button(label=str(down), emoji="👎", style=discord.ButtonStyle.danger, custom_id=f"suggestion:{s['suggestion_id']}:down"))
    return v


class Suggestion(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[tuple, asyncio.Lock] = {}

    group = app_commands.Group(name="suggestion", description="Community suggestions", guild_only=True)

    async def _get(self, gid, num):
        return await db.col("suggestions").find_one({"guild_id": str(gid), "suggestion_id": num})

    async def _counts(self, gid, num) -> tuple:
        votes = await db.col("suggestion_votes").find({"guild_id": str(gid), "suggestion_id": num}).to_list(None)
        return sum(1 for v in votes if v["vote"] == 1), sum(1 for v in votes if v["vote"] == -1)

    # ── commands ─────────────────────────────────────────────
    @group.command(name="submit", description="Submit a suggestion")
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.guild_id, i.user.id))
    async def submit(self, interaction: discord.Interaction, suggestion: app_commands.Range[str, 5, 1000]):
        gid = str(interaction.guild_id)
        cfg = await db.get_guild_config(gid) or {}
        channel = interaction.guild.get_channel(int(cfg["suggestion_channel"])) if cfg.get("suggestion_channel") else None
        if channel is None:
            return await interaction.response.send_message(embed=error_embed("Not Set Up", "No suggestion channel yet. A manager can set one with `/suggestion channel`."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        s = {"guild_id": gid, "suggestion_id": await db.next_seq(gid, "suggestion"), "author_id": str(interaction.user.id),
             "text": sanitize_input(suggestion, 1000), "status": "pending", "response": None, "created_at": datetime.now(timezone.utc)}
        try:
            msg = await channel.send(embed=suggestion_embed(s, 0, 0), view=vote_view(s))
        except discord.HTTPException:
            return await interaction.followup.send(embed=error_embed("Couldn't Post", f"I can't post in {channel.mention}."), ephemeral=True)
        s.update(channel_id=str(channel.id), message_id=str(msg.id))
        await db.col("suggestions").insert_one(dict(s))
        await interaction.followup.send(embed=success_embed("Suggestion Submitted", f"Posted as **#{s['suggestion_id']}** in {channel.mention}."), ephemeral=True)

    async def _decide(self, interaction: discord.Interaction, num: int, status: str, response: str | None):
        if not await is_staff_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Staff role or Manage Messages."), ephemeral=True)
        s = await self._get(interaction.guild_id, num)
        if not s:
            return await interaction.response.send_message(embed=error_embed("Not Found", f"No suggestion #{num}."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        upd = {"status": status, "response": sanitize_input(response, 1000) if response else None, "decided_by": str(interaction.user.id), "decided_at": datetime.now(timezone.utc)}
        await db.col("suggestions").update_one({"_id": s["_id"]}, {"$set": upd})
        s.update(upd)
        up, down = await self._counts(interaction.guild_id, num)
        ch = interaction.guild.get_channel(int(s["channel_id"]))
        try:  # voting closes once decided (buttons removed)
            msg = await ch.fetch_message(int(s["message_id"])) if ch else None
            if msg:
                await msg.edit(embed=suggestion_embed(s, up, down), view=None)
        except discord.HTTPException:
            pass
        notified = True
        try:
            user = interaction.guild.get_member(int(s["author_id"])) or await self.bot.fetch_user(int(s["author_id"]))
            dm = brand(f"Your suggestion was {status}", f"> {s['text'][:300]}\n\n" + (f"**Staff response:** {s['response']}" if s["response"] else ""), STATUS[status][1])
            dm.set_footer(text=f"{interaction.guild.name} • Suggestion #{num}")
            await user.send(embed=dm)
        except (discord.HTTPException, discord.Forbidden):
            notified = False
        await interaction.followup.send(embed=success_embed(f"Suggestion {status.title()}", f"#{num} marked {status}." + ("" if notified else " (Couldn't DM the author.)")), ephemeral=True)

    @group.command(name="approve", description="Approve a suggestion (Staff)")
    async def approve(self, interaction: discord.Interaction, suggestion_id: int, response: app_commands.Range[str, 1, 1000] = None):
        await self._decide(interaction, suggestion_id, "approved", response)

    @group.command(name="deny", description="Deny a suggestion (Staff)")
    async def deny(self, interaction: discord.Interaction, suggestion_id: int, response: app_commands.Range[str, 1, 1000] = None):
        await self._decide(interaction, suggestion_id, "denied", response)

    @group.command(name="channel", description="Set the suggestion channel (Manager)")
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await has_manager_perms(interaction) and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        await db.set_guild_config(str(interaction.guild_id), {"suggestion_channel": channel.id})
        await interaction.response.send_message(embed=success_embed("Suggestion Channel Set", f"Suggestions will be posted in {channel.mention}."), ephemeral=True)

    @group.command(name="history", description="Browse past suggestions")
    @app_commands.choices(status=[app_commands.Choice(name=v[0], value=k) for k, v in STATUS.items()])
    async def history(self, interaction: discord.Interaction, status: str = None, mine: bool = False):
        q = {"guild_id": str(interaction.guild_id)}
        if status:
            q["status"] = status
        if mine:
            q["author_id"] = str(interaction.user.id)
        rows = sorted(await db.col("suggestions").find(q).to_list(None), key=lambda s: -s["suggestion_id"])
        if not rows:
            return await interaction.response.send_message(embed=error_embed("Nothing Found", "No suggestions match."), ephemeral=True)
        lines = [f"**#{s['suggestion_id']}** {STATUS[s['status']][0]} — {s['text'][:80]}{'…' if len(s['text']) > 80 else ''}" for s in rows]
        pages = [brand("💡 Suggestion History", "\n".join(lines[i:i + 8])) for i in range(0, len(lines), 8)]
        await send_paged(interaction, pages, ephemeral=True)

    # ── voting ───────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild_id is None:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("suggestion:"):
            return
        try:
            _, num, direction = cid.split(":")
            num = int(num)
        except ValueError:
            return
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        async with self._locks.setdefault((gid, num), asyncio.Lock()):
            s = await self._get(gid, num)
            if not s or s["status"] != "pending":
                return await interaction.followup.send("🔒 Voting on this suggestion is closed.", ephemeral=True)
            want = 1 if direction == "up" else -1
            key = {"guild_id": gid, "suggestion_id": num, "user_id": str(interaction.user.id)}
            existing = await db.col("suggestion_votes").find_one(key)
            if existing and existing["vote"] == want:  # clicking the same button again withdraws the vote
                await db.col("suggestion_votes").delete_one(key)
                note = "↩️ Vote removed."
            else:  # one vote per person; clicking the other button switches it
                await db.col("suggestion_votes").update_one(key, {"$set": {"vote": want}}, upsert=True)
                note = "👍 You voted up." if want == 1 else "👎 You voted down."
            up, down = await self._counts(gid, num)
            try:
                await interaction.message.edit(embed=suggestion_embed(s, up, down), view=vote_view(s, up, down))
            except discord.HTTPException:
                pass
        await interaction.followup.send(note, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Suggestion(bot))
