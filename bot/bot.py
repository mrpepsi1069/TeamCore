"""bot.py — TeamCore Discord Bot (discord.py rewrite). Single entry point: `python bot.py`."""

import os
import sys
import time
import asyncio
import traceback
from pathlib import Path

# Repo layout: /bot, /backend, /database, /shared. Make the repo root importable so the bot can
# `import database` and `import shared` no matter where it is launched from.
ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "bot")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv()

from aiohttp import web  # noqa: E402
import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import commands  # noqa: E402

import database as db  # noqa: E402
from utils.embeds import error_embed  # noqa: E402

START_TIME = time.time()

# ──────────────────────────────────────────────
# Command gating (runs before every slash command)
# ──────────────────────────────────────────────

# Commands that make sense outside a server. Everything else assumes interaction.guild
# exists and used to crash with AttributeError when run from a DM.
DM_SAFE_COMMANDS = {
    "ping", "help", "invite", "premium", "flipcoin", "randomnumber", "bold",
    "fban", "fkick", "suggest",
    # owner-only tools that don't touch interaction.guild
    "botstats", "guilds", "join", "botkick", "globalannouncement", "setpremium",
}

# Commands that read/write the database. If MongoDB is down the old code "succeeded"
# silently (e.g. /league add created a Discord role but stored nothing).
DB_COMMANDS = {
    "award", "award-remove", "awardcheck", "leaderboard", "ring-add", "ring-remove",
    "lineup", "depthchart", "league", "contract", "gametime", "times", "activitycheck",
    "matchmaking", "matchmaking-queue", "matchmaking-cancel", "setup", "disableglobalmessages",
    "record", "stats", "lock", "unlock", "poll", "giveaway", "countdown", "activity", "rolepanel",
    "announcement", "backup", "memberstats", "suggestion", "roster", "match",
}


class TeamCoreTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cmd = interaction.command
        if cmd is None:
            return True
        root = cmd.qualified_name.split()[0]
        if interaction.guild is None and root not in DM_SAFE_COMMANDS:
            raise app_commands.CheckFailure("This command can only be used in a server.")
        if root in DB_COMMANDS and not db.is_connected():
            raise app_commands.CheckFailure(
                "The database is currently unavailable, so I can't run this command. Please try again shortly."
            )
        return True


# ──────────────────────────────────────────────
# Bot
# ──────────────────────────────────────────────

COGS = [
    "cogs.ping", "cogs.help", "cogs.invite", "cogs.bold", "cogs.randomnumber", "cogs.flipcoin",
    "cogs.fban", "cogs.fkick", "cogs.suggest", "cogs.ban", "cogs.kick",
    "cogs.timeout", "cogs.role", "cogs.unrole", "cogs.mutevc", "cogs.unmutevc",
    "cogs.setup", "cogs.activitycheck", "cogs.botstats", "cogs.guilds",
    "cogs.dmmembers", "cogs.premium", "cogs.awardcheck", "cogs.award",
    "cogs.ringadd", "cogs.lineup", "cogs.depthchart", "cogs.league",
    "cogs.gametime", "cogs.times", "cogs.contract",
    "cogs.globalannouncement", "cogs.templateuse", "cogs.join",
    "cogs.logging", "cogs.botkick", "cogs.say", "cogs.matchmaking",
    # v2 feature set
    "cogs.serverinfo", "cogs.record", "cogs.leaderboard", "cogs.stats", "cogs.lock", "cogs.unlock",
    "cogs.poll", "cogs.giveaway", "cogs.countdown", "cogs.activity", "cogs.embed", "cogs.rolepanel",
    "cogs.announcement", "cogs.backup", "cogs.memberstats", "cogs.suggestion",
    # website dashboard bridge + automation (additive: the bot runs fine without the website)
    "cogs.dashboard_bridge", "cogs.automation", "cogs.roster", "cogs.match",
]


class TeamCoreBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # privileged: needed for role.members (DMs, activity checks)
        # message_content is NOT requested: nothing here reads message text, and asking for
        # a privileged intent we don't use makes the bot fail to start if it isn't enabled.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            tree_cls=TeamCoreTree,
        )
        self._ready_once = False
        self.start_time = START_TIME

    async def setup_hook(self) -> None:
        # Order matters: the DB must be up *before* cogs start background tasks,
        # otherwise e.g. matchmaking's startup sweep posts a duplicate board in every
        # server because it can't see the stored board message id yet.
        await db.initialize()

        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"✅ Loaded: {cog}")
            except Exception as e:
                print(f"❌ Failed to load {cog}: {e}")
                traceback.print_exc()

        await start_http(self)

        # Sync once per process (it used to run on every gateway reconnect, which
        # burns through Discord's command-sync rate limit).
        try:
            synced = await self.tree.sync()
            print(f"✅ Synced {len(synced)} slash command(s)")
        except Exception as e:
            print(f"❌ Sync error: {e}")

    async def on_ready(self):
        print(f"🤖 {self.user} is online")
        print(f"📊 Servers: {len(self.guilds)}")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="your team | /help")
        )
        if not self._ready_once:
            self._ready_once = True
            # Servers the bot was already in before the DB existed have no guild doc,
            # which made /premium grants silently do nothing for them.
            for g in self.guilds:
                try:
                    await db.create_guild(str(g.id), g.name)
                except Exception as e:
                    print(f"⚠️ Could not register guild {g.id}: {e}")


bot = TeamCoreBot()


# ──────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────

