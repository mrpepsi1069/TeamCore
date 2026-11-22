"""cogs/award.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_manager_perms
from utils.validation import sanitize_input, validate_season
import database as db


class Award(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _league_autocomplete(self, interaction: discord.Interaction, current: str):
        leagues = await db.get_leagues(str(interaction.guild_id))
        return [
            app_commands.Choice(name=f"{l['league_abbr']} - {l['league_name']}", value=l["league_abbr"])
            for l in leagues
            if current.lower() in l["league_abbr"].lower() or current.lower() in l["league_name"].lower()
        ][:25]

    @app_commands.command(name="award", description="Give an individual award to a player")
    @app_commands.describe(
        league="League abbreviation",
        award="Award name (e.g., MVP)",
        season="Season (e.g., S1, 2024)",
        member="Player to award",
    )
    @app_commands.autocomplete(league=_league_autocomplete)
    async def award(
        self,
        interaction: discord.Interaction,
        league: str,
        award: str,
        season: str,
        member: discord.User,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )

        league_abbr = league.upper()
        award_name = sanitize_input(award, 100)
        season = sanitize_input(season, 20)

        if not award_name:
            return await interaction.response.send_message(
                embed=error_embed("Invalid Award", "The award name can't be empty."), ephemeral=True
            )
        if not validate_season(season):
            return await interaction.response.send_message(
                embed=error_embed("Invalid Season", "Season must be 1-20 characters."), ephemeral=True
            )

        league_doc = await db.get_league_by_abbr(str(interaction.guild_id), league_abbr)
        if not league_doc:
            return await interaction.response.send_message(
                embed=error_embed("League Not Found", f"No league with abbreviation **{league_abbr}**."), ephemeral=True
            )

        await db.create_or_update_user(str(member.id), member.name)
        result = await db.add_award(
            str(interaction.guild_id), str(league_doc["_id"]),
            str(member.id), award_name, season, str(interaction.user.id)
        )

        if not result:
            return await interaction.response.send_message(
                embed=error_embed("Already Exists", f"{member.mention} already has **{award_name}** for {season}."), ephemeral=True
            )

        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        embed = success_embed(
            f"🏆 {award_name}",
            f"**{league_doc['league_name']} — {season}**\n\nCongratulations to {member.mention} for winning **{award_name}**!",
        )
        if cfg.get("awards_channel"):
            ch = interaction.guild.get_channel(int(cfg["awards_channel"]))
            if ch:
                try:  # the award is already saved — a missing-permission here shouldn't turn into "Command failed"
                    await ch.send(embed=embed)
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(
            embed=success_embed("Award Given", f"Gave **{award_name}** to {member.mention} for **{league_doc['league_name']} {season}**"),
            ephemeral=True,
        )

    @app_commands.command(name="award-remove", description="Remove an individual award from a player (fix a mistake)")
    @app_commands.describe(
        league="League abbreviation",
        award="Award name exactly as given",
        season="Season it was given for",
        member="Player to remove it from",
    )
    @app_commands.autocomplete(league=_league_autocomplete)
    async def award_remove(
        self,
        interaction: discord.Interaction,
        league: str,
        award: str,
        season: str,
        member: discord.User,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        league_abbr = league.upper()
        league_doc = await db.get_league_by_abbr(str(interaction.guild_id), league_abbr)
        if not league_doc:
            return await interaction.response.send_message(
                embed=error_embed("League Not Found", f"No league with abbreviation **{league_abbr}**."), ephemeral=True
            )
        removed = await db.remove_award(
            interaction.guild_id, league_doc["_id"], member.id,
            sanitize_input(award, 100), sanitize_input(season, 20),
        )
        if not removed:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"{member.mention} has no **{award}** award for {season} in {league_abbr}."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=success_embed("Award Removed", f"Removed **{award}** ({league_abbr} {season}) from {member.mention}."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Award(bot))
