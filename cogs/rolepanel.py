"""cogs/rolepanel.py — Button / dropdown self-assign role panels (persistent, with safety checks)."""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import is_manager_or_mod
from utils.ui import brand
from utils.validation import sanitize_input

# A panel must never be a way to self-grant moderation power, even if a role gains these later.
DANGEROUS = discord.Permissions(
    administrator=True, manage_guild=True, manage_roles=True, manage_channels=True, kick_members=True,
    ban_members=True, moderate_members=True, manage_webhooks=True, manage_messages=True, view_audit_log=True,
).value
MAX_ROLES_PER_PANEL = 25


def unsafe_reason(role: discord.Role, guild: discord.Guild) -> str | None:
    if role.is_default() or role.managed:
        return "that role is managed by Discord/an integration"
    if role >= guild.me.top_role:
        return "it's above my highest role, so I couldn't assign it"
    if role.permissions.value & DANGEROUS:
        return "it has moderation/administrative permissions"
    return None


def panel_embed(p: dict) -> discord.Embed:
    lines = [f"{r.get('emoji') or '▫️'} <@&{r['role_id']}>" + (f" — {r['label']}" if r.get("label") else "") for r in p["roles"]]
    e = brand(p["title"], (p.get("description") or "") + ("\n\n" + "\n".join(lines) if lines else "\n\n*No roles yet — an admin can add some with `/rolepanel add`.*"))
    if p.get("max_roles"):
        e.set_footer(text=f"TeamCore • Pick up to {p['max_roles']} role(s)")
    return e


def _emoji(text: str | None):
    if not text:
        return None
    try:
        return discord.PartialEmoji.from_str(text.strip())
    except Exception:
        return None


