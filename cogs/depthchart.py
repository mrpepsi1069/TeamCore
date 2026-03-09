"""cogs/depthchart.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms
import database as db


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

    @group.command(name="create", description="Create a new depth chart")
    @app_commands.describe(name="Depth chart name (e.g., Quarterbacks)", abbreviation="Short name (e.g., QB)")
    async def create(self, interaction: discord.Interaction, name: str, abbreviation: str):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True
            )
        abbr = abbreviation.upper()[:10]
        await interaction.response.defer(ephemeral=True)
        existing = await db.get_depth_chart(str(interaction.guild_id), abbr)
        if existing:
            return await interaction.followup.send(
                embed=error_embed("Already Exists", f"Depth chart **{abbr}** already exists."), ephemeral=True
            )
        await db.create_depth_chart(str(interaction.guild_id), name[:50], abbr)
        await interaction.followup.send(
            embed=success_embed("Depth Chart Created", f"Created **{name}** ({abbr}). Use `/depthchart add` to add players!"),
            ephemeral=True,
        )

    @group.command(name="add", description="Add a player to a depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to add")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def add(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True)
        if any(p["userId"] == str(user.id) for p in (dc.get("players") or [])):
            return await interaction.followup.send(embed=error_embed("Already Added", f"{user.mention} is already in **{depthchart}**."), ephemeral=True)
        await db.add_player_to_depth_chart(str(interaction.guild_id), depthchart, str(user.id))
        pos = len(dc.get("players") or []) + 1
        await interaction.followup.send(
            embed=success_embed("Player Added", f"Added {user.mention} to **{dc['name']}** at position **{depthchart}{pos}**."),
            ephemeral=True,
        )

    @group.command(name="remove", description="Remove a player from a depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to remove")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def remove(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True)
        if not any(p["userId"] == str(user.id) for p in (dc.get("players") or [])):
            return await interaction.followup.send(embed=error_embed("Not Found", f"{user.mention} is not in **{depthchart}**."), ephemeral=True)
        await db.remove_player_from_depth_chart(str(interaction.guild_id), depthchart, str(user.id))
        await interaction.followup.send(embed=success_embed("Player Removed", f"Removed {user.mention} from **{dc['name']}**."), ephemeral=True)

    @group.command(name="promote", description="Promote a player up the depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to promote")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def promote(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True)
        players = dc.get("players") or []
        idx = next((i for i, p in enumerate(players) if p["userId"] == str(user.id)), -1)
        if idx == -1:
            return await interaction.followup.send(embed=error_embed("Not Found", f"{user.mention} is not in **{depthchart}**."), ephemeral=True)
        if idx == 0:
            return await interaction.followup.send(embed=error_embed("Already at Top", f"{user.mention} is already at the top!"), ephemeral=True)
        await db.swap_depth_chart_players(str(interaction.guild_id), depthchart, idx, idx - 1)
        await interaction.followup.send(embed=success_embed("⬆️ Promoted", f"{user.mention}: **{depthchart}{idx + 1}** → **{depthchart}{idx}**"), ephemeral=True)

    @group.command(name="demote", description="Demote a player down the depth chart")
    @app_commands.describe(depthchart="Select depth chart", user="Player to demote")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def demote(self, interaction: discord.Interaction, depthchart: str, user: discord.User):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True)
        players = dc.get("players") or []
        idx = next((i for i, p in enumerate(players) if p["userId"] == str(user.id)), -1)
        if idx == -1:
            return await interaction.followup.send(embed=error_embed("Not Found", f"{user.mention} is not in **{depthchart}**."), ephemeral=True)
        if idx == len(players) - 1:
            return await interaction.followup.send(embed=error_embed("Already at Bottom", f"{user.mention} is already at the bottom!"), ephemeral=True)
        await db.swap_depth_chart_players(str(interaction.guild_id), depthchart, idx, idx + 1)
        await interaction.followup.send(embed=success_embed("⬇️ Demoted", f"{user.mention}: **{depthchart}{idx + 1}** → **{depthchart}{idx + 2}**"), ephemeral=True)

    @group.command(name="post", description="Display a depth chart")
    @app_commands.describe(depthchart="Select depth chart to post")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def post(self, interaction: discord.Interaction, depthchart: str):
        await interaction.response.defer()
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True)
        players = dc.get("players") or []
        if not players:
            return await interaction.followup.send(embed=error_embed("Empty", f"**{dc['name']}** has no players."), ephemeral=True)
        lines = []
        for i, p in enumerate(players):
            try:
                u = await self.bot.fetch_user(int(p["userId"]))
                lines.append(f"**{depthchart}{i + 1}:** {u.mention}")
            except Exception:
                pass
        embed = discord.Embed(title=f"{dc['name']}:", description="\n".join(lines) or "No players found", color=0x5865F2)
        embed.set_footer(text=f"{interaction.guild.name} • Depth Chart")
        await interaction.followup.send(embed=embed)

    @group.command(name="delete", description="Delete an entire depth chart")
    @app_commands.describe(depthchart="Select depth chart to delete")
    @app_commands.autocomplete(depthchart=_dc_autocomplete)
    async def delete(self, interaction: discord.Interaction, depthchart: str):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        dc = await db.get_depth_chart(str(interaction.guild_id), depthchart)
        if not dc:
            return await interaction.followup.send(embed=error_embed("Not Found", f"Depth chart **{depthchart}** doesn't exist."), ephemeral=True)
        await db.delete_depth_chart(str(interaction.guild_id), depthchart)
        count = len(dc.get("players") or [])
        await interaction.followup.send(embed=success_embed("Deleted", f"Deleted **{dc['name']}** ({depthchart}) and {count} player(s)."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DepthChart(bot))
