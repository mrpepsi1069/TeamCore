"""cogs/announcement.py — Formatted announcements with preview and optional ping."""

from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import is_manager_or_mod
from utils.ui import ConfirmView, parse_color
from utils.validation import is_valid_url, sanitize_input


class Announcement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="announcement", description="Send announcements", guild_only=True)

    @group.command(name="send", description="Send an announcement (you'll get a preview first)")
    @app_commands.describe(title="Headline", description="Body text (use \\n for a new line)", image="Large image URL", thumbnail="Small image URL",
                           color="Hex colour, e.g. #FF8800", ping="Role to ping", footer="Footer text", channel="Override the announcement channel")
    async def send(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 256], description: app_commands.Range[str, 1, 3500],
                   image: str = None, thumbnail: str = None, color: str = None, ping: discord.Role = None,
                   footer: app_commands.Range[str, 1, 200] = None, channel: discord.TextChannel = None):
        if not await is_manager_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        for label, url in (("image", image), ("thumbnail", thumbnail)):
            if url and not is_valid_url(url):
                return await interaction.response.send_message(embed=error_embed("Invalid URL", f"The {label} must start with http(s)://"), ephemeral=True)
        col = parse_color(color)
        if col is None:
            return await interaction.response.send_message(embed=error_embed("Invalid Colour", "Use a hex colour like `#FF8800`."), ephemeral=True)

        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        target = channel or (interaction.guild.get_channel(int(cfg["announce_channel"])) if cfg.get("announce_channel") else None)
        if target is None:
            return await interaction.response.send_message(embed=error_embed("No Channel", "Set one with `/announcement channel` (or pick a `channel` here)."), ephemeral=True)
        if not target.permissions_for(interaction.user).send_messages or not target.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message(embed=error_embed("Can't Post", f"Either you or I can't send messages in {target.mention}."), ephemeral=True)

        embed = discord.Embed(title=sanitize_input(title, 256), description=description.replace("\\n", "\n"), color=col, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=footer or interaction.guild.name)
        if image:
            embed.set_image(url=image)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        view = ConfirmView(interaction.user.id, "Send announcement")
        await interaction.response.send_message(
            content=f"**Preview** — will be posted in {target.mention}" + (f" and ping {ping.mention}" if ping else "") + ":",
            embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
        await view.wait()
        if not view.value:
            return await interaction.edit_original_response(content="Cancelled — nothing was sent.", embed=None, view=None)
        try:
            msg = await target.send(content=ping.mention if ping else None, embed=embed,
                                    allowed_mentions=discord.AllowedMentions(roles=[ping]) if ping else discord.AllowedMentions.none())
        except discord.HTTPException:
            return await interaction.edit_original_response(content="❌ Couldn't send — check my permissions in that channel.", embed=None, view=None)
        gid = str(interaction.guild_id)
        await db.col("announcements").insert_one({
            "guild_id": gid, "announcement_id": await db.next_seq(gid, "announcement"), "title": embed.title, "channel_id": str(target.id),
            "message_id": str(msg.id), "by": str(interaction.user.id), "at": datetime.now(timezone.utc),
        })
        await interaction.edit_original_response(content=f"✅ Announcement sent: {msg.jump_url}", embed=None, view=None)

    @group.command(name="channel", description="Set the default announcement channel")
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await is_manager_or_mod(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        await db.set_guild_config(str(interaction.guild_id), {"announce_channel": channel.id})
        await interaction.response.send_message(embed=success_embed("Announcement Channel Set", f"Announcements go to {channel.mention}."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Announcement(bot))
