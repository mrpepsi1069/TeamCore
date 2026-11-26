"""cogs/record.py — Multi-team win/loss/tie records with history, per server."""

from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from database import teams as T
from utils.embeds import error_embed, success_embed
from utils.moderation import send_mod_log
from utils.permissions import has_manager_perms
from utils.records import (
    MAX_TEAMS, DEFAULT_COMPETITION, add_history, competition_autocomplete, fmt_pct, fmt_record,
    get_team, list_teams, snapshot, sort_teams, team_autocomplete, team_embed, valid_logo,
)
from utils.ui import ConfirmView, brand, send_paged
from utils.validation import sanitize_input
from shared.resources import DEFAULT_TEAM_COLOR

NO_PERMS = lambda: error_embed("Permission Denied", "You need Manager role or higher to change records.")  # noqa: E731


class Record(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="record", description="Manage team records", guild_only=True)

    # ── helpers ──────────────────────────────────────────────
    @staticmethod
    async def _team_role(guild: discord.Guild, team: dict) -> discord.Role | None:
        """Every team is a role too (@Team name), in the default colour when nobody picked one.

        Best effort: a bot without Manage Roles still creates the team, it just has no role.
        """
        try:
            role = await guild.create_role(name=team["name"][:100], colour=discord.Colour(DEFAULT_TEAM_COLOR),
                                           reason=f"TeamCore: team created by {team.get('created_by') or 'a manager'}")
        except discord.HTTPException:
            return None
        await db.col("teams").update_one({"guild_id": str(guild.id), "team_id": team["team_id"]},
                                          {"$set": {"role_id": str(role.id)}})
        return role

    async def _guard(self, interaction: discord.Interaction, team_name: str):
        """Permission + team lookup for write commands. Returns the team or None (reply already sent)."""
        if not await has_manager_perms(interaction):
            await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
            return None
        team = await get_team(interaction.guild_id, team_name)
        if not team:
            await interaction.response.send_message(
                embed=error_embed("Team Not Found", f"No team named **{team_name}**. Use `/record list`."), ephemeral=True
            )
        return team

    async def _changed(self, interaction, team, action, before, note=""):
        """Persist history, log to the server's log channel, and notify live panels/stat channels."""
        after = await get_team(interaction.guild_id, team["name"])
        await add_history(interaction.guild_id, team, action, interaction.user.id, before, snapshot(after), note)
        log = brand("📝 Record Updated", f"**{after['name']}** — {action}\n{fmt_record_arrow(before, after)}")
        log.set_footer(text=f"By {interaction.user}")
        await send_mod_log(interaction.guild, log)
        self.bot.dispatch("team_record_changed", str(interaction.guild_id), team["team_id"])
        return after

    # ── commands ─────────────────────────────────────────────
    @group.command(name="create", description="Create a new team record")
    @app_commands.describe(name="Team name", competition="Competition (default General)", logo="Logo image URL (optional)", division="Division within the competition (optional)")
    @app_commands.autocomplete(competition=competition_autocomplete)
    async def create(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 50],
                     competition: app_commands.Range[str, 1, 50] = DEFAULT_COMPETITION, logo: str = None,
                     division: app_commands.Range[str, 1, 50] = None):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
        name, competition = sanitize_input(name, 50), sanitize_input(competition, 50) or DEFAULT_COMPETITION
        if not name:
            return await interaction.response.send_message(embed=error_embed("Invalid Name", "Name can't be empty."), ephemeral=True)
        if not valid_logo(logo):
            return await interaction.response.send_message(embed=error_embed("Invalid Logo", "Logo must be an http(s) image URL."), ephemeral=True)
        gid = str(interaction.guild_id)
        if len(await list_teams(gid)) >= MAX_TEAMS:
            return await interaction.response.send_message(embed=error_embed("Limit Reached", f"A server can have up to {MAX_TEAMS} teams."), ephemeral=True)
        if await get_team(gid, name):
            return await interaction.response.send_message(embed=error_embed("Already Exists", f"**{name}** already exists."), ephemeral=True)

        try:  # shared with the website dashboard: same ids, same competition/division records, same history
            team = await T.create_team(db.handle(), gid, name, competition, division, logo, interaction.user.id)
        except T.TeamError as e:
            return await interaction.response.send_message(embed=error_embed("Can't Create Team", str(e)), ephemeral=True)
        role = await self._team_role(interaction.guild, team)
        line = f"Created **{name}** in **{competition}**."
        if role is not None:
            line += f"\nRole: {role.mention}"
        await interaction.response.send_message(embed=success_embed("Team Created", line))
        self.bot.dispatch("team_record_changed", gid, team["team_id"])

    @group.command(name="delete", description="Delete a team and its history")
    @app_commands.autocomplete(team=team_autocomplete)
    async def delete(self, interaction: discord.Interaction, team: str):
        t = await self._guard(interaction, team)
        if not t:
            return
        view = ConfirmView(interaction.user.id, "Delete team")
        await interaction.response.send_message(
            embed=error_embed("Delete Team?", f"This permanently deletes **{t['name']}** ({fmt_record(t)}) and its history."), view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return await interaction.edit_original_response(content="Cancelled.", embed=None, view=None)
        gid = str(interaction.guild_id)
        rid = str(t.get("role_id") or "")
        role = interaction.guild.get_role(int(rid)) if rid.isdigit() else None
        if role is not None:  # the team's role goes with it
            try:
                await role.delete(reason="TeamCore: team deleted")
            except discord.HTTPException:
                pass
        await db.col("teams").delete_one({"guild_id": gid, "team_id": t["team_id"]})
        await db.col("team_history").delete_many({"guild_id": gid, "team_id": t["team_id"]})
        await db.col("stats_channels").delete_many({"guild_id": gid, "key": f"team:{t['team_id']}"})  # channel is cleaned up by /stats on next update
        await interaction.edit_original_response(embed=success_embed("Team Deleted", f"Deleted **{t['name']}**."), view=None)
        await send_mod_log(interaction.guild, brand("📝 Team Deleted", f"**{t['name']}** was deleted by {interaction.user.mention}."))
        self.bot.dispatch("team_record_changed", gid, t["team_id"])

    @group.command(name="edit", description="Set a team's record directly")
    @app_commands.describe(team="Team", wins="New wins", losses="New losses", ties="New ties", competition="Move to another competition", logo="New logo URL")
    @app_commands.autocomplete(team=team_autocomplete, competition=competition_autocomplete)
    async def edit(self, interaction: discord.Interaction, team: str, wins: app_commands.Range[int, 0, 9999] = None,
                   losses: app_commands.Range[int, 0, 9999] = None, ties: app_commands.Range[int, 0, 9999] = None,
                   competition: app_commands.Range[str, 1, 50] = None, logo: str = None):
        t = await self._guard(interaction, team)
        if not t:
            return
        updates = {k: v for k, v in (("wins", wins), ("losses", losses), ("ties", ties)) if v is not None}
        if competition:
            c = sanitize_input(competition, 50)
            updates.update(competition=c, competition_lower=c.lower())
        if logo is not None:
            if not valid_logo(logo):
                return await interaction.response.send_message(embed=error_embed("Invalid Logo", "Logo must be an http(s) URL."), ephemeral=True)
            updates["logo"] = logo or None
        if not updates:
            return await interaction.response.send_message(embed=error_embed("Nothing to Change", "Provide at least one value."), ephemeral=True)
        before = snapshot(t)
        await db.col("teams").update_one({"guild_id": str(interaction.guild_id), "team_id": t["team_id"]}, {"$set": updates})
        after = await self._changed(interaction, t, "edited", before, ", ".join(f"{k}={v}" for k, v in updates.items() if k != "competition_lower"))
        await interaction.response.send_message(embed=team_embed(after))

    @group.command(name="view", description="Show a team's record")
    @app_commands.autocomplete(team=team_autocomplete)
    async def view(self, interaction: discord.Interaction, team: str):
        t = await get_team(interaction.guild_id, team)
        if not t:
            return await interaction.response.send_message(embed=error_embed("Team Not Found", f"No team named **{team}**."), ephemeral=True)
        peers = sort_teams(await list_teams(interaction.guild_id, t["competition"]), "wins")
        rank = next((i + 1 for i, p in enumerate(peers) if p["team_id"] == t["team_id"]), None)
        await interaction.response.send_message(embed=team_embed(t, rank, len(peers)))

    @group.command(name="list", description="List all registered teams")
    @app_commands.describe(competition="Only show one competition/division")
    @app_commands.autocomplete(competition=competition_autocomplete)
    async def list_(self, interaction: discord.Interaction, competition: str = None):
        teams = sort_teams(await list_teams(interaction.guild_id, competition), "wins")
        if not teams:
            return await interaction.response.send_message(embed=error_embed("No Teams", "No teams registered yet. Use `/record create`."), ephemeral=True)
        pages = []
        for i in range(0, len(teams), 10):
            e = brand(f"📋 Teams{f' — {competition}' if competition else ''}")
            e.description = "\n".join(
                f"**{i + n + 1}.** {t['name']} — **{fmt_record(t)}** ({fmt_pct(t)}) · *{t['competition']}*" for n, t in enumerate(teams[i:i + 10])
            )
            e.set_footer(text=f"TeamCore • {len(teams)} team(s) • Page {len(pages) + 1}/{(len(teams) + 9) // 10}")
            pages.append(e)
        await send_paged(interaction, pages)

    @group.command(name="rename", description="Rename a team")
    @app_commands.autocomplete(team=team_autocomplete)
    async def rename(self, interaction: discord.Interaction, team: str, new_name: app_commands.Range[str, 1, 50]):
        t = await self._guard(interaction, team)
        if not t:
            return
        new_name = sanitize_input(new_name, 50)
        other = await get_team(interaction.guild_id, new_name)
        if not new_name or (other and other["team_id"] != t["team_id"]):
            return await interaction.response.send_message(embed=error_embed("Invalid Name", "That name is empty or already used."), ephemeral=True)
        await db.col("teams").update_one(
            {"guild_id": str(interaction.guild_id), "team_id": t["team_id"]}, {"$set": {"name": new_name, "name_lower": new_name.lower()}}
        )
        await self._changed(interaction, t, "renamed", snapshot(t), f"{t['name']} → {new_name}")
        await interaction.response.send_message(embed=success_embed("Team Renamed", f"**{t['name']}** → **{new_name}**"))

    @group.command(name="reset", description="Reset a team's record to 0-0-0")
    @app_commands.autocomplete(team=team_autocomplete)
    async def reset(self, interaction: discord.Interaction, team: str):
        t = await self._guard(interaction, team)
        if not t:
            return
        view = ConfirmView(interaction.user.id, "Reset record")
        await interaction.response.send_message(
            embed=error_embed("Reset Record?", f"**{t['name']}** is currently {fmt_record(t)}. History is kept."), view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return await interaction.edit_original_response(content="Cancelled.", embed=None, view=None)
        before = snapshot(t)
        await db.col("teams").update_one({"guild_id": str(interaction.guild_id), "team_id": t["team_id"]}, {"$set": {"wins": 0, "losses": 0, "ties": 0}})
        await self._changed(interaction, t, "reset", before)
        await interaction.edit_original_response(embed=success_embed("Record Reset", f"**{t['name']}** is now 0-0-0."), view=None)

    async def _bump(self, interaction: discord.Interaction, team: str, field: str, count: int):
        t = await self._guard(interaction, team)
        if not t:
            return
        before = snapshot(t)
        await db.col("teams").update_one({"guild_id": str(interaction.guild_id), "team_id": t["team_id"]}, {"$inc": {field: count}})
        after = await self._changed(interaction, t, f"+{count} {field[:-1] if field != 'ties' else 'tie'}", before)
        await interaction.response.send_message(embed=team_embed(after))

    @group.command(name="win", description="Add a win")
    @app_commands.autocomplete(team=team_autocomplete)
    async def win(self, interaction: discord.Interaction, team: str, count: app_commands.Range[int, 1, 50] = 1):
        await self._bump(interaction, team, "wins", count)

    @group.command(name="loss", description="Add a loss")
    @app_commands.autocomplete(team=team_autocomplete)
    async def loss(self, interaction: discord.Interaction, team: str, count: app_commands.Range[int, 1, 50] = 1):
        await self._bump(interaction, team, "losses", count)

    @group.command(name="tie", description="Add a tie")
    @app_commands.autocomplete(team=team_autocomplete)
    async def tie(self, interaction: discord.Interaction, team: str, count: app_commands.Range[int, 1, 50] = 1):
        await self._bump(interaction, team, "ties", count)

    @group.command(name="history", description="Show a team's recent record changes")
    @app_commands.autocomplete(team=team_autocomplete)
    async def history(self, interaction: discord.Interaction, team: str):
        t = await get_team(interaction.guild_id, team)
        if not t:
            return await interaction.response.send_message(embed=error_embed("Team Not Found", f"No team named **{team}**."), ephemeral=True)
        rows = await db.col("team_history").find({"guild_id": str(interaction.guild_id), "team_id": t["team_id"]}).sort("timestamp", -1).to_list(15)
        rows = rows[:15]
        lines = [
            f"📝 {discord.utils.format_dt(r['timestamp'], 'd')} — {r['note']}" if r.get("action") == "note" and r.get("note") else
            f"{discord.utils.format_dt(r['timestamp'], 'R')} — **{r['action']}** by <@{r['by']}>"
            + (f" ({fmt_record(r['after'])})" if r.get("after") and "wins" in r["after"] else "")
            for r in rows
        ]
        await interaction.response.send_message(embed=brand(f"🕘 {t['name']} — History", "\n".join(lines) or "No history yet."), allowed_mentions=discord.AllowedMentions.none())


def fmt_record_arrow(before: dict, after: dict) -> str:
    if not before or "wins" not in before:
        return fmt_record(after)
    return f"{fmt_record(before)} → **{fmt_record(after)}**"


async def setup(bot: commands.Bot):
    await bot.add_cog(Record(bot))
