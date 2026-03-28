"""cogs/templateuse.py — Apply a server template by cloning from a template guild"""

import discord
from discord import app_commands
from discord.ext import commands

# ── Add more templates here as needed ───────────────────────────
TEMPLATES = {
    "TeamCore Default": {
        "guild_id": 1487243792917463072,
        "description": "Standard TeamCore layout with channels, roles & permissions",
        "emoji": "🏈",
    },
}


# ── Confirmation view ────────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, target_guild: discord.Guild, template_guild: discord.Guild):
        super().__init__(timeout=30)
        self.target_guild = target_guild
        self.template_guild = template_guild

    @discord.ui.button(label="Yes, apply template", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="⏳ Applying template... this may take a minute.",
            view=self
        )
        await apply_template(self.template_guild, self.target_guild, interaction)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Template select dropdown ─────────────────────────────────────
class TemplateSelectView(discord.ui.View):
    def __init__(self, target_guild: discord.Guild, bot: commands.Bot):
        super().__init__(timeout=60)
        self.target_guild = target_guild
        self.bot = bot

        options = [
            discord.SelectOption(
                label=name,
                description=data["description"][:100],
                emoji=data["emoji"],
                value=str(data["guild_id"]),
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
        guild_id = int(interaction.data["values"][0])
        template_guild = self.bot.get_guild(guild_id)

        if not template_guild:
            return await interaction.response.edit_message(
                content="❌ Template server not found. Make sure the bot is in the template server.",
                view=None
            )

        template_name = next(
            (name for name, data in TEMPLATES.items() if data["guild_id"] == guild_id),
            "Unknown"
        )

        await interaction.response.edit_message(
            content=(
                f"## ⚠️ Are you sure?\n"
                f"You selected: **{template_name}**\n\n"
                f"This will **permanently delete ALL channels and roles** in **{self.target_guild.name}** "
                f"and replace them with the template layout.\n\n"
                f"**This cannot be undone.**"
            ),
            view=ConfirmView(self.target_guild, template_guild)
        )


# ── Core apply logic ─────────────────────────────────────────────
async def apply_template(
    template_guild: discord.Guild,
    target_guild: discord.Guild,
    interaction: discord.Interaction,
):
    # 1. Delete all channels
    for ch in list(target_guild.channels):
        try:
            await ch.delete(reason="Template application")
        except Exception:
            pass

    # 2. Delete all non-default, non-managed roles
    for role in list(target_guild.roles):
        if role.is_default() or role.managed:
            continue
        try:
            await role.delete(reason="Template application")
        except Exception:
            pass

    # 3. Recreate roles (lowest position first)
    role_map: dict[int, discord.Role] = {}
    for role in sorted(template_guild.roles, key=lambda r: r.position, reverse=True):
        if role.is_default() or role.managed:
            continue
        try:
            new_role = await target_guild.create_role(
                name=role.name,
                permissions=role.permissions,
                color=role.color,
                hoist=role.hoist,
                mentionable=role.mentionable,
                reason="Template application",
            )
            role_map[role.id] = new_role
        except Exception:
            pass

    def build_overwrites(channel) -> dict:
        overwrites = {}
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Role):
                if target.is_default():
                    overwrites[target_guild.default_role] = overwrite
                elif target.id in role_map:
                    overwrites[role_map[target.id]] = overwrite
        return overwrites

    # 4. Recreate categories
    category_map: dict[int, discord.CategoryChannel] = {}
    for cat in sorted(template_guild.categories, key=lambda c: c.position):
        try:
            new_cat = await target_guild.create_category(
                name=cat.name,
                overwrites=build_overwrites(cat),
                position=cat.position,
                reason="Template application",
            )
            category_map[cat.id] = new_cat
        except Exception:
            pass

    # 5. Recreate channels
    for ch in sorted(template_guild.channels, key=lambda c: c.position):
        if isinstance(ch, discord.CategoryChannel):
            continue
        parent = category_map.get(ch.category.id) if ch.category else None
        overwrites = build_overwrites(ch)
        try:
            if isinstance(ch, discord.TextChannel):
                await target_guild.create_text_channel(
                    name=ch.name,
                    topic=ch.topic or "",
                    overwrites=overwrites,
                    category=parent,
                    nsfw=ch.nsfw,
                    slowmode_delay=ch.slowmode_delay,
                    reason="Template application",
                )
            elif isinstance(ch, discord.VoiceChannel):
                await target_guild.create_voice_channel(
                    name=ch.name,
                    overwrites=overwrites,
                    category=parent,
                    bitrate=min(ch.bitrate, 96000),
                    user_limit=ch.user_limit,
                    reason="Template application",
                )
            elif isinstance(ch, discord.StageChannel):
                await target_guild.create_stage_channel(
                    name=ch.name,
                    overwrites=overwrites,
                    category=parent,
                    reason="Template application",
                )
        except Exception:
            pass

    # 6. DM the owner since all channels were wiped
    try:
        embed = discord.Embed(
            title="✅ Template Applied!",
            description=(
                f"The template from **{template_guild.name}** has been successfully "
                f"applied to **{target_guild.name}**."
            ),
            color=0x57F287,
        )
        await interaction.user.send(embed=embed)
    except Exception:
        pass


# ── Cog ──────────────────────────────────────────────────────────
class TemplateUse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="templateuse",
        description="Apply a server template — replaces all channels and roles"
    )
    async def templateuse(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                "❌ Only the server owner can use this command.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "🗂️ **Select a template to apply to this server:**",
            view=TemplateSelectView(interaction.guild, self.bot),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplateUse(bot))