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

    @app_commands.command(name="leaderboard", description="Show who has the most championship rings or awards")
    @app_commands.describe(category="What to rank players by")
    @app_commands.choices(category=[
        app_commands.Choice(name="Championship Rings", value="rings"),
        app_commands.Choice(name="Individual Awards", value="awards"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, category: str = "rings"):
        rows = await db.get_leaderboard(str(interaction.guild_id), category, limit=10)
        if not rows:
            return await interaction.response.send_message(
                embed=error_embed("Nothing Yet", "No one has any of those yet."), ephemeral=True
            )
        medals = ["🥇", "🥈", "🥉"]
        icon = "💍" if category == "rings" else "🏆"
        lines = [
            f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{r['_id']}> — {icon} **{r['count']}**"
            for i, r in enumerate(rows)
        ]
        title = "Championship Rings" if category == "rings" else "Individual Awards"
        embed = discord.Embed(title=f"🏅 {title} Leaderboard", description="\n".join(lines), color=0xFFD700)
        embed.set_footer(text=interaction.guild.name)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(AwardCheck(bot))
