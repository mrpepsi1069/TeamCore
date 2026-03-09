"""cogs/randomnumber.py"""

import random
import discord
from discord import app_commands
from discord.ext import commands


class RandomNumber(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="randomnumber", description="Generate a random number between min and max")
    @app_commands.describe(min_val="Minimum number", max_val="Maximum number")
    async def randomnumber(self, interaction: discord.Interaction, min_val: int, max_val: int):
        if max_val < min_val:
            return await interaction.response.send_message(
                "❌ Maximum cannot be less than minimum!", ephemeral=True
            )
        result = random.randint(min_val, max_val)
        embed = discord.Embed(
            title="🎲 Random Number Generator",
            description=f"Your random number between **{min_val}** and **{max_val}** is:\n**{result}**",
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(RandomNumber(bot))
