"""cogs/matchmaking.py — Scrimmage matchmaking board.

Flow:
  1. /matchmaking creates (or reuses) a "Core Match Making" channel in the
     current server and posts a persistent board embed with two game
     buttons.
  2. Clicking a game button shows an ephemeral (private) menu to pick a match
     type (2v2-11v11).
  3. Picking a size opens a modal asking for a time ("now" for available now).
  4. On submit, the bot checks the GLOBAL queue (pooled across every server
     the bot is in) for someone else waiting on the same game + match type:
       - Match found  -> both players are DMed (each told which server the
         other is from) and removed from the queue everywhere they were
         posted.
       - No match yet -> the request is broadcast into the Core Match Making
         channel of every server the bot is in (auto-creating that channel
         wherever it doesn't exist yet), and the player is DMed automatically
         the moment someone anywhere queues for the same game + match type.
  5. Every posted copy of a request is automatically removed after 24 hours
     if nobody matches it.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import COLORS
from utils.embeds import success_embed, error_embed, info_embed
import database as db

MATCH_SIZES = ["2v2", "3v3", "4v4", "5v5", "6v6", "7v7", "8v8", "9v9", "10v10", "11v11"]
GAMES = ("Football Fusion 3", "Ultimate Football", "Basketball")
BASKETBALL_SIZES = ["1v1", "2v2", "3v3", "4v4", "5v5"]
GAME_SIZES = {GAMES[2]: BASKETBALL_SIZES}  # any game not listed uses MATCH_SIZES
GAME_EMOJI = {GAMES[0]: "⚽", GAMES[1]: "🏈", GAMES[2]: "🏀"}


def _sizes_for(game: str) -> list:
    return GAME_SIZES.get(game, MATCH_SIZES)


def _board_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 Core Match Making",
        description=(
            "Looking for a scrimmage? Pick your game below to get started!\n\n"
            + "\n".join(f"**{GAME_EMOJI[g]} {g}**" for g in GAMES)
            + "\n\nYou'll then choose a match type (2v2–11v11 for football, 1v1–5v5 for basketball) and a time. "
            "Your request gets posted across **every server** using this bot, "
            "and we'll DM you the moment anyone, anywhere, is looking for the same match!"
        ),
        color=COLORS["primary"],
    )
    embed.set_footer(text="TeamCore • Matchmaking")
    return embed
MATCHMAKING_CHANNEL_NAME = "Core Match Making"
QUEUE_EXPIRY_HOURS = 24
AVAILABLE_NOW_ALIASES = {"now", "available now", "available", "asap", "anytime"}


# ─────────────────────────────────────────────
# Per-guild locks
#
# Two separate namespaces (channel creation vs. board-embed creation) so
# _ensure_matchmaking_board can safely hold its own lock while calling into
# _ensure_matchmaking_channel, which acquires a *different* lock — avoids
# any risk of deadlocking on a non-reentrant asyncio.Lock.
# ─────────────────────────────────────────────

_locks: dict[tuple, asyncio.Lock] = {}


def _get_lock(namespace: str, guild_id: int) -> asyncio.Lock:
    key = (namespace, guild_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _display_time(time_text: str) -> str:
    return "Available now" if time_text.strip().lower() in AVAILABLE_NOW_ALIASES else time_text.strip()


def _size_select_embed(game: str) -> discord.Embed:
    return discord.Embed(
        title=f"🎮 {game}",
        description=(
            "Select your **match type** below.\n\n"
            "*Only you can see this message.*"
        ),
        color=COLORS["primary"],
    )


def _board_post_embed(user: discord.abc.User, game: str, size: str, display_time: str, origin_guild_name: str = None) -> discord.Embed:
    desc = f"🔎 **{discord.utils.escape_markdown(user.display_name)}** ({user.mention})"
    if origin_guild_name:
        desc += f" (from **{origin_guild_name}**)"
    desc += f" is looking for a **{size} {game}** match\n🕐 {display_time}"

    embed = discord.Embed(description=desc, color=COLORS["primary"])
    embed.set_footer(text="Use /matchmaking to find your own match! • Auto-removed after 24h")
    return embed


def _match_found_embed(size: str, game: str, opponent_display: str, your_time: str, opponent_time: str, opponent_guild_name: str = None) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Match Found!",
        description=f"You've been matched for a **{size} {game}** match!",
        color=COLORS["success"],
    )
    embed.add_field(name="Opponent", value=opponent_display, inline=True)
    if opponent_guild_name:
        embed.add_field(name="Their Server", value=opponent_guild_name, inline=True)
    embed.add_field(name="Your Time", value=your_time, inline=True)
    embed.add_field(name="Their Time", value=opponent_time or "Unknown", inline=True)
    embed.set_footer(text="Reach out to coordinate your match — good luck!")
    return embed


async def _ensure_matchmaking_channel(guild: discord.Guild):
    async with _get_lock("channel", guild.id):
        cfg = await db.get_guild_config(str(guild.id)) or {}
        cid = cfg.get("matchmaking_channel")
        if cid:
            ch = guild.get_channel(int(cid))
            if ch:
                return ch

        # Stored id is stale (channel renamed/deleted) — see if one already exists by name
        ch = discord.utils.get(guild.text_channels, name="core-match-making")
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    name=MATCHMAKING_CHANNEL_NAME,
                    topic="🔎 Find scrimmage opponents — pick a game below to get started!",
                    reason="TeamCore matchmaking setup",
                )
            except (discord.Forbidden, discord.HTTPException):
                return None

        await db.set_guild_config(str(guild.id), {"matchmaking_channel": str(ch.id)})
        return ch


async def _ensure_matchmaking_board(guild: discord.Guild):
    channel, msg = await _ensure_board_inner(guild)
    if channel:
        try:
            await _ensure_role_message(guild, channel)
        except Exception as e:
            print(f"⚠️ Matchmaking ping-role setup failed for {guild.name}: {e}")
    return channel, msg


async def _ensure_board_inner(guild: discord.Guild):
    """Returns (channel, message). message is None if the board couldn't be posted.

    Guarded by a per-guild lock so that if multiple people run /matchmaking at
    the same moment (e.g. right after someone announces it), they can't race
    past the "does a board already exist?" check and end up creating
    duplicate board embeds in the same channel.
    """
    async with _get_lock("board", guild.id):
        channel = await _ensure_matchmaking_channel(guild)
        if not channel:
            return None, None

        cfg = await db.get_guild_config(str(guild.id)) or {}
        board_msg_id = cfg.get("matchmaking_board_message_id")
        if board_msg_id:
            try:
                msg = await channel.fetch_message(int(board_msg_id))
                has_new_game = bool(msg.embeds) and GAMES[2] in (msg.embeds[0].description or "")
                if not has_new_game:  # board predates Basketball — add the option to the existing message
                    try:
                        await msg.edit(embed=_board_embed(), view=MatchmakingBoardView())
                    except discord.HTTPException:
                        pass
                return channel, msg
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass  # message is gone or unreachable; repost below

        embed = _board_embed()

        try:
            msg = await channel.send(embed=embed, view=MatchmakingBoardView())
        except (discord.Forbidden, discord.HTTPException):
            return channel, None

        await db.set_guild_config(str(guild.id), {"matchmaking_board_message_id": str(msg.id)})
        return channel, msg


# ─────────────────────────────────────────────
# Ping roles (one per game) + the reaction-role message
# ─────────────────────────────────────────────

_role_message_ids: dict[int, int] = {}  # role-message id -> guild id (lets reaction events skip the DB)


def _role_name(game: str) -> str:
    return f"Matchmaking Ping - {game}"


async def _ensure_ping_roles(guild: discord.Guild) -> dict:
    """Returns {game: Role}, creating any missing 'Matchmaking Ping - <game>' role.
    Games whose role can't be created (no Manage Roles) are simply left out."""
    async with _get_lock("roles", guild.id):
        cfg = await db.get_guild_config(str(guild.id)) or {}
        stored = dict(cfg.get("mm_ping_roles") or {})
        roles, changed = {}, False
        for game in GAMES:
            role = guild.get_role(int(stored[game])) if stored.get(game) else None
            if role is None:  # deleted/renamed in the DB sense — reuse one with the right name if it exists
                role = discord.utils.get(guild.roles, name=_role_name(game))
            if role is None:
                try:
                    role = await guild.create_role(
                        name=_role_name(game), mentionable=True, reason="TeamCore matchmaking ping role"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    continue
            if stored.get(game) != str(role.id):
                stored[game] = str(role.id)
                changed = True
            roles[game] = role
        if changed:
            await db.set_guild_config(str(guild.id), {"mm_ping_roles": stored})
        return roles


async def _ensure_role_message(guild: discord.Guild, channel: discord.TextChannel):
    """Posts (once) the 'react to get pinged' message with the three reactions pre-added by the bot."""
    async with _get_lock("rolemsg", guild.id):
        roles = await _ensure_ping_roles(guild)
        if not roles:
            return None

        cfg = await db.get_guild_config(str(guild.id)) or {}
        msg = None
        if cfg.get("mm_role_message_id"):
            try:
                msg = await channel.fetch_message(int(cfg["mm_role_message_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                msg = None

        if msg is None:
            embed = discord.Embed(
                title="🔔 Matchmaking Pings",
                description=(
                    "React below to get pinged when someone posts a match request for that game. "
                    "Remove your reaction to stop being pinged.\n\n"
                    + "\n".join(
                        f"{GAME_EMOJI[g]} **{g}** — {roles[g].mention}" for g in GAMES if g in roles
                    )
                ),
                color=COLORS["primary"],
            )
            embed.set_footer(text="TeamCore • Matchmaking")
            try:
                msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                return None
            await db.set_guild_config(str(guild.id), {"mm_role_message_id": str(msg.id)})

        # Make sure the bot's own reactions are all present (they're what people click)
        present = {str(r.emoji) for r in msg.reactions if r.me}
        for game in GAMES:
            if game in roles and GAME_EMOJI[game] not in present:
                try:
                    await msg.add_reaction(GAME_EMOJI[game])
                except discord.HTTPException:
                    pass

        _role_message_ids[msg.id] = guild.id
        return msg


async def _post_scrim_everywhere(bot: commands.Bot, user: discord.abc.User, game: str, size: str, display_time: str, origin_guild_name: str) -> list:
    """Broadcasts a scrim request into the Core Match Making channel of every
    server the bot is in, auto-creating that channel wherever it doesn't
    already exist. Returns the list of {guild_id, channel_id, message_id}
    postings that succeeded — servers where the bot lacks permission are
    skipped without blocking the rest."""

    async def _post_to_guild(guild: discord.Guild):
        channel = await _ensure_matchmaking_channel(guild)
        if not channel:
            return None
        roles = await _ensure_ping_roles(guild)
        role = roles.get(game)
        try:
            msg = await channel.send(
                content=role.mention if role else None,
                embed=_board_post_embed(user, game, size, display_time, origin_guild_name),
                allowed_mentions=discord.AllowedMentions(roles=[role]) if role else discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return None
        return {"guild_id": str(guild.id), "channel_id": str(channel.id), "message_id": str(msg.id)}

    results = await asyncio.gather(*[_post_to_guild(g) for g in bot.guilds], return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]


async def _delete_postings(bot: commands.Bot, postings: list) -> None:
    async def _delete_one(p):
        try:
            guild = bot.get_guild(int(p["guild_id"]))
            if not guild:
                return
            channel = guild.get_channel(int(p["channel_id"]))
            if not channel:
                return
            msg = await channel.fetch_message(int(p["message_id"]))
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, KeyError, TypeError):
            pass

    await asyncio.gather(*[_delete_one(p) for p in (postings or [])], return_exceptions=True)


async def _update_postings(bot: commands.Bot, postings: list, user, game: str, size: str, display_time: str, origin_guild_name: str) -> None:
    embed = _board_post_embed(user, game, size, display_time, origin_guild_name)

    async def _update_one(p):
        try:
            guild = bot.get_guild(int(p["guild_id"]))
            if not guild:
                return
            channel = guild.get_channel(int(p["channel_id"]))
            if not channel:
                return
            msg = await channel.fetch_message(int(p["message_id"]))
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, KeyError, TypeError):
            pass

    await asyncio.gather(*[_update_one(p) for p in (postings or [])], return_exceptions=True)


async def _handle_matchmaking_submission(interaction: discord.Interaction, game: str, size: str, raw_time: str) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send(
            embed=error_embed("Servers Only", "Matchmaking only works inside a server."), ephemeral=True
        )
        return

    if size not in _sizes_for(game):
        await interaction.followup.send(embed=error_embed("Invalid Match Type", f"{game} supports {', '.join(_sizes_for(game))}."), ephemeral=True)
        return

    bot = interaction.client
    guild_id = str(guild.id)
    guild_name = guild.name
    user_id = str(interaction.user.id)
    display_time = _display_time(raw_time)

    if not db.is_connected():
        await interaction.followup.send(
            embed=error_embed("Database Unavailable", "I can't process matchmaking right now. Please try again shortly."),
            ephemeral=True,
        )
        return

    # find-opponent / queue must not interleave for two people at once, or both can queue
    # themselves instead of matching each other. (The claim itself is also atomic in Mongo.)
    async with _get_lock("queue", 0):
        existing = await db.get_user_matchmaking_request(user_id, game, size)
        opponent = None
        request = None
        if not existing:
            opponent = await db.claim_matchmaking_opponent(game, size, exclude_user_id=user_id)
            if not opponent:
                request = await db.create_matchmaking_request(guild_id, guild_name, user_id, game, size, display_time)

    # Already queued for this exact game + size, anywhere? Just update the time.
    if existing:
        await db.update_matchmaking_request(existing["_id"], time_text=display_time)
        await _update_postings(
            bot, existing.get("postings"), interaction.user, game, size, display_time, existing.get("guild_name")
        )
        await interaction.followup.send(
            embed=success_embed("Request Updated", f"Updated your **{size} {game}** request — now listed as **{display_time}**."),
            ephemeral=True,
        )
        return

    if opponent:
        await _delete_postings(bot, opponent.get("postings"))

        opponent_user = None
        try:
            opponent_user = bot.get_user(int(opponent["user_id"])) or await bot.fetch_user(int(opponent["user_id"]))
        except (discord.NotFound, discord.HTTPException, ValueError):
            pass

        # Mentions don't resolve for people you share no server with, so always include the name too.
        def _who(u, uid):
            return f"{u.mention} (**{discord.utils.escape_markdown(u.name)}**)" if u else f"<@{uid}>"

        opponent_display = _who(opponent_user, opponent["user_id"])
        you_display = _who(interaction.user, interaction.user.id)
        opponent_time = opponent.get("time_text", "Unknown")
        opponent_guild_name = opponent.get("guild_name")

        dm_ok = True
        try:
            await interaction.user.send(
                embed=_match_found_embed(size, game, opponent_display, display_time, opponent_time, opponent_guild_name)
            )
        except (discord.Forbidden, discord.HTTPException):
            dm_ok = False
        if opponent_user:
            try:
                await opponent_user.send(
                    embed=_match_found_embed(size, game, you_display, opponent_time, display_time, guild_name)
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        tail = "Check your DMs for details." if dm_ok else "I couldn't DM you, so reach out to them directly."
        await interaction.followup.send(
            embed=success_embed("Match Found! 🎉", f"You've been matched with {opponent_display} for a **{size} {game}** match! {tail}"),
            ephemeral=True,
        )
        return

    # No opponent waiting yet — broadcast the request to every server's Core Match Making channel
    postings = await _post_scrim_everywhere(bot, interaction.user, game, size, display_time, guild_name)
    if request:
        await db.update_matchmaking_request(request["_id"], postings=postings)

    if postings:
        note = (
            f"Posted in the Core Match Making channel across **{len(postings)}** server(s). "
            "It'll auto-remove in 24 hours if no one matches."
        )
    else:
        note = "I couldn't post to any Core Match Making channel — check my channel permissions. You're still in the queue."

    await interaction.followup.send(
        embed=success_embed(
            "Added to Queue",
            f"You're queued for a **{size} {game}** match — **{display_time}**.\n{note}",
        ),
        ephemeral=True,
    )


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

class MatchTimeModal(discord.ui.Modal):
    def __init__(self, game: str, size: str):
        super().__init__(title=f"{size} • {game}"[:45])
        self.game = game
        self.size = size
        self.time_input = discord.ui.TextInput(
            label="When are you available?",
            placeholder="e.g. 8:30 PM EST — or type 'now' for available now",
            max_length=100,
            required=True,
        )
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await _handle_matchmaking_submission(interaction, self.game, self.size, self.time_input.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        msg = "❌ Something went wrong submitting your request. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


class SizeSelectView(discord.ui.View):
    """Ephemeral — only the person who clicked a game button ever sees this."""

    def __init__(self, game: str):
        super().__init__(timeout=180)
        self.game = game
        self.select = discord.ui.Select(
            placeholder="Select a match type...",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=s, value=s) for s in _sizes_for(game)],
            row=0,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        size = self.select.values[0]
        await interaction.response.send_modal(MatchTimeModal(self.game, size))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Cancelled.", embed=None, view=None)


class MatchmakingBoardView(discord.ui.View):
    """Persistent — registered once via bot.add_view() so it survives restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=GAMES[0], style=discord.ButtonStyle.primary, emoji="⚽", custom_id="mm_game_ff3")
    async def football_fusion_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=_size_select_embed(GAMES[0]), view=SizeSelectView(GAMES[0]), ephemeral=True
        )

    @discord.ui.button(label=GAMES[1], style=discord.ButtonStyle.primary, emoji="🏈", custom_id="mm_game_uf")
    async def ultimate_football(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=_size_select_embed(GAMES[1]), view=SizeSelectView(GAMES[1]), ephemeral=True
        )

    @discord.ui.button(label=GAMES[2], style=discord.ButtonStyle.primary, emoji="🏀", custom_id="mm_game_bb")
    async def basketball(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=_size_select_embed(GAMES[2]), view=SizeSelectView(GAMES[2]), ephemeral=True
        )


# ─────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────

class Matchmaking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(MatchmakingBoardView())
        self.cleanup_expired_requests.start()
        self.provision_all_guilds.start()

    def cog_unload(self):
        self.cleanup_expired_requests.cancel()
        self.provision_all_guilds.cancel()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Force-create the Core Match Making channel (+ board) the moment the
        bot joins a server, instead of waiting for someone to run /matchmaking
        or for the next scrim broadcast to reach it."""
        try:
            await _ensure_matchmaking_board(guild)
        except Exception as e:
            print(f"⚠️ Matchmaking setup failed for {guild.name}: {e}")

    async def _apply_reaction(self, payload: discord.RawReactionActionEvent, add: bool):
        if payload.message_id not in _role_message_ids or payload.guild_id is None:
            return
        if payload.user_id == self.bot.user.id:
            return
        game = next((g for g in GAMES if GAME_EMOJI[g] == str(payload.emoji)), None)
        guild = self.bot.get_guild(payload.guild_id)
        if game is None or guild is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        role = (await _ensure_ping_roles(guild)).get(game)
        if role is None:
            return
        try:
            if add:
                await member.add_roles(role, reason="Matchmaking ping reaction")
            else:
                await member.remove_roles(role, reason="Matchmaking ping reaction removed")
        except (discord.Forbidden, discord.HTTPException):
            pass  # role above the bot's, or member left

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._apply_reaction(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._apply_reaction(payload, False)

    @tasks.loop(count=1)
    async def provision_all_guilds(self):
        """One-shot sweep on startup: make sure every server the bot is
        already in has its Core Match Making channel + board set up, not
        just the ones that happen to trigger it via a command or broadcast."""
        for guild in self.bot.guilds:
            try:
                await _ensure_matchmaking_board(guild)
            except Exception as e:
                print(f"⚠️ Matchmaking channel provisioning failed for {guild.name}: {e}")

    @provision_all_guilds.before_loop
    async def before_provision(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="matchmaking", description="Open the Core Match Making board to find an opponent")
    @app_commands.guild_only()
    async def matchmaking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel, msg = await _ensure_matchmaking_board(interaction.guild)
        if not channel:
            return await interaction.followup.send(
                embed=error_embed("Permission Error", "I need the **Manage Channels** permission to set up the matchmaking board."),
                ephemeral=True,
            )
        if not msg:
            return await interaction.followup.send(
                embed=error_embed("Permission Error", f"I found {channel.mention} but can't post there — check my **Send Messages** permission."),
                ephemeral=True,
            )
        await interaction.followup.send(
            embed=success_embed(
                "Matchmaking Board Ready",
                f"Head to {channel.mention} and pick a game to get started! "
                "You'll be matched with anyone across every server using this bot.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="matchmaking-queue", description="View who's currently looking for a match")
    @app_commands.guild_only()
    async def matchmaking_queue(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        queue = await db.get_matchmaking_queue()
        if not queue:
            return await interaction.followup.send(
                embed=info_embed("🔎 Matchmaking Queue", "No one is currently queued. Use `/matchmaking` to start looking!"),
                ephemeral=True,
            )

        lines = [
            f"• <@{r['user_id']}> ({r.get('guild_name', 'Unknown server')}) — **{r['size']} {r['game']}** — 🕐 {r['time_text']}"
            for r in queue[:25]
        ]
        embed = discord.Embed(title="🔎 Current Matchmaking Queue", description="\n".join(lines), color=COLORS["primary"])
        embed.set_footer(text=f"{len(queue)} open request(s) across all servers • auto-removed after 24h")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="matchmaking-cancel", description="Cancel your pending matchmaking request(s)")
    @app_commands.guild_only()
    async def matchmaking_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        requests = await db.get_user_matchmaking_requests(str(interaction.user.id))
        if not requests:
            return await interaction.followup.send(
                embed=error_embed("No Requests", "You don't have any pending matchmaking requests."), ephemeral=True
            )

        for r in requests:
            await db.remove_matchmaking_request(r["_id"])
            await _delete_postings(self.bot, r.get("postings"))

        await interaction.followup.send(
            embed=success_embed("Cancelled", f"Cancelled **{len(requests)}** pending request(s)."), ephemeral=True
        )

    @tasks.loop(minutes=5)
    async def cleanup_expired_requests(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=QUEUE_EXPIRY_HOURS)
        expired = await db.get_expired_matchmaking_requests(cutoff)
        for r in expired:
            try:
                await db.remove_matchmaking_request(r["_id"])
                await _delete_postings(self.bot, r.get("postings"))
            except Exception as e:
                print(f"⚠️ Could not expire matchmaking request {r.get('_id')}: {e}")

    @cleanup_expired_requests.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Matchmaking(bot))
