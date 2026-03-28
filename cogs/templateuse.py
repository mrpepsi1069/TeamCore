"""cogs/templateuse.py — Apply a Discord server template"""

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# ── Templates registry ──────────────────────────────────────────
TEMPLATES = {
    "TeamCore Default": {
        "code": "8e3TuU57zRaE",
        "description": "Standard TeamCore layout with channels, roles & permissions",
        "emoji": "🏈",
    },
}

# ── Fetch template via Discord API ───────────────────────────────
async def fetch_template(bot, code: str):
    url = f"https://discord.com/api/v10/guilds/templates/{code}"
    headers = {"Authorization": f"Bot {bot.http.token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch template: {resp.status}")
            return await resp.json()

# ── Build permission overwrites ─────────────────────────────────
def build_overwrites(ch, guild, role_map):
    overwrites = {}
    for ow in ch.get("permission_overwrites", []):
        allow = discord.Permissions(ow["allow"])
        deny = discord.Permissions(ow["deny"])
        target = None
        if ow["type"] == 0:  # role
            if ow["id"] == 0:
                target = guild.default_role
            else:
                target = role_map.get(ow["id"])
        elif ow["type"] == 1:  # member
            target = guild.get_member(int(ow["id"]))
        if target:
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
    return overwrites

# ── Confirmation view ───────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, template_name: str, template_code: str):
        super().__init__(timeout=30)
        self.template_name = template_name
        self.template_code = template_code

    @discord.ui.button(label="Yes, apply template", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="⏳ Applying template... this may take a moment.",
            view=self
        )
        await apply_template(interaction, self.template_code)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)

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
        select = discord.ui.Select(
            placeholder="Choose a template...",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        chosen = interaction.data["values"][0]
        template = TEMPLATES[chosen]
        confirm_view = ConfirmView(chosen, template["code"])
        await interaction.response.edit_message(
            content=(
                f"## {template['emoji']} {chosen}\n"
                f"{template['description']}\n\n"
                f"⚠️ **WARNING:** This will delete ALL channels and roles.\n"
                f"**This cannot be undone. Continue?**"
            ),
            view=confirm_view,
        )

# ── Core apply logic ─────────────────────────────────────────────
async def apply_template(interaction: discord.Interaction, template_code: str):
    guild = interaction.guild
    bot = interaction.client

    try:
        data = await fetch_template(bot, template_code)
        serialized = data["serialized_source_guild"]

        # ── Delete all channels ──
        for channel in guild.channels:
            try:
                await channel.delete(reason="Template application")
            except Exception as e:
                print(f"Failed to delete channel {channel.name}: {e}")

        # ── Delete all roles except @everyone/managed ──
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            try:
                await role.delete(reason="Template application")
            except Exception as e:
                print(f"Failed to delete role {role.name}: {e}")

        # ── Create roles ──
        role_map = {}
        for role in sorted(serialized["roles"], key=lambda r: r.get("position", 0)):
            try:
                new_role = await guild.create_role(
                    name=role["name"],
                    permissions=discord.Permissions(role["permissions"]),
                    color=discord.Color(role["color"]),
                    hoist=role.get("hoist", False),
                    mentionable=role.get("mentionable", False),
                    reason="Template application",
                )
                role_map[role["id"]] = new_role
            except Exception as e:
                print(f"Failed to create role {role['name']}: {e}")

        # ── Create categories first ──
        category_map = {}
        for ch in serialized["channels"]:
            if ch["type"] != 4:
                continue
            try:
                overwrites = build_overwrites(ch, guild, role_map)
                cat = await guild.create_category(
                    name=ch["name"],
                    overwrites=overwrites,
                    reason="Template application",
                )
                category_map[ch["id"]] = cat
            except Exception as e:
                print(f"Failed to create category {ch['name']}: {e}")

        # ── Create all other channels ──
        for ch in serialized["channels"]:
            if ch["type"] == 4:
                continue
            try:
                overwrites = build_overwrites(ch, guild, role_map)
                parent = category_map.get(ch.get("parent_id"))
                if ch["type"] == 0:  # text
                    await guild.create_text_channel(
                        name=ch["name"],
                        topic=ch.get("topic", ""),
                        category=parent,
                        overwrites=overwrites,
                        reason="Template application",
                    )
                elif ch["type"] == 2:  # voice
                    await guild.create_voice_channel(
                        name=ch["name"],
                        category=parent,
                        overwrites=overwrites,
                        reason="Template application",
                    )
                elif ch["type"] == 5:  # announcement/news
                    await guild.create_text_channel(
                        name=ch["name"],
                        topic=ch.get("topic", ""),
                        category=parent,
                        overwrites=overwrites,
                        news=True,
                        reason="Template application",
                    )
                elif ch["type"] == 13:  # stage
                    await guild.create_stage_channel(
                        name=ch["name"],
                        category=parent,
                        overwrites=overwrites,
                        reason="Template application",
                    )
            except Exception as e:
                print(f"Failed to create channel {ch['name']}: {e}")

        # ── Notify user ──
        try:
            await interaction.user.send(
                f"✅ Template **{data['name']}** applied successfully to **{guild.name}**!"
            )
        except:
            pass

    except Exception as e:
        try:
            await interaction.user.send(f"❌ Template failed: {e}")
        except:
            pass

# ── Cog ─────────────────────────────────────────────────────────
class TemplateUse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="templateuse",
        description="Apply a server template (DELETES EVERYTHING)"
    )
    async def templateuse(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                "❌ Only the server owner can use this.",
                ephemeral=True
            )
        await interaction.response.send_message(
            "🗂️ Select a template:",
            view=TemplateSelectView(),
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(TemplateUse(bot))