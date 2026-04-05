"""cogs/setup.py — Full server setup with checklist embed and role hierarchy"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db

# Role hierarchy levels (higher = more permissions)
ROLE_HIERARCHY = [
    "assistant_coach_role",
    "head_coach_role",
    "general_manager_role",
    "franchise_owner_role",
    "admin_role",
]

ROLE_LABELS = {
    "assistant_coach_role":  "Assistant Coach",
    "head_coach_role":       "Head Coach",
    "general_manager_role":  "General Manager",
    "franchise_owner_role":  "Franchise Owner",
    "admin_role":            "Admin",
}

CHANNEL_KEYS = {
    "log_channel":        "Log Channel",
    "announce_channel":   "Announcements Channel",
    "gametime_channel":   "Gametime Channel",
}


def check(val) -> str:
    return "✅" if val else "❌"


async def build_status_embed(guild_id: str) -> discord.Embed:
    cfg = await db.get_guild_config(guild_id) or {}

    embed = discord.Embed(
        title="⚙️ TeamCore Setup",
        description="Configure your server below. Use the buttons to set each item.",
        color=0x5865F2,
    )

    # Roles section
    role_lines = ""
    for key in ROLE_HIERARCHY:
        rid = cfg.get(key)
        role_lines += f"{check(rid)} **{ROLE_LABELS[key]}** — {f'<@&{rid}>' if rid else 'Not set'}\n"
    embed.add_field(name="👥 Role Hierarchy", value=role_lines, inline=False)

    # Channels section
    ch_lines = ""
    for key, label in CHANNEL_KEYS.items():
        cid = cfg.get(key)
        ch_lines += f"{check(cid)} **{label}** — {f'<#{cid}>' if cid else 'Not set'}\n"
    embed.add_field(name="📢 Channels", value=ch_lines, inline=False)

    # Progress bar
    total = len(ROLE_HIERARCHY) + len(CHANNEL_KEYS)
    done = sum(1 for k in list(ROLE_HIERARCHY) + list(CHANNEL_KEYS) if cfg.get(k))
    bar_filled = int((done / total) * 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    embed.add_field(
        name="📊 Progress",
        value=f"`{bar}` {done}/{total} complete",
        inline=False,
    )

    embed.set_footer(text="Only the server owner can use setup")
    return embed


class RoleSetModal(discord.ui.Modal):
    def __init__(self, key: str, label: str, view_ref):
        super().__init__(title=f"Set {label}")
        self.key = key
        self.view_ref = view_ref
        self.role_input = discord.ui.TextInput(
            label=f"{label} Role ID or @mention",
            placeholder="Paste the role ID (e.g. 123456789012345678)",
            max_length=30,
        )
        self.add_item(self.role_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.role_input.value.strip().strip("<@&>")
        try:
            role_id = int(raw)
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message(
                    "❌ Role not found. Make sure you paste a valid role ID.", ephemeral=True
                )
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid input. Please paste a role ID.", ephemeral=True
            )

        await db.set_guild_config(str(interaction.guild_id), {self.key: role_id})
        embed = await build_status_embed(str(interaction.guild_id))
        await interaction.response.edit_message(embed=embed, view=self.view_ref)


class ChannelSetModal(discord.ui.Modal):
    def __init__(self, key: str, label: str, view_ref):
        super().__init__(title=f"Set {label}")
        self.key = key
        self.view_ref = view_ref
        self.ch_input = discord.ui.TextInput(
            label=f"{label} Channel ID or #mention",
            placeholder="Paste the channel ID (e.g. 123456789012345678)",
            max_length=30,
        )
        self.add_item(self.ch_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.ch_input.value.strip().strip("<#>")
        try:
            ch_id = int(raw)
            ch = interaction.guild.get_channel(ch_id)
            if not ch:
                return await interaction.response.send_message(
                    "❌ Channel not found. Make sure you paste a valid channel ID.", ephemeral=True
                )
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid input. Please paste a channel ID.", ephemeral=True
            )

        await db.set_guild_config(str(interaction.guild_id), {self.key: ch_id})
        embed = await build_status_embed(str(interaction.guild_id))
        await interaction.response.edit_message(embed=embed, view=self.view_ref)


class SetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    # ── Role buttons ────────────────────────────────────────────

    @discord.ui.button(label="Assistant Coach", style=discord.ButtonStyle.secondary, emoji="🏈", row=0)
    async def set_ac(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RoleSetModal("assistant_coach_role", "Assistant Coach", self)
        )

    @discord.ui.button(label="Head Coach", style=discord.ButtonStyle.secondary, emoji="🏈", row=0)
    async def set_hc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RoleSetModal("head_coach_role", "Head Coach", self)
        )

    @discord.ui.button(label="General Manager", style=discord.ButtonStyle.secondary, emoji="💼", row=0)
    async def set_gm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RoleSetModal("general_manager_role", "General Manager", self)
        )

    @discord.ui.button(label="Franchise Owner", style=discord.ButtonStyle.secondary, emoji="👑", row=1)
    async def set_fo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RoleSetModal("franchise_owner_role", "Franchise Owner", self)
        )

    @discord.ui.button(label="Admin", style=discord.ButtonStyle.secondary, emoji="🛡️", row=1)
    async def set_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RoleSetModal("admin_role", "Admin", self)
        )

    # ── Channel buttons ──────────────────────────────────────────

    @discord.ui.button(label="Log Channel", style=discord.ButtonStyle.primary, emoji="📋", row=2)
    async def set_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ChannelSetModal("log_channel", "Log Channel", self)
        )

    @discord.ui.button(label="Announcements", style=discord.ButtonStyle.primary, emoji="📢", row=2)
    async def set_announce(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ChannelSetModal("announce_channel", "Announcements Channel", self)
        )

    @discord.ui.button(label="Gametime Channel", style=discord.ButtonStyle.primary, emoji="⏰", row=2)
    async def set_gametime(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ChannelSetModal("gametime_channel", "Gametime Channel", self)
        )

    # ── Reset ────────────────────────────────────────────────────

    @discord.ui.button(label="Reset All", style=discord.ButtonStyle.danger, emoji="🗑️", row=3)
    async def reset_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.set_guild_config(str(interaction.guild_id), {
            key: None for key in list(ROLE_HIERARCHY) + list(CHANNEL_KEYS)
        })
        embed = await build_status_embed(str(interaction.guild_id))
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Configure TeamCore for this server")
    async def setup(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                "❌ Only the server owner can run setup.", ephemeral=True
            )

        embed = await build_status_embed(str(interaction.guild_id))
        await interaction.response.send_message(embed=embed, view=SetupView(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))