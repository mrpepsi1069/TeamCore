"""cogs/ringadd.py"""

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_manager_perms
from utils.validation import sanitize_input, validate_season
import database as db


class RingAdd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _league_autocomplete(self, interaction: discord.Interaction, current: str):
        leagues = await db.get_leagues(str(interaction.guild_id))
        return [
            app_commands.Choice(name=f"{l['league_abbr']} - {l['league_name']}", value=l["league_abbr"])
            for l in leagues
            if current.lower() in l["league_abbr"].lower() or current.lower() in l["league_name"].lower()
        ][:25]

    @app_commands.command(name="ring-add", description="Grant championship rings to players")
    @app_commands.describe(
        league="League abbreviation", season="Season (e.g., S1)", opponent="Team you beat in the finals",
        player1="Player 1", player2="Player 2", player3="Player 3", player4="Player 4", player5="Player 5",
        player6="Player 6", player7="Player 7", player8="Player 8", player9="Player 9", player10="Player 10",
    )
    @app_commands.autocomplete(league=_league_autocomplete)
    async def ring_add(
        self,
        interaction: discord.Interaction,
        league: str,
        season: str,
        player1: discord.Member,
        opponent: Optional[str] = None,
        player2: Optional[discord.Member] = None,
        player3: Optional[discord.Member] = None,
        player4: Optional[discord.Member] = None,
        player5: Optional[discord.Member] = None,
        player6: Optional[discord.Member] = None,
        player7: Optional[discord.Member] = None,
        player8: Optional[discord.Member] = None,
        player9: Optional[discord.Member] = None,
        player10: Optional[discord.Member] = None,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )

        league_abbr = league.upper()
        season = sanitize_input(season, 20)
        opponent = sanitize_input(opponent or "", 100)

        if not validate_season(season):
            return await interaction.response.send_message(
                embed=error_embed("Invalid Season", "Season must be 1-20 characters."), ephemeral=True
            )

        league_doc = await db.get_league_by_abbr(str(interaction.guild_id), league_abbr)
        if not league_doc:
            return await interaction.response.send_message(
                embed=error_embed("League Not Found", f"No league with abbreviation **{league_abbr}**."), ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # De-duplicate: picking the same person in two slots used to report a bogus "already has ring"
        players, seen = [], set()
        for p in [player1, player2, player3, player4, player5, player6, player7, player8, player9, player10]:
            if p and p.id not in seen:
                seen.add(p.id)
                players.append(p)

        results = []
        for p in players:
            await db.create_or_update_user(str(p.id), p.name)
            ring = await db.add_championship_ring(
                str(interaction.guild_id), str(league_doc["_id"]),
                str(p.id), season, opponent, str(interaction.user.id)
            )
            results.append(f"{'✅' if ring else '⚠️ (already has ring)'} {p.mention}")

        self.bot.dispatch("teamcore_stats_changed", str(interaction.guild_id))  # refresh the rings stat channel

        cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
        desc = (
            f"**{league_doc['league_name']} — {season} Champions**"
            + (f"\n\nDefeated **{opponent}** in the finals!" if opponent else "")
            + "\n\n"
            + " ".join(p.mention for p in players)
        )
        congrats = success_embed("💍 Championship Rings Awarded!", desc)
        if cfg.get("awards_channel"):
            try:
                ch = interaction.guild.get_channel(int(cfg["awards_channel"]))
                if ch:
                    await ch.send(embed=congrats)
            except Exception:
                pass

        await interaction.followup.send(
            embed=success_embed("Rings Granted", f"Processed **{len(players)}** player(s):\n\n" + "\n".join(results)),
            ephemeral=True,
        )

    @app_commands.command(name="ring-remove", description="Remove a championship ring from a player (fix a mistake)")
    @app_commands.describe(league="League abbreviation", season="Season the ring was for", member="Player to remove it from")
    @app_commands.autocomplete(league=_league_autocomplete)
    async def ring_remove(self, interaction: discord.Interaction, league: str, season: str, member: discord.User):
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
        removed = await db.remove_championship_ring(
            interaction.guild_id, league_doc["_id"], member.id, sanitize_input(season, 20)
        )
        if not removed:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"{member.mention} has no {league_abbr} ring for {season}."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=success_embed("Ring Removed", f"Removed the **{league_abbr} {season}** ring from {member.mention}."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RingAdd(bot))
