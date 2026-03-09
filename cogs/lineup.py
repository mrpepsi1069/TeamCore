"""cogs/lineup.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed, lineup_embed
from utils.permissions import has_manager_perms
from utils.validation import sanitize_input
import database as db


class Lineup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="lineup", description="Manage team lineups")

    # ── Autocomplete ───────────────────────────────────────
    async def _lineup_autocomplete(self, interaction: discord.Interaction, current: str):
        lineups = await db.get_lineups(str(interaction.guild_id))
        return [
            app_commands.Choice(name=l["lineup_name"], value=l["lineup_name"])
            for l in lineups
            if current.lower() in l["lineup_name"].lower()
        ][:25]

    # ── Subcommands ────────────────────────────────────────
    @group.command(name="create", description="Create a new lineup")
    @app_commands.describe(name="Lineup name", description="Optional description")
    async def create(self, interaction: discord.Interaction, name: str, description: str = ""):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        name = sanitize_input(name, 50)
        try:
            await db.create_lineup(str(interaction.guild_id), name, sanitize_input(description, 200), str(interaction.user.id))
            await interaction.response.send_message(
                embed=success_embed("Lineup Created", f"Created **{name}**! Use `/lineup add` to add players.")
            )
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Already Exists", f"A lineup named **{name}** already exists."), ephemeral=True
            )

    @group.command(name="add", description="Add a player to a lineup")
    @app_commands.describe(name="Lineup name", player="Player to add", position="Position (e.g., QB)")
    @app_commands.autocomplete(name=_lineup_autocomplete)
    async def add(self, interaction: discord.Interaction, name: str, player: discord.User, position: str):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        lu = await db.get_lineup(str(interaction.guild_id), sanitize_input(name))
        if not lu:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"Lineup **{name}** does not exist."), ephemeral=True
            )
        await db.create_or_update_user(str(player.id), player.name)
        pos = sanitize_input(position, 50).upper()
        await db.add_player_to_lineup(lu["_id"], str(player.id), pos)
        await interaction.response.send_message(
            embed=success_embed("Player Added", f"Added {player.mention} to **{name}** as **{pos}**")
        )

    @group.command(name="remove", description="Remove a player from a lineup")
    @app_commands.describe(name="Lineup name", player="Player to remove")
    @app_commands.autocomplete(name=_lineup_autocomplete)
    async def remove(self, interaction: discord.Interaction, name: str, player: discord.User):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        lu = await db.get_lineup(str(interaction.guild_id), sanitize_input(name))
        if not lu:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"Lineup **{name}** does not exist."), ephemeral=True
            )
        await db.remove_player_from_lineup(lu["_id"], str(player.id))
        await interaction.response.send_message(
            embed=success_embed("Player Removed", f"Removed {player.mention} from **{name}**")
        )

    @group.command(name="edit", description="Edit a player's position in a lineup")
    @app_commands.describe(name="Lineup name", player="Player to edit", position="New position")
    @app_commands.autocomplete(name=_lineup_autocomplete)
    async def edit(self, interaction: discord.Interaction, name: str, player: discord.User, position: str):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        lu = await db.get_lineup(str(interaction.guild_id), sanitize_input(name))
        if not lu:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"Lineup **{name}** does not exist."), ephemeral=True
            )
        pos = sanitize_input(position, 50).upper()
        await db.add_player_to_lineup(lu["_id"], str(player.id), pos)
        await interaction.response.send_message(
            embed=success_embed("Position Updated", f"Updated {player.mention}'s position to **{pos}** in **{name}**")
        )

    @group.command(name="view", description="View a specific lineup")
    @app_commands.describe(name="Lineup name")
    @app_commands.autocomplete(name=_lineup_autocomplete)
    async def view(self, interaction: discord.Interaction, name: str):
        lu = await db.get_lineup(str(interaction.guild_id), sanitize_input(name))
        if not lu:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"Lineup **{name}** does not exist."), ephemeral=True
            )
        await interaction.response.send_message(embed=lineup_embed(lu))

    @group.command(name="list", description="View all lineups")
    async def list_(self, interaction: discord.Interaction):
        lineups = await db.get_lineups(str(interaction.guild_id))
        if not lineups:
            return await interaction.response.send_message(
                embed=error_embed("No Lineups", "No lineups exist yet. Use `/lineup create`."), ephemeral=True
            )
        items = "\n".join(
            f"• **{l['lineup_name']}**" + (f" — {l['description']}" if l.get("description") else "")
            for l in lineups
        )
        await interaction.response.send_message(embed=success_embed("Server Lineups", items))

    @group.command(name="delete", description="Delete a lineup")
    @app_commands.describe(name="Lineup name")
    @app_commands.autocomplete(name=_lineup_autocomplete)
    async def delete(self, interaction: discord.Interaction, name: str):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        lu = await db.get_lineup(str(interaction.guild_id), sanitize_input(name))
        if not lu:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"Lineup **{name}** does not exist."), ephemeral=True
            )
        await db.delete_lineup(str(interaction.guild_id), name)
        await interaction.response.send_message(
            embed=success_embed("Lineup Deleted", f"Deleted **{name}**")
        )

    @group.command(name="post", description="Post a lineup to a channel")
    @app_commands.describe(name="Lineup name", channel="Channel to post to (defaults to current)")
    @app_commands.autocomplete(name=_lineup_autocomplete)
    async def post(self, interaction: discord.Interaction, name: str, channel: discord.TextChannel = None):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )
        lu = await db.get_lineup(str(interaction.guild_id), sanitize_input(name))
        if not lu:
            return await interaction.response.send_message(
                embed=error_embed("Not Found", f"Lineup **{name}** does not exist."), ephemeral=True
            )
        target = channel or interaction.channel
        await target.send(embed=lineup_embed(lu))
        await interaction.response.send_message(
            embed=success_embed("Posted", f"Posted **{name}** to {target.mention}"), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Lineup(bot))
