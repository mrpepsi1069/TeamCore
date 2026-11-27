"""cogs/logging.py — Private logging for guild joins, leaves, and errors"""

import traceback
import discord
from discord.ext import commands
from config import LOG_ERRORS_CHANNEL_ID, LOG_JOINS_CHANNEL_ID, LOG_LEAVES_CHANNEL_ID


async def _get_log_channel(bot: commands.Bot, channel_id: int):
    """The log channels are fixed by ID (config.py), so renaming them doesn't break logging,
    and the bot never creates a stray channel. Returns None when the channel is unreachable."""
    channel = bot.get_channel(channel_id) or bot.get_channel(str(channel_id))
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except Exception:
        return None


_invite_cache: dict[int, str] = {}


async def _get_invite(guild: discord.Guild) -> str:
    """Try to fetch or create an invite link for a guild.

    Cached per guild: without a cache, a server where the bot can't list invites got a
    brand-new permanent invite created on *every* logged error.
    """
    if guild.id in _invite_cache:
        return _invite_cache[guild.id]
    url = await _fetch_or_create_invite(guild)
    if url != "No invite available":
        _invite_cache[guild.id] = url
    return url


async def _fetch_or_create_invite(guild: discord.Guild) -> str:
    try:
        invites = await guild.invites()
        if invites:
            return str(invites[0].url)
    except Exception:
        pass
    try:
        # Try to create one from the first channel we can
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                invite = await channel.create_invite(max_age=0, max_uses=0, unique=False)
                return str(invite.url)
    except Exception:
        pass
    return "No invite available"


class Logging(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────
    # Guild Join
    # ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        channel = await _get_log_channel(self.bot, LOG_JOINS_CHANNEL_ID)
        if channel is None:
            return

        invite = await _get_invite(guild)

        embed = discord.Embed(
            title="✅ TeamCore Joined a Server",
            color=0x57F287
        )
        embed.add_field(name="Server", value=guild.name, inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Server ID", value=str(guild.id), inline=True)
        embed.add_field(name="Invite", value=invite, inline=False)
        embed.set_footer(text=f"Total servers: {len(self.bot.guilds)}")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        await channel.send(embed=embed)

    # ──────────────────────────────────────────────
    # Guild Leave
    # ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        channel = await _get_log_channel(self.bot, LOG_LEAVES_CHANNEL_ID)
        if channel is None:
            return

        embed = discord.Embed(
            title="❌ TeamCore Left a Server",
            color=0xED4245
        )
        embed.add_field(name="Server", value=guild.name, inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Server ID", value=str(guild.id), inline=True)
        embed.set_footer(text=f"Total servers: {len(self.bot.guilds)}")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        await channel.send(embed=embed)

    # ──────────────────────────────────────────────
    # Error Logging
    # ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception):
        """Private admin logging only. bot.py's @bot.tree.error handler owns the
        user-facing reply and re-dispatches here via bot.dispatch(), since
        discord.py's app_commands errors don't reach cog listeners on their own."""
        channel = await _get_log_channel(self.bot, LOG_ERRORS_CHANNEL_ID)
        error = getattr(error, "original", error)  # unwrap CommandInvokeError so the real traceback is logged

        guild_name = interaction.guild.name if interaction.guild else "DM"
        guild_id   = str(interaction.guild_id) if interaction.guild_id else "N/A"
        command    = interaction.command.name if interaction.command else "unknown"
        tb         = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        if channel:
            invite = await _get_invite(interaction.guild) if interaction.guild else "N/A"

            embed = discord.Embed(
                title="⚠️ Bot Error",
                color=0xFEE75C
            )
            embed.add_field(name="Command", value=f"/{command}", inline=True)
            embed.add_field(name="Server", value=guild_name, inline=True)
            embed.add_field(name="Server ID", value=guild_id, inline=True)
            embed.add_field(name="Invite", value=invite, inline=False)
            embed.add_field(
                name="Error",
                value=f"```{str(error)[:1000]}```",
                inline=False
            )
            if tb and tb.strip() != "NoneType: None":
                embed.add_field(
                    name="Traceback",
                    value=f"```{tb[-800:]}```",
                    inline=False
                )
            embed.set_footer(text=f"User: {interaction.user}")
            await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logging(bot))