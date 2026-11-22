"""cogs/awardcheck.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import awards_embed, error_embed
import database as db


class AwardCheck(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="awardcheck", description="View a player's awards and championship rings")
    @app_commands.describe(member="Member to check (leave empty for yourself)")
    async def awardcheck(self, interaction: discord.Interaction, member: discord.User = None):
        target = member or interaction.user
        await db.create_or_update_user(str(target.id), target.name)
        data = await db.get_user_awards(str(interaction.guild_id), str(target.id))
        if not data:
            return await interaction.response.send_message(
                embed=error_embed("User Not Found", "Could not find this user's data."), ephemeral=True
            )
        await interaction.response.send_message(embed=awards_embed(data))


async def setup(bot: commands.Bot):
    await bot.add_cog(AwardCheck(bot))
