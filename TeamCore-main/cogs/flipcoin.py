# cogs/flipcoin.py
import random
import discord
from discord import app_commands
from discord.ext import commands


class FlipCoin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="flipcoin", description="Flip a coin (heads or tails)")
    async def flipcoin(self, interaction: discord.Interaction):
        result = "Heads" if random.random() < 0.5 else "Tails"
        emoji = "🪙" if result == "Heads" else "💿"

        embed = discord.Embed(
            title=f"{emoji} {result}!",
            description=f"The coin landed on **{result}**",
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FlipCoin(bot))
