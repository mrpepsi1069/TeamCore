"""cogs/suggest.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed
import database as db

SUGGESTION_CHANNEL_ID = 1466284506385219686


class Suggest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="suggest", description="Submit a suggestion")
    @app_commands.describe(suggestion="Your suggestion (max 1000 chars)")
    async def suggest(self, interaction: discord.Interaction, suggestion: app_commands.Range[str, 1, 1000]):
        await db.create_suggestion(str(interaction.guild_id), str(interaction.user.id), suggestion)

        try:
            channel = self.bot.get_channel(SUGGESTION_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title="New Suggestion",
                    description=suggestion,
                    color=0x5865F2,
                )
                embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                msg = await channel.send(embed=embed)
                await msg.add_reaction("👍")
                await msg.add_reaction("👎")
        except Exception as e:
            print(f"Error sending suggestion: {e}")

        await interaction.response.send_message(
            embed=success_embed("Suggestion Submitted", "Thank you! The server admins will review it."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Suggest(bot))
