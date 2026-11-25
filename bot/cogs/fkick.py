"""cogs/fkick.py — Fake kick (just for fun)."""

import discord
from discord import app_commands
from discord.ext import commands


class FKick(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="fkick", description="Fake kick a user (for fun)")
    @app_commands.describe(user="User to fake kick", reason="Fake reason")
    async def fkick(self, interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
        embed = discord.Embed(
            title="👢 User Kicked",
            description=f"**{user}** has been kicked from the server!",
            color=0xED4245,
        )
        embed.add_field(name="Reason", value=reason)
        embed.set_footer(text="Just kidding! This is a fake kick.")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FKick(bot))
