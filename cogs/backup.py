"""cogs/backup.py — Save and restore a server's structure (roles, channels, permissions, basic settings)."""

import json
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.backup_io import NOT_SAVED, restore, serialize, summarize
from utils.embeds import error_embed, success_embed
from utils.permissions import is_server_admin
from utils.ui import ConfirmView, brand
from utils.validation import sanitize_input

MAX_BACKUPS = 10
MAX_BYTES = 8_000_000  # stay well under Mongo's 16 MB document limit


async def backup_autocomplete(interaction: discord.Interaction, current: str):
    rows = await db.col("backups").find({"guild_id": str(interaction.guild_id)}).to_list(None)
    return [app_commands.Choice(name=b["name"], value=b["name"]) for b in rows if current.lower() in b["name_lower"]][:25]


class TypeToConfirm(discord.ui.Modal, title="Final confirmation"):
    def __init__(self, view: "WipeConfirm"):
        super().__init__()
        self.v = view
        self.box = discord.ui.TextInput(label="Type RESTORE to delete everything and restore", max_length=10)
        self.add_item(self.box)

    async def on_submit(self, interaction: discord.Interaction):
        if self.box.value.strip() == "RESTORE":
            self.v.value = True
            await interaction.response.defer()
            self.v.stop()
        else:
            await interaction.response.send_message("❌ That wasn't `RESTORE` — nothing was changed.", ephemeral=True)