def panel_view(p: dict) -> discord.ui.View | None:
    if not p["roles"]:
        return None
    v = discord.ui.View(timeout=None)
    if p["style"] == "dropdown":
        opts = [discord.SelectOption(label=(r.get("label") or r["name"])[:100], value=r["role_id"], emoji=_emoji(r.get("emoji"))) for r in p["roles"]]
        v.add_item(discord.ui.Select(
            placeholder="Choose your roles…", options=opts, min_values=0, max_values=min(len(opts), p["max_roles"] or len(opts)),
            custom_id=f"rolepanel:{p['panel_id']}:select",
        ))
    else:
        for i, r in enumerate(p["roles"]):
            v.add_item(discord.ui.Button(label=(r.get("label") or r["name"])[:80], emoji=_emoji(r.get("emoji")), style=discord.ButtonStyle.secondary,
                                         custom_id=f"rolepanel:{p['panel_id']}:{r['role_id']}", row=i // 5))
    return v


async def panel_autocomplete(interaction: discord.Interaction, current: str):
    rows = await db.col("role_panels").find({"guild_id": str(interaction.guild_id)}).to_list(None)
    return [app_commands.Choice(name=f"#{p['panel_id']} {p['title']}"[:100], value=p["panel_id"]) for p in rows if current.lower() in p["title"].lower() or current in str(p["panel_id"])][:25]


class RolePanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="rolepanel", description="Self-assign role panels", guild_only=True)

    async def _admin(self, interaction) -> bool:
        if await is_manager_or_mod(interaction):
            return True
        await interaction.response.send_message(embed=error_embed("Permission Denied", "You need a Manager role or Manage Server."), ephemeral=True)
        return False

    async def _get(self, gid, pid):
        return await db.col("role_panels").find_one({"guild_id": str(gid), "panel_id": pid})

    async def _render(self, guild: discord.Guild, p: dict) -> bool:
        ch = guild.get_channel(int(p["channel_id"]))
        try:
            msg = await ch.fetch_message(int(p["message_id"])) if ch else None
            if msg:
                await msg.edit(embed=panel_embed(p), view=panel_view(p))
            return msg is not None
        except discord.HTTPException:
            return False

    @group.command(name="create", description="Create a role panel")
    @app_commands.describe(title="Panel title", description="Panel text", style="Buttons or a dropdown", max_roles="Max roles a member may hold from this panel (0 = unlimited)")
    @app_commands.choices(style=[app_commands.Choice(name="Buttons", value="buttons"), app_commands.Choice(name="Dropdown", value="dropdown")])
    async def create(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 200], description: app_commands.Range[str, 0, 1000] = "",
                     style: str = "buttons", max_roles: app_commands.Range[int, 0, 25] = 0, channel: discord.TextChannel = None):
        if not await self._admin(interaction):
            return
        channel = channel or interaction.channel
        gid = str(interaction.guild_id)
        p = {"guild_id": gid, "panel_id": await db.next_seq(gid, "rolepanel"), "channel_id": str(channel.id), "message_id": None,
             "title": sanitize_input(title, 200), "description": description.replace("\\n", "\n"), "style": style, "max_roles": max_roles, "roles": []}
        try:
            msg = await channel.send(embed=panel_embed(p))
        except discord.HTTPException:
            return await interaction.response.send_message(embed=error_embed("Couldn't Post", f"I can't post in {channel.mention}."), ephemeral=True)
        p["message_id"] = str(msg.id)
        await db.col("role_panels").insert_one(dict(p))
        await interaction.response.send_message(embed=success_embed("Panel Created", f"Panel **#{p['panel_id']}** posted. Add roles with `/rolepanel add panel:{p['panel_id']}`."), ephemeral=True)

    @group.command(name="add", description="Add a role to a panel")
    @app_commands.describe(label="Button/option text (default: role name)", emoji="Emoji shown next to it")
    @app_commands.autocomplete(panel=panel_autocomplete)
    async def add(self, interaction: discord.Interaction, panel: int, role: discord.Role, label: app_commands.Range[str, 1, 80] = None, emoji: app_commands.Range[str, 1, 60] = None):
        if not await self._admin(interaction):
            return
        p = await self._get(interaction.guild_id, panel)
        if not p:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No such panel."), ephemeral=True)
        if len(p["roles"]) >= MAX_ROLES_PER_PANEL or any(r["role_id"] == str(role.id) for r in p["roles"]):
            return await interaction.response.send_message(embed=error_embed("Can't Add", "The panel is full or already has that role."), ephemeral=True)
        why = unsafe_reason(role, interaction.guild)
        if why:
            return await interaction.response.send_message(embed=error_embed("Role Not Allowed", f"I can't put **{role.name}** on a panel: {why}."), ephemeral=True)
        if interaction.user.id != interaction.guild.owner_id and role >= interaction.user.top_role:
            return await interaction.response.send_message(embed=error_embed("Role Not Allowed", "You can only add roles below your own highest role."), ephemeral=True)
        entry = {"role_id": str(role.id), "name": role.name, "label": label, "emoji": emoji}
        await db.col("role_panels").update_one({"_id": p["_id"]}, {"$push": {"roles": entry}})
        p["roles"].append(entry)
        shown = await self._render(interaction.guild, p)
        await interaction.response.send_message(embed=success_embed("Role Added", f"Added {role.mention} to panel #{panel}." + ("" if shown else " (The panel message is missing — use `/rolepanel refresh`.)")), ephemeral=True)

    @group.command(name="remove", description="Remove a role from a panel")
    @app_commands.autocomplete(panel=panel_autocomplete)
    async def remove(self, interaction: discord.Interaction, panel: int, role: discord.Role):
        if not await self._admin(interaction):
            return
        p = await self._get(interaction.guild_id, panel)
        if not p or not any(r["role_id"] == str(role.id) for r in p["roles"]):
            return await interaction.response.send_message(embed=error_embed("Not Found", "That panel or role isn't there."), ephemeral=True)
        await db.col("role_panels").update_one({"_id": p["_id"]}, {"$pull": {"roles": {"role_id": str(role.id)}}})
        p["roles"] = [r for r in p["roles"] if r["role_id"] != str(role.id)]
        await self._render(interaction.guild, p)
        await interaction.response.send_message(embed=success_embed("Role Removed", f"Removed {role.mention} from panel #{panel}."), ephemeral=True)

    @group.command(name="delete", description="Delete a panel and its message")
    @app_commands.autocomplete(panel=panel_autocomplete)
    async def delete(self, interaction: discord.Interaction, panel: int):
        if not await self._admin(interaction):
            return
        p = await self._get(interaction.guild_id, panel)
        if not p:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No such panel."), ephemeral=True)
        ch = interaction.guild.get_channel(int(p["channel_id"]))
        try:
            if ch:
                await (await ch.fetch_message(int(p["message_id"]))).delete()
        except discord.HTTPException:
            pass
        await db.col("role_panels").delete_one({"_id": p["_id"]})
        await interaction.response.send_message(embed=success_embed("Panel Deleted", f"Panel #{panel} removed."), ephemeral=True)

    @group.command(name="refresh", description="Re-post a panel whose message was deleted")
    @app_commands.autocomplete(panel=panel_autocomplete)
    async def refresh(self, interaction: discord.Interaction, panel: int):
        if not await self._admin(interaction):
            return
        p = await self._get(interaction.guild_id, panel)
        if not p:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No such panel."), ephemeral=True)
        if await self._render(interaction.guild, p):
            return await interaction.response.send_message(embed=success_embed("Refreshed", "Panel updated in place."), ephemeral=True)
        ch = interaction.channel
        msg = await ch.send(embed=panel_embed(p), view=panel_view(p))
        await db.col("role_panels").update_one({"_id": p["_id"]}, {"$set": {"channel_id": str(ch.id), "message_id": str(msg.id)}})
        await interaction.response.send_message(embed=success_embed("Re-posted", f"Panel posted again in {ch.mention}."), ephemeral=True)

    @group.command(name="list", description="List this server's panels")
    async def list_(self, interaction: discord.Interaction):
        rows = await db.col("role_panels").find({"guild_id": str(interaction.guild_id)}).to_list(None)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("None", "No panels yet."), ephemeral=True)
        lines = [f"**#{p['panel_id']}** {p['title']} — {p['style']}, {len(p['roles'])} role(s) · <#{p['channel_id']}>" for p in rows]
        await interaction.response.send_message(embed=brand("🎭 Role Panels", "\n".join(lines)), ephemeral=True)

    # ── using a panel ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild_id is None:
            return
        data = interaction.data or {}
        cid = data.get("custom_id", "")
        if not cid.startswith("rolepanel:"):
            return
        try:
            _, pid, action = cid.split(":")
            pid = int(pid)
        except ValueError:
            return
        await interaction.response.defer(ephemeral=True)
        p = await self._get(interaction.guild_id, pid)
        member, guild = interaction.user, interaction.guild
        if not p or not isinstance(member, discord.Member):
            return await interaction.followup.send("❌ This panel no longer exists.", ephemeral=True)

        panel_ids = {r["role_id"] for r in p["roles"]}
        have = {str(r.id) for r in member.roles} & panel_ids
        desired = set(data.get("values") or []) & panel_ids if action == "select" else (have ^ {action}) & panel_ids  # button toggles one role
        if action != "select" and action not in panel_ids:
            return await interaction.followup.send("❌ That role was removed from this panel.", ephemeral=True)
        if p.get("max_roles") and len(desired) > p["max_roles"]:
            return await interaction.followup.send(f"❌ You can hold at most **{p['max_roles']}** role(s) from this panel. Remove one first.", ephemeral=True)

        add_ids, remove_ids = desired - have, have - desired
        to_add, blocked = [], []
        for rid in add_ids:
            role = guild.get_role(int(rid))
            if role is None or unsafe_reason(role, guild):  # re-checked now: a role may have gained dangerous permissions since it was added
                blocked.append(role.name if role else rid)
            else:
                to_add.append(role)
        to_remove = [r for r in (guild.get_role(int(x)) for x in remove_ids) if r]
        try:
            if to_add:
                await member.add_roles(*to_add, reason=f"Role panel #{pid}")
            if to_remove:
                await member.remove_roles(*to_remove, reason=f"Role panel #{pid}")
        except discord.Forbidden:
            return await interaction.followup.send("❌ I can't manage those roles — an admin needs to move my role higher.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send("❌ Discord rejected that change. Please try again.", ephemeral=True)

        parts = []
        if to_add:
            parts.append("➕ " + ", ".join(r.mention for r in to_add))
        if to_remove:
            parts.append("➖ " + ", ".join(r.mention for r in to_remove))
        if blocked:
            parts.append("🚫 Not allowed: " + ", ".join(blocked))
        await interaction.followup.send("\n".join(parts) or "No changes.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePanel(bot))
