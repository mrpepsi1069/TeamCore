"""cogs/say.py — Send a message as the bot"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import has_manager_perms
from utils.embeds import error_embed


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

        try:
            await target.send(message)
            await interaction.response.send_message(
                f"✅ Message sent in {target.mention}", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", f"I can't send messages in {target.mention}."),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Say(bot))