class WipeConfirm(discord.ui.View):
    """Two-step guard for the destructive restore: a button, then a typed confirmation."""

    def __init__(self, user_id: int):
        super().__init__(timeout=90)
        self.user_id, self.value = user_id, None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Continue…", style=discord.ButtonStyle.danger)
    async def go(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(TypeToConfirm(self))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="backup", description="Save and restore the server structure", guild_only=True)

    async def _admin(self, interaction) -> bool:
        if await is_server_admin(interaction):
            return True
        await interaction.response.send_message(embed=error_embed("Permission Denied", "Only server administrators can use backups."), ephemeral=True)
        return False

    async def _find(self, interaction, name):
        return await db.col("backups").find_one({"guild_id": str(interaction.guild_id), "name_lower": name.strip().lower()})

    @group.command(name="create", description="Save the current channels, roles and permissions")
    async def create(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 50]):
        if not await self._admin(interaction):
            return
        name = sanitize_input(name, 50)
        gid = str(interaction.guild_id)
        if await self._find(interaction, name):
            return await interaction.response.send_message(embed=error_embed("Name Taken", f"A backup called **{name}** already exists."), ephemeral=True)
        if len(await db.col("backups").find({"guild_id": gid}).to_list(None)) >= MAX_BACKUPS:
            return await interaction.response.send_message(embed=error_embed("Limit Reached", f"Keep at most {MAX_BACKUPS} backups — delete one first."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data = serialize(interaction.guild)
        if len(json.dumps(data)) > MAX_BYTES:
            return await interaction.followup.send(embed=error_embed("Too Large", "This server's structure is too big to store."), ephemeral=True)
        stats = summarize(data)
        await db.col("backups").insert_one({
            "guild_id": gid, "name": name, "name_lower": name.lower(), "data": data, "stats": stats,
            "created_at": datetime.now(timezone.utc), "created_by": str(interaction.user.id),
        })
        e = success_embed("Backup Saved", f"**{name}**: {stats['roles']} roles, {stats['categories']} categories, {stats['channels']} channels.")
        e.add_field(name="Not included", value=NOT_SAVED[:1000], inline=False)
        await interaction.followup.send(embed=e, ephemeral=True)

    @group.command(name="list", description="List saved backups")
    async def list_(self, interaction: discord.Interaction):
        if not await self._admin(interaction):
            return
        rows = await db.col("backups").find({"guild_id": str(interaction.guild_id)}).to_list(None)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("No Backups", "Create one with `/backup create`."), ephemeral=True)
        lines = [f"**{b['name']}** — {b['stats']['channels']} channels, {b['stats']['roles']} roles · {discord.utils.format_dt(b['created_at'], 'R')}" for b in rows]
        await interaction.response.send_message(embed=brand("💾 Backups", "\n".join(lines)), ephemeral=True)

    @group.command(name="view", description="Show what a backup contains")
    @app_commands.autocomplete(name=backup_autocomplete)
    async def view(self, interaction: discord.Interaction, name: str):
        if not await self._admin(interaction):
            return
        b = await self._find(interaction, name)
        if not b:
            return await interaction.response.send_message(embed=error_embed("Not Found", f"No backup named **{name}**."), ephemeral=True)
        d = b["data"]
        e = brand(f"💾 {b['name']}")
        e.add_field(name="Created", value=f"{discord.utils.format_dt(b['created_at'], 'F')} by <@{b['created_by']}>", inline=False)
        e.add_field(name="Contents", value=f"{b['stats']['roles']} roles\n{b['stats']['categories']} categories\n{b['stats']['channels']} channels", inline=True)
        e.add_field(name="Source server", value=d.get("guild_name", "?"), inline=True)
        e.add_field(name="Roles", value=", ".join(r["name"] for r in sorted(d["roles"], key=lambda r: -r["position"]))[:1000] or "None", inline=False)
        e.add_field(name="Channels", value=", ".join(f"#{c['name']}" for c in d["channels"])[:1000] or "None", inline=False)
        e.add_field(name="Not included", value=NOT_SAVED[:1000], inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @group.command(name="delete", description="Delete a backup")
    @app_commands.autocomplete(name=backup_autocomplete)
    async def delete(self, interaction: discord.Interaction, name: str):
        if not await self._admin(interaction):
            return
        b = await self._find(interaction, name)
        if not b:
            return await interaction.response.send_message(embed=error_embed("Not Found", f"No backup named **{name}**."), ephemeral=True)
        await db.col("backups").delete_one({"_id": b["_id"]})
        await interaction.response.send_message(embed=success_embed("Backup Deleted", f"**{b['name']}** was deleted."), ephemeral=True)

    @group.command(name="restore", description="Restore a backup (asks for confirmation)")
    @app_commands.describe(mode="Add missing = safe, creates only what's absent. Full = DELETES all channels/roles first.")
    @app_commands.choices(mode=[app_commands.Choice(name="Add missing only (safe)", value="missing"), app_commands.Choice(name="Full restore (deletes everything first)", value="full")])
    @app_commands.autocomplete(name=backup_autocomplete)
    async def restore_(self, interaction: discord.Interaction, name: str, mode: str = "missing"):
        if not await self._admin(interaction):
            return
        b = await self._find(interaction, name)
        if not b:
            return await interaction.response.send_message(embed=error_embed("Not Found", f"No backup named **{name}**."), ephemeral=True)
        need = discord.Permissions(manage_channels=True, manage_roles=True)
        if not interaction.guild.me.guild_permissions.is_superset(need):
            return await interaction.response.send_message(embed=error_embed("Missing Permissions", "I need **Manage Channels** and **Manage Roles**."), ephemeral=True)

        wipe = mode == "full"
        if wipe:
            view = WipeConfirm(interaction.user.id)
            warn = error_embed("⚠️ Full restore — destructive", f"This will **delete every channel and every role I'm able to** in this server, then rebuild from **{b['name']}**. "
                                                               f"Messages in deleted channels are gone forever, and members will lose these roles (I can't re-assign them).")
        else:
            view = ConfirmView(interaction.user.id, "Restore missing items")
            warn = brand("Restore backup?", f"I'll create any roles/channels from **{b['name']}** that don't exist yet. Nothing existing is deleted or changed.", 0xFEE75C)
        await interaction.response.send_message(embed=warn, view=view, ephemeral=True)
        await view.wait()
        if not view.value:
            return await interaction.edit_original_response(content="Cancelled — nothing was changed.", embed=None, view=None)

        await interaction.edit_original_response(content="⏳ Restoring… this can take a few minutes.", embed=None, view=None)
        report = await restore(interaction.guild, b["data"], wipe)
        e = success_embed("Restore Finished", (f"🗑️ {report['deleted']} deleted · " if wipe else "") +
                          f"➕ {report['roles_created']} roles, {report['channels_created']} channels created · "
                          f"⏭️ {report['roles_existing']} roles, {report['channels_existing']} channels already existed")
        if report["failed"]:
            e.add_field(name=f"⚠️ {len(report['failed'])} could not be restored", value=", ".join(report["failed"])[:1000], inline=False)
        e.add_field(name="Not restored", value=NOT_SAVED[:1000], inline=False)
        try:
            await interaction.edit_original_response(content=None, embed=e)
        except discord.HTTPException:
            pass
        try:  # a full restore deletes the channel this ran in, so also DM the result
            await interaction.user.send(embed=e)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