@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"✅ Joined: {guild.name}")
    await db.create_guild(str(guild.id), guild.name)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Log slash command usage and handle the league signup button."""
    if interaction.type == discord.InteractionType.application_command:
        cmd = interaction.command
        name = cmd.qualified_name if cmd else interaction.data.get("name", "unknown")
        await db.log_command(name, str(interaction.guild_id), str(interaction.user.id))

    elif interaction.type == discord.InteractionType.component:
        cid = (interaction.data or {}).get("custom_id", "")
        if cid.startswith("league_signup_"):
            await _handle_league_signup(interaction, cid[len("league_signup_"):])


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)

    # NB: MissingPermissions and CommandOnCooldown are both subclasses of CheckFailure,
    # so they have to be tested before the generic CheckFailure branch.
    if isinstance(error, app_commands.MissingPermissions):
        msg = error_embed("Missing Permissions", "You don't have permission to use this command.")
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = error_embed("Slow Down", f"Try again in {error.retry_after:.0f}s.")
    elif isinstance(error, app_commands.CheckFailure):
        msg = error_embed("Can't Do That", str(error) or "You can't use this command here.")
    else:
        print(f"❌ Command error in /{interaction.command.qualified_name if interaction.command else '?'}: {original!r}")
        traceback.print_exception(type(original), original, original.__traceback__)
        msg = error_embed("Error", "Something went wrong running that command. The developers have been notified.")
        # Only genuine failures are forwarded to the private error-log channel (cogs/logging.py);
        # permission/availability rejections above are normal and would just be noise.
        bot.dispatch("app_command_error", interaction, error)

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
    except Exception:
        pass


# ──────────────────────────────────────────────
# League Signup Button Handler
# ──────────────────────────────────────────────

async def _handle_league_signup(interaction: discord.Interaction, role_id: str):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        return await interaction.followup.send("❌ This button only works inside a server.", ephemeral=True)

    try:
        role = guild.get_role(int(role_id))
    except ValueError:
        role = None
    if not role:
        return await interaction.followup.send("❌ League role not found. It may have been deleted.", ephemeral=True)

    try:
        if member.get_role(role.id):
            await member.remove_roles(role, reason="League signup button")
            return await interaction.followup.send(f"✅ You have been removed from **{role.name}**!", ephemeral=True)
        await member.add_roles(role, reason="League signup button")
    except discord.Forbidden:
        return await interaction.followup.send(
            "❌ I can't manage that role. Ask an admin to move my role above the league role.", ephemeral=True
        )
    except discord.HTTPException:
        return await interaction.followup.send("❌ Discord rejected that change. Please try again.", ephemeral=True)

    league = await db.get_league_by_role_id(str(guild.id), str(role.id))
    league_name = league["league_name"] if league else role.name
    try:
        welcome = discord.Embed(
            title="🎉 Welcome to the League!",
            description=f"You've joined **{league_name}**!",
            color=0x00FF00,
        )
        welcome.add_field(name="🏆 League", value=league_name, inline=True)
        welcome.add_field(name="🏠 Server", value=guild.name, inline=True)
        if league and league.get("signup_link"):
            welcome.add_field(name="🔗 League Link", value=f"[Click here]({league['signup_link']})", inline=False)
        welcome.set_footer(text="Good luck and have fun!")
        await member.send(embed=welcome)
    except Exception:
        pass
    await interaction.followup.send(
        f"✅ You've been added to **{role.name}**!\n\nCheck your DMs for more information.", ephemeral=True
    )


# ──────────────────────────────────────────────
# Health Check HTTP Server (for uptime monitoring + landing page stats)
# ──────────────────────────────────────────────

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# The landing page lists every server using the bot (name, icon, size). Set
# PUBLIC_GUILD_LIST=0 to stop /api/guilds from exposing that to the whole internet.
PUBLIC_GUILD_LIST = os.getenv("PUBLIC_GUILD_LIST", "1").lower() not in ("0", "false", "no")


def _make_handler(bot: commands.Bot):
    async def _http_handler(request: web.Request) -> web.Response:
        if request.method == "OPTIONS":
            return web.Response(headers=CORS_HEADERS)
        path = request.path.rstrip("/") or "/"
        if path in ("/", "/v1", "/api/stats"):
            data = {
                "status": "online" if bot.is_ready() else "starting",
                "bot": str(bot.user) if bot.user else "starting",
                "guilds": len(bot.guilds),
                "users": sum(g.member_count or 0 for g in bot.guilds),  # member_count can be None
                "uptime": int(time.time() - START_TIME),                # seconds (was a unix timestamp)
            }
            return web.json_response(data, headers=CORS_HEADERS)
        if path == "/health":
            return web.json_response(
                {"status": "healthy" if bot.is_ready() else "starting", "database": db.is_connected()},
                headers=CORS_HEADERS,
            )
        if path == "/api/guilds":
            if not PUBLIC_GUILD_LIST:
                return web.json_response([], headers=CORS_HEADERS)
            guilds = [
                {
                    "id": str(g.id),
                    "name": g.name,
                    "icon": str(g.icon.url) if g.icon else None,
                    "members": g.member_count or 0,
                }
                for g in sorted(bot.guilds, key=lambda x: x.member_count or 0, reverse=True)
            ]
            return web.json_response(guilds, headers=CORS_HEADERS)
        return web.Response(status=404, text="Not found", headers=CORS_HEADERS)

    return _http_handler


async def start_http(bot: commands.Bot):
    base_port = int(os.getenv("PORT", 3000))
    app = web.Application()
    app.router.add_get("/{path_info:.*}", _make_handler(bot))
    app.router.add_route("OPTIONS", "/{path_info:.*}", _make_handler(bot))
    runner = web.AppRunner(app)
    await runner.setup()

    # Try the configured port, then fall back to alternatives
    for port in [base_port, 3001, 8080, 8081, 0]:
        try:
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            actual_port = port if port != 0 else runner.addresses[0][1]
            print(f"🌐 HTTP server on port {actual_port}")
            return
        except OSError:
            if port == 0:
                print("⚠️  HTTP server could not start (all ports busy) — bot will still run fine")
            continue


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN is not set!")
        sys.exit(1)

    print("🔐 Logging into Discord...")
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
