# cogs/ring_add.py
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
import database
from utils.embeds import success_embed, error_embed
from utils.permissions import has_manager_perms
from utils.validation import sanitize_input, validate_season


class RingAdd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ring-add", description="Grant championship rings to players")
    @app_commands.describe(
        league="League abbreviation",
        season="Season (e.g., S1, 2024)",
        player1="Player 1 (required)",
        opponent="Who you beat in the finals",
        player2="Player 2", player3="Player 3", player4="Player 4", player5="Player 5",
        player6="Player 6", player7="Player 7", player8="Player 8", player9="Player 9",
        player10="Player 10",
    )
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
                embed=error_embed("Permission Denied", "You need Manager role or higher to use this command."),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        league_abbr = league.upper()
        season = sanitize_input(season, 20)
        opponent = sanitize_input(opponent, 100) if opponent else None

        if not validate_season(season):
            return await interaction.followup.send(
                embed=error_embed("Invalid Season", "Season must be between 1-20 characters."), ephemeral=True
            )

        league_doc = await database.get_league_by_abbr(str(interaction.guild_id), league_abbr)
        if not league_doc:
            return await interaction.followup.send(
                embed=error_embed("League Not Found", f"League **{league_abbr}** does not exist.\nUse `/league-add` to create it first."),
                ephemeral=True,
            )

        players = [p for p in [player1, player2, player3, player4, player5, player6, player7, player8, player9, player10] if p]

        results = []
        for player in players:
            await database.create_or_update_user(str(player.id), player.name)
            ring = await database.add_championship_ring(
                str(interaction.guild_id),
                league_doc["_id"],
                str(player.id),
                season,
                opponent,
                str(interaction.user.id),
            )
            if ring:
                results.append(f"✅ {player.mention}")
            else:
                results.append(f"⚠️ {player.mention} (already has ring)")

        congrats_embed = success_embed(
            "💍 Championship Rings Awarded!",
            f"**{league_doc['league_name']} - {season} Champions**"
            + (f"\n\nDefeated **{opponent}** in the finals!" if opponent else "")
            + "\n\n" + ", ".join(p.mention for p in players),
        )

        channels = await database.get_guild_channels(str(interaction.guild_id))
        if channels.get("awards"):
            try:
                awards_channel = interaction.guild.get_channel(int(channels["awards"]))
                if awards_channel:
                    await awards_channel.send(embed=congrats_embed)
            except Exception:
                pass

        await interaction.followup.send(
            embed=success_embed(
                "Rings Granted",
                f"Successfully granted rings to **{len(players)}** player(s) for **{league_doc['league_name']} {season}**!\n\n" + "\n".join(results),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RingAdd(bot))
