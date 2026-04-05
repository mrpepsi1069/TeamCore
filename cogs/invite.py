"""cogs/invite.py"""

import discord
from discord import app_commands
from discord.ext import commands


class Invite(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="invite", description="Get the bot invite link")
    async def invite(self, interaction: discord.Interaction):
        link = (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={self.bot.user.id}&permissions=8&scope=bot%20applications.commands"
        )
        embed = discord.Embed(
            title="📨 Invite TeamCore Bot",
            description=f"[Click here to invite me to your server!]({link})",
            color=0x5865F2,
        )
        embed.set_footer(text="By Ghostie")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Invite(bot))
