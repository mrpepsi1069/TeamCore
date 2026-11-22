"""cogs/bold.py — Convert text to bold Unicode characters."""

import discord
from discord import app_commands
from discord.ext import commands


_BOLD_MAP: dict[int, int] = {
    **{ord(c): 0x1D400 + (ord(c) - ord("A")) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{ord(c): 0x1D41A + (ord(c) - ord("a")) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{ord(c): 0x1D7CE + (ord(c) - ord("0")) for c in "0123456789"},
}


def _boldify(text: str) -> str:
    return "".join(chr(_BOLD_MAP.get(ord(ch), ord(ch))) for ch in text)


class Bold(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="bold", description="Convert text to bold Unicode characters")
    @app_commands.describe(text="Text to boldify")
    async def bold(self, interaction: discord.Interaction, text: str):
        await interaction.response.send_message(_boldify(text))


async def setup(bot: commands.Bot):
    await bot.add_cog(Bold(bot))
