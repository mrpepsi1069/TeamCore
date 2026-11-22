"""cogs/say.py — Send a message as the bot, and post announcements to the configured channel."""

import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import has_manager_perms
from utils.embeds import error_embed, success_embed
import database as db


def _mentions_for(member: discord.abc.User, channel: discord.abc.GuildChannel) -> discord.AllowedMentions:
    """Only allow @everyone/role pings if the *person* running the command could do it themselves."""
    perms = channel.permissions_for(member)
    return discord.AllowedMentions(everyone=perms.mention_everyone, roles=perms.mention_everyone, users=True)


class Say(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="say", description="Send a message as the bot")
    @app_commands.describe(
        message="The message to send",
        channel="Channel to send in (defaults to current channel)"
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: app_commands.Range[str, 1, 2000],
        channel: discord.TextChannel = None,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."),
                ephemeral=True
            )

        target = channel or interaction.channel

        # Without this, a Manager could post (and @everyone) in channels they can't even write in.
        if not target.permissions_for(interaction.user).send_messages:
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", f"You can't send messages in {target.mention}."),
                ephemeral=True,
            )

        try:
            await target.send(message, allowed_mentions=_mentions_for(interaction.user, target))
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("Missing Permissions", f"I can't send messages in {target.mention}."),
                ephemeral=True
            )
        await interaction.response.send_message(f"✅ Message sent in {target.mention}", ephemeral=True)

    @app_commands.command(name="announce", description="Post an announcement in the server's announcement channel (Manager only)")
    @app_commands.describe(
        title="Announcement title",
        message="Announcement text",
        role="Role to ping (optional)",
    )
    async def announce(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 200],
        message: app_commands.Range[str, 1, 3500],
        role: discord.Role = None,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )

        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        cid = cfg.get("announce_channel")
        channel = interaction.guild.get_channel(int(cid)) if cid else None
        if channel is None:
            return await interaction.response.send_message(
                embed=error_embed("Setup Required", "No announcement channel is set. The server owner can pick one in `/setup`."),
                ephemeral=True,
            )

        embed = discord.Embed(title=f"📢 {title}", description=message, color=0x5865F2)
        embed.set_footer(text=f"Announced by {interaction.user.display_name}")
        try:
            await channel.send(
                content=role.mention if role else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=bool(role)) if role else discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("Missing Permissions", f"I can't send messages in {channel.mention}."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=success_embed("Announcement Posted", f"Posted in {channel.mention}."), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Say(bot))
