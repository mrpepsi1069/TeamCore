"""cogs/roster.py — Team rosters (shared with the website's Rosters page)."""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from database import teams as T
from utils.embeds import error_embed, success_embed
from utils.permissions import has_manager_perms
from utils.records import get_team, team_autocomplete
from utils.ui import brand, send_paged

ICON = {"active": "🟢", "reserve": "🟡", "injured": "🩹", "inactive": "⚫"}
STATUS_CHOICES = [app_commands.Choice(name=s.title(), value=s) for s in T.ROSTER_STATUSES]


class Roster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="roster", description="Team rosters", guild_only=True)

    async def _team(self, interaction, name):
        team = await get_team(interaction.guild_id, name)
        if not team:
            await interaction.response.send_message(embed=error_embed("Team Not Found", f"No team named **{name}**."), ephemeral=True)
        return team

    @group.command(name="add", description="Add a player to a team's roster (Manager)")
    @app_commands.describe(team="Team", player="Player", position="e.g. QB", jersey="Jersey number", status="Roster status")
    @app_commands.autocomplete(team=team_autocomplete)
    @app_commands.choices(status=STATUS_CHOICES)
    async def add(self, interaction: discord.Interaction, team: str, player: discord.Member, position: app_commands.Range[str, 1, 40] = "",
                  jersey: app_commands.Range[int, 0, 999] = None, status: str = "active"):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True)
        t = await self._team(interaction, team)
        if not t:
            return
        try:
            await T.add_roster_member(db.handle(), interaction.guild_id, t, str(player.id), player.display_name, position, jersey, status, "", interaction.user.id)
        except T.TeamError as e:
            return await interaction.response.send_message(embed=error_embed("Can't Add Player", str(e)), ephemeral=True)
        await interaction.response.send_message(embed=success_embed("Added to Roster", f"{player.mention} joined **{t['name']}**" + (f" as **{position}**" if position else "") + (f" #{jersey}" if jersey is not None else "") + "."),
                                                allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="remove", description="Remove a player from a team's roster (Manager)")
    @app_commands.autocomplete(team=team_autocomplete)
    async def remove(self, interaction: discord.Interaction, team: str, player: discord.User):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True)
        t = await self._team(interaction, team)
        if not t:
            return
        if not await T.remove_roster_member(db.handle(), interaction.guild_id, t["team_id"], str(player.id)):
            return await interaction.response.send_message(embed=error_embed("Not On Roster", f"{player.mention} isn't on **{t['name']}**."), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message(embed=success_embed("Removed", f"{player.mention} was removed from **{t['name']}**."), allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="list", description="Show a team's roster")
    @app_commands.autocomplete(team=team_autocomplete)
    async def list_(self, interaction: discord.Interaction, team: str):
        t = await self._team(interaction, team)
        if not t:
            return
        rows = await T.list_roster(db.handle(), interaction.guild_id, t["team_id"])
        if not rows:
            return await interaction.response.send_message(embed=error_embed("Empty Roster", f"**{t['name']}** has no players yet. Use `/roster add`."), ephemeral=True)
        lines = [f"{ICON.get(m['status'], '▫️')} {'#' + str(m['jersey']) + ' ' if m.get('jersey') is not None else ''}<@{m['user_id']}>" + (f" — {m['position']}" if m.get("position") else "") for m in rows]
        pages = []
        for i in range(0, len(lines), 15):
            e = brand(f"👥 {t['name']} — Roster ({len(rows)})", "\n".join(lines[i:i + 15]))
            if t.get("logo"):
                e.set_thumbnail(url=t["logo"])
            pages.append(e)
        await send_paged(interaction, pages, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="player", description="Which teams is a player on?")
    async def player(self, interaction: discord.Interaction, player: discord.Member = None):
        player = player or interaction.user
        rows = await T.teams_of_user(db.handle(), interaction.guild_id, str(player.id))
        if not rows:
            return await interaction.response.send_message(embed=error_embed("No Teams", f"{player.display_name} isn't on any roster."), ephemeral=True)
        lines = [f"**{m['team_name']}** — {m.get('position') or 'no position'}" + (f" #{m['jersey']}" if m.get("jersey") is not None else "") + f" ({m['status']})" for m in rows]
        await interaction.response.send_message(embed=brand(f"🪪 {player.display_name}", "\n".join(lines)))


async def setup(bot: commands.Bot):
    await bot.add_cog(Roster(bot))
