"""cogs/match.py — Log and browse match results (shared with the website's Match History page)."""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from database import teams as T
from utils.embeds import error_embed
from utils.moderation import send_mod_log
from utils.permissions import has_manager_perms
from utils.records import competition_autocomplete, get_team, team_autocomplete
from utils.ui import brand, send_paged


class Match(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="match", description="Match results", guild_only=True)

    @group.command(name="log", description="Log a match result (Manager)")
    @app_commands.describe(home="Home team", away="Away team", home_score="Home score", away_score="Away score",
                           competition="Competition (default: home team's)", apply_record="Also add the win/loss/tie to both teams' records", notes="Optional notes")
    @app_commands.autocomplete(home=team_autocomplete, away=team_autocomplete, competition=competition_autocomplete)
    async def log(self, interaction: discord.Interaction, home: str, away: str, home_score: app_commands.Range[int, 0, 9999], away_score: app_commands.Range[int, 0, 9999],
                  competition: str = None, apply_record: bool = True, notes: app_commands.Range[str, 1, 500] = ""):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True)
        h, a = await get_team(interaction.guild_id, home), await get_team(interaction.guild_id, away)
        if not h or not a:
            return await interaction.response.send_message(embed=error_embed("Team Not Found", "Pick both teams from the list."), ephemeral=True)
        try:
            doc, updated = await T.log_match(db.handle(), interaction.guild_id, h, a, home_score, away_score, competition, None, notes, apply_record, interaction.user.id)
        except T.TeamError as e:
            return await interaction.response.send_message(embed=error_embed("Can't Log Match", str(e)), ephemeral=True)
        e = brand("🏟️ Match Logged", f"**{h['name']}** {home_score} – {away_score} **{a['name']}**", 0x57F287)
        e.add_field(name="Competition", value=doc["competition"] or "—", inline=True)
        e.add_field(name="Records", value="Updated ✅" if apply_record else "Not changed", inline=True)
        if notes:
            e.add_field(name="Notes", value=notes, inline=False)
        await interaction.response.send_message(embed=e)
        await send_mod_log(interaction.guild, e)
        for t in updated:  # refresh live leaderboards + stat channels
            self.bot.dispatch("team_record_changed", str(interaction.guild_id), t["team_id"])

    @group.command(name="history", description="Recent match results")
    @app_commands.describe(team="Only matches involving this team", competition="Only this competition")
    @app_commands.autocomplete(team=team_autocomplete, competition=competition_autocomplete)
    async def history(self, interaction: discord.Interaction, team: str = None, competition: str = None):
        g = str(interaction.guild_id)
        q = {"guild_id": g}
        if team:
            t = await get_team(g, team)
            if not t:
                return await interaction.response.send_message(embed=error_embed("Team Not Found", f"No team named **{team}**."), ephemeral=True)
            q["$or"] = [{"home_team_id": t["team_id"]}, {"away_team_id": t["team_id"]}]
        if competition:
            q["competition"] = competition
        rows = await db.col("matches").find(q).sort("played_at", -1).to_list(60)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("No Matches", "Nothing logged yet."), ephemeral=True)
        names = {t["team_id"]: t["name"] for t in await T.list_teams(db.handle(), g)}
        lines = [f"{discord.utils.format_dt(m['played_at'], 'd')} · **{names.get(m['home_team_id'], '?')}** {m['home_score']}–{m['away_score']} **{names.get(m['away_team_id'], '?')}**"
                 + (f" · *{m['competition']}*" if m.get("competition") else "") for m in rows]
        pages = [brand("🏟️ Match History", "\n".join(lines[i:i + 10])) for i in range(0, len(lines), 10)]
        await send_paged(interaction, pages)


async def setup(bot: commands.Bot):
    await bot.add_cog(Match(bot))
