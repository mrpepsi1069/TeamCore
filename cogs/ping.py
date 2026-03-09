"""cogs/ping.py"""

import time
import discord
from discord import app_commands
from discord.ext import commands


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check bot latency and response time")
    async def ping(self, interaction: discord.Interaction):
        start = time.monotonic()
        await interaction.response.defer(ephemeral=True)
        elapsed = round((time.monotonic() - start) * 1000)
        api_latency = round(self.bot.latency * 1000)

        embed = discord.Embed(title="🏓 Pong!", color=0x5865F2)
        embed.add_field(name="Bot Latency", value=f"{elapsed}ms", inline=True)
        embed.add_field(name="API Latency", value=f"{api_latency}ms", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Ping(bot))
