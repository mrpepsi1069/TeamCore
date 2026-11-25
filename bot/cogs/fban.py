"""cogs/fban.py — Fake ban (just for fun)."""

import discord
from discord import app_commands
from discord.ext import commands


class FBan(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="fban", description="Fake ban a user (for fun)")
    @app_commands.describe(user="User to fake ban", reason="Fake reason")
    async def fban(self, interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
        embed = discord.Embed(
            title="👢 User Banned!",
            description=f"**{user}** has been banned from the server!",
            color=0xED4245,
        )
        embed.add_field(name="Reason", value=reason)
        embed.set_footer(text="Just kidding! This is a fake ban.")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FBan(bot))
