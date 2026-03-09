"""cogs/setup.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
import database as db


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Configure the bot for your server")
    @app_commands.describe(
        leaguelogchannel="Channel for league logs",
        historychannel="Channel for team history",
        logchannel="Channel for bot logs",
        contractchannel="Channel for player contracts",
        coachrole="Coach role",
        ownerrole="Owner role",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        leaguelogchannel: discord.TextChannel,
        historychannel: discord.TextChannel,
        logchannel: discord.TextChannel,
        contractchannel: discord.TextChannel,
        coachrole: discord.Role,
        ownerrole: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            gid = str(interaction.guild_id)
            await db.create_guild(gid, interaction.guild.name)
            await db.set_guild_channel(gid, "league_log", str(leaguelogchannel.id))
            await db.set_guild_channel(gid, "history", str(historychannel.id))
            await db.set_guild_channel(gid, "log", str(logchannel.id))
            await db.set_guild_channel(gid, "contract", str(contractchannel.id))
            await db.set_guild_role(gid, "coach", str(coachrole.id))
            await db.set_guild_role(gid, "owner", str(ownerrole.id))
            await db.update_guild_setup(gid, True)

            embed = success_embed(
                "Setup Complete",
                f"**Channels:**\n"
                f"League Log: {leaguelogchannel.mention}\n"
                f"History: {historychannel.mention}\n"
                f"Log: {logchannel.mention}\n"
                f"Contract: {contractchannel.mention}\n\n"
                f"**Roles:**\n"
                f"Coach: {coachrole.mention}\n"
                f"Owner: {ownerrole.mention}\n\n"
                f"Your server is now configured!",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"Setup error: {e}")
            await interaction.followup.send(
                embed=error_embed("Setup Failed", "An error occurred. Please try again."), ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
