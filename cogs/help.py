"""cogs/help.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import help_embed


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Display the command help menu")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=help_embed())


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
