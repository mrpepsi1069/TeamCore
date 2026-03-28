"""cogs/templateuse.py — Apply a Discord server template"""

import discord
from discord import app_commands
from discord.ext import commands

# ── Templates registry ──────────────────────────────────────────
TEMPLATES = {
    "TeamCore Default": {
        "code": "8e3TuU57zRaE",
        "description": "Standard TeamCore layout with channels, roles & permissions",
        "emoji": "🏈",
    },
}


# ── Confirmation view ────────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, template_name: str, template_code: str):
        super().__init__(timeout=30)
        self.template_name = template_name
        self.template_code = template_code
        self.confirmed = False

    @discord.ui.button(label="Yes, apply template", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="⏳ Applying template... this may take a moment.", view=self)
        await apply_template(interaction, self.template_code)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except Exception:
            pass


# ── Template select view ─────────────────────────────────────────
class TemplateSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        options = [
            discord.SelectOption(
                label=name,
                description=data["description"][:100],
                emoji=data["emoji"],
                value=name,
            )
            for name, data in TEMPLATES.items()
        ]
        self.select = discord.ui.Select(
            placeholder="Choose a template...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        chosen = self.select.values[0]
        template = TEMPLATES[chosen]
        self.stop()

        confirm_view = ConfirmView(chosen, template["code"])
        await interaction.response.edit_message(
            content=(
                f"## {template['emoji']} {chosen}\n"
                f"{template['description']}\n\n"
                f"⚠️ **WARNING:** This will **delete ALL existing channels and roles** and replace them with the template.\n"
                f"**This cannot be undone.** Are you sure?"
            ),
            view=confirm_view,
        )


# ── Core apply logic ─────────────────────────────────────────────
async def apply_template(interaction: discord.Interaction, template_code: str):
    guild = interaction.guild
    bot = interaction.client

    try:
        # Fetch template from Discord
        template = await bot.fetch_guild_template(template_code)

        # Delete all existing channels
        for channel in guild.channels:
            try:
                await channel.delete(reason="Template application")
            except Exception:
                pass

        # Delete all non-default roles (skip @everyone and bot roles)
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            try:
                await role.delete(reason="Template application")
            except Exception:
                pass

        # Apply the template — creates all roles + channels from it
        await template.sync()

        # Use Guild.edit to apply template to THIS guild
        # Discord.py doesn't have a direct "apply template to existing guild" method
        # so we manually create from the serialized template data
        serialized = template.source_guild

        # Create roles first
        role_map = {}  # template role id -> created role
        for t_role in sorted(serialized.roles, key=lambda r: r.get("position", 0)):
            if t_role.get("id") == 0:  # @everyone
                continue
            try:
                perms = discord.Permissions(t_role.get("permissions", 0))
                color = discord.Color(t_role.get("color", 0))
                new_role = await guild.create_role(
                    name=t_role["name"],
                    permissions=perms,
                    color=color,
                    hoist=t_role.get("hoist", False),
                    mentionable=t_role.get("mentionable", False),
                    reason="Template application",
                )
                role_map[t_role["id"]] = new_role
            except Exception:
                pass

        # Create categories first, then channels
        category_map = {}  # template channel id -> created category

        for t_ch in serialized.channels:
            if t_ch.get("type") != 4:  # category
                continue
            try:
                overwrites = _build_overwrites(t_ch, guild, role_map)
                cat = await guild.create_category(
                    name=t_ch["name"],
                    overwrites=overwrites,
                    reason="Template application",
                )
                category_map[t_ch["id"]] = cat
            except Exception:
                pass

        # Now create text/voice channels
        for t_ch in serialized.channels:
            if t_ch.get("type") == 4:
                continue
            try:
                overwrites = _build_overwrites(t_ch, guild, role_map)
                parent = category_map.get(t_ch.get("parent_id"))
                ch_type = t_ch.get("type", 0)

                if ch_type == 0:  # text
                    await guild.create_text_channel(
                        name=t_ch["name"],
                        topic=t_ch.get("topic") or "",
                        category=parent,
                        overwrites=overwrites,
                        reason="Template application",
                    )
                elif ch_type == 2:  # voice
                    await guild.create_voice_channel(
                        name=t_ch["name"],
                        category=parent,
                        overwrites=overwrites,
                        reason="Template application",
                    )
            except Exception:
                pass

        # Try to send success DM to the user since channels were wiped
        try:
            await interaction.user.send(
                embed=discord.Embed(
                    title="✅ Template Applied!",
                    description=f"The **{template.name}** template has been successfully applied to **{guild.name}**.",
                    color=0x57F287,
                )
            )
        except Exception:
            pass

    except Exception as e:
        try:
            await interaction.user.send(f"❌ Template application failed: {e}")
        except Exception:
            pass


def _build_overwrites(t_ch: dict, guild: discord.Guild, role_map: dict) -> dict:
    overwrites = {}
    for overwrite in t_ch.get("permission_overwrites", []):
        allow = discord.Permissions(overwrite.get("allow", 0))
        deny  = discord.Permissions(overwrite.get("deny", 0))
        ow_id = overwrite.get("id")

        if overwrite.get("type") == 0:  # role
            if ow_id == 0:  # @everyone
                target = guild.default_role
            else:
                target = role_map.get(ow_id)
            if target:
                overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
    return overwrites


# ── Cog ──────────────────────────────────────────────────────────
class TemplateUse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="templateuse",
        description="Apply a server template — replaces all channels and roles"
    )
    async def templateuse(self, interaction: discord.Interaction):
        # Owner only
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                "❌ Only the server owner can apply templates.",
                ephemeral=True
            )

        view = TemplateSelectView()
        await interaction.response.send_message(
            "🗂️ **Select a template to apply to this server:**",
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplateUse(bot))