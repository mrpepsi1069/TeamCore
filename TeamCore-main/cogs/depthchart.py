"""cogs/depthchart.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms
from utils.validation import is_valid_league_abbr, sanitize_input
import database as db

NO_PERMS = lambda: error_embed("Permission Denied", "You need Coach role or higher.")  # noqa: E731


def _has_player(dc: dict, user_id: int) -> bool:
    return any(p["userId"] == str(user_id) for p in (dc.get("players") or []))


def _index_of(dc: dict, user_id: int) -> int:
    return next((i for i, p in enumerate(dc.get("players") or []) if p["userId"] == str(user_id)), -1)


class DepthChart(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="depthchart", description="Manage team depth charts")

    async def _dc_autocomplete(self, interaction: discord.Interaction, current: str):
        dcs = await db.get_all_depth_charts(str(interaction.guild_id))
        return [
            app_commands.Choice(name=f"{d['abbreviation']} - {d['name']}", value=d["abbreviation"])
            for d in dcs
            if current.lower() in d["abbreviation"].lower() or current.lower() in d["name"].lower()
        ][:25]

    async def _load(self, interaction: discord.Interaction, depthchart: str):
        """Defer, then fetch the chart; sends the 'not found' reply itself and returns None if missing."""
        await interaction.response.defer(ephemeral=True)
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True
            )
        return dc

    @group.command(name="create", description="Create a new depth chart")
    @app_commands.describe(name="Depth chart name (e.g., Quarterbacks)", abbreviation="Short name (e.g., QB)")
    async def create(self, interaction: discord.Interaction, name: str, abbreviation: str):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
        abbr = abbreviation.strip().upper()
        if not is_valid_league_abbr(abbr):
            return await interaction.response.send_message(
                embed=error_embed("Invalid Abbreviation", "Use 2-10 letters/numbers, no spaces (e.g. `QB`)."), ephemeral=True
            )
        name = sanitize_input(name, 50)
        await interaction.response.defer(ephemeral=True)
        if await db.get_depth_chart(str(interaction.guild_id), abbr):
            return await interaction.followup.send(
                embed=error_embed("Already Exists", f"Depth chart **{abbr}** already exists."), ephemeral=True
            )
        await db.create_depth_chart(str(interaction.guild_id), name, abbr)
        await interaction.followup.send(
            embed=success_embed("Depth Chart Created", f"Created **{name}** ({abbr}). Use `/depthchart add` to add players!"),
            ephemeral=True,
        )

    @group.command(name="add", description="Add a player to a depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to add")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def add(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
        dc = await self._load(interaction, depthchart)
        if not dc:
            return
        abbr = dc["abbreviation"]  # use the stored abbreviation, not whatever case the user typed
        # add_player_to_depth_chart is atomic and returns False if the player is already there
        if not await db.add_player_to_depth_chart(str(interaction.guild_id), abbr, str(user.id)):
            return await interaction.followup.send(
                embed=error_embed("Already Added", f"{user.mention} is already in **{abbr}**."), ephemeral=True
            )
        pos = len(dc.get("players") or []) + 1
        await interaction.followup.send(
            embed=success_embed("Player Added", f"Added {user.mention} to **{dc['name']}** at position **{abbr}{pos}**."),
            ephemeral=True,
        )

    @group.command(name="remove", description="Remove a player from a depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to remove")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def remove(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
        dc = await self._load(interaction, depthchart)
        if not dc:
            return
        abbr = dc["abbreviation"]
        if not _has_player(dc, user.id):
            return await interaction.followup.send(
                embed=error_embed("Not Found", f"{user.mention} is not in **{abbr}**."), ephemeral=True
            )
        await db.remove_player_from_depth_chart(str(interaction.guild_id), abbr, str(user.id))
        await interaction.followup.send(
            embed=success_embed("Player Removed", f"Removed {user.mention} from **{dc['name']}**."), ephemeral=True
        )

    async def _move(self, interaction: discord.Interaction, depthchart: str, user: discord.User, step: int):
        """step=-1 promotes (towards the top), step=+1 demotes."""
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
        dc = await self._load(interaction, depthchart)
        if not dc:
            return
        abbr = dc["abbreviation"]
        players = dc.get("players") or []
        idx = _index_of(dc, user.id)
        if idx == -1:
            return await interaction.followup.send(
                embed=error_embed("Not Found", f"{user.mention} is not in **{abbr}**."), ephemeral=True
            )
        new_idx = idx + step
        if new_idx < 0:
            return await interaction.followup.send(
                embed=error_embed("Already at Top", f"{user.mention} is already at the top!"), ephemeral=True
            )
        if new_idx >= len(players):
            return await interaction.followup.send(
                embed=error_embed("Already at Bottom", f"{user.mention} is already at the bottom!"), ephemeral=True
            )
        await db.swap_depth_chart_players(str(interaction.guild_id), abbr, idx, new_idx)
        title = "⬆️ Promoted" if step < 0 else "⬇️ Demoted"
        await interaction.followup.send(
            embed=success_embed(title, f"{user.mention}: **{abbr}{idx + 1}** → **{abbr}{new_idx + 1}**"), ephemeral=True
        )

    @group.command(name="promote", description="Promote a player up the depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to promote")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def promote(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        await self._move(interaction, depthchart, user, -1)

    @group.command(name="demote", description="Demote a player down the depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to demote")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def demote(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        await self._move(interaction, depthchart, user, +1)

    @group.command(name="post", description="Display a depth chart")
    @app_commands.describe(depthchart="Select depth chart to post")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def post(self, interaction: discord.Interaction, depthchart: str):
        await interaction.response.defer()
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(
                embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True
            )
        players = dc.get("players") or []
        if not players:
            return await interaction.followup.send(
                embed=error_embed("Empty", f"**{dc['name']}** has no players."), ephemeral=True
            )
        abbr = dc["abbreviation"]
        # Plain mentions: the old code did a fetch_user() API call per player (slow, rate-limited)
        # and silently dropped anyone who failed to resolve.
        lines = [f"**{abbr}{i + 1}:** <@{p['userId']}>" for i, p in enumerate(players)]
        embed = discord.Embed(title=f"{dc['name']}:", description="\n".join(lines)[:4000], color=0x5865F2)
        embed.set_footer(text=f"{interaction.guild.name} • Depth Chart")
        await interaction.followup.send(embed=embed)

    @group.command(name="delete", description="Delete an entire depth chart")
    @app_commands.describe(depthchart="Select depth chart to delete")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def delete(self, interaction: discord.Interaction, depthchart: str):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=NO_PERMS(), ephemeral=True)
        dc = await self._load(interaction, depthchart)
        if not dc:
            return
        await db.delete_depth_chart(str(interaction.guild_id), dc["abbreviation"])
        count = len(dc.get("players") or [])
        await interaction.followup.send(
            embed=success_embed("Deleted", f"Deleted **{dc['name']}** ({dc['abbreviation']}) and {count} player(s)."), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DepthChart(bot))
