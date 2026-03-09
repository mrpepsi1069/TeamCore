"""cogs/guilds.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import has_owner_perms


class Guilds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="guilds", description="View all servers the bot is in")
    async def guilds(self, interaction: discord.Interaction):
        if not await has_owner_perms(interaction):
            return await interaction.response.send_message(
                content="❌ You do not have permission to use this.", ephemeral=True
            )

        guild_lines = [
            f"**{g.name}**\nID: {g.id}\nMembers: {g.member_count}"
            for g in self.bot.guilds
        ]
        description = "\n\n".join(guild_lines)[:4000]

        embed = discord.Embed(title="📊 Bot Guilds", description=description, color=0x5865F2)
        embed.set_footer(text=f"Total Servers: {len(self.bot.guilds)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Guilds(bot))
