"""cogs/templateuse.py — Clone a server layout from a source server"""

import discord
from discord import app_commands
from discord.ext import commands

SOURCE_SERVER_ID = 1487243792917463072  # Template source server

# ── Confirmation view ───────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, target_guild: discord.Guild, source_guild: discord.Guild):
        super().__init__(timeout=30)
        self.target_guild = target_guild
        self.source_guild = source_guild

    @discord.ui.button(label="Yes, clone server", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="⏳ Cloning server layout... this may take a while.",
            view=self
        )
        await clone_server_layout(self.source_guild, self.target_guild, interaction)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)

# ── Clone logic ─────────────────────────────────────────────────
async def clone_server_layout(source: discord.Guild, target: discord.Guild, interaction: discord.Interaction):
    try:
        # ── Delete all channels in target ──
        for channel in target.channels:
            try:
                await channel.delete(reason="Server clone")
            except Exception as e:
                print(f"Failed to delete channel {channel.name}: {e}")

        # ── Delete all roles in target except @everyone/managed ──
        for role in target.roles:
            if role.is_default() or role.managed:
                continue
            try:
                await role.delete(reason="Server clone")
            except Exception as e:
                print(f"Failed to delete role {role.name}: {e}")

        # ── Clone roles ──
        role_map = {}
        for role in sorted(source.roles, key=lambda r: r.position):
            if role.is_default() or role.managed:
                continue
            try:
                new_role = await target.create_role(
                    name=role.name,
                    permissions=role.permissions,
                    color=role.color,
                    hoist=role.hoist,
                    mentionable=role.mentionable,
                    reason="Server clone"
                )
                role_map[role.id] = new_role
            except Exception as e:
                print(f"Failed to clone role {role.name}: {e}")

        # ── Clone categories first ──
        category_map = {}
        for cat in source.categories:
            try:
                overwrites = {}
                for target_role in cat.overwrites:
                    if isinstance(target_role, discord.Role) and target_role.id in role_map:
                        overwrites[role_map[target_role.id]] = cat.overwrites[target_role]
                    elif isinstance(target_role, discord.Role) and target_role.is_default():
                        overwrites[target.roles[0]] = cat.overwrites[target_role]
                new_cat = await target.create_category(
                    name=cat.name,
                    overwrites=overwrites,
                    position=cat.position,
                    reason="Server clone"
                )
                category_map[cat.id] = new_cat
            except Exception as e:
                print(f"Failed to clone category {cat.name}: {e}")

        # ── Clone channels ──
        for ch in source.channels:
            if isinstance(ch, discord.CategoryChannel):
                continue
            parent = category_map.get(ch.category.id) if ch.category else None
            overwrites = {}
            for target_role in ch.overwrites:
                if isinstance(target_role, discord.Role):
                    if target_role.id in role_map:
                        overwrites[role_map[target_role.id]] = ch.overwrites[target_role]
                    elif target_role.is_default():
                        overwrites[target.roles[0]] = ch.overwrites[target_role]
            try:
                if isinstance(ch, discord.TextChannel):
                    await target.create_text_channel(
                        name=ch.name,
                        topic=ch.topic,
                        overwrites=overwrites,
                        category=parent,
                        nsfw=ch.nsfw,
                        position=ch.position,
                        reason="Server clone"
                    )
                elif isinstance(ch, discord.VoiceChannel):
                    await target.create_voice_channel(
                        name=ch.name,
                        overwrites=overwrites,
                        category=parent,
                        position=ch.position,
                        bitrate=ch.bitrate,
                        user_limit=ch.user_limit,
                        reason="Server clone"
                    )
                elif isinstance(ch, discord.StageChannel):
                    await target.create_stage_channel(
                        name=ch.name,
                        overwrites=overwrites,
                        category=parent,
                        position=ch.position,
                        reason="Server clone"
                    )
            except Exception as e:
                print(f"Failed to clone channel {ch.name}: {e}")

        # Notify the user
        try:
            await interaction.user.send(f"✅ Server layout cloned successfully from **{source.name}** to **{target.name}**!")
        except:
            pass

    except Exception as e:
        try:
            await interaction.user.send(f"❌ Server clone failed: {e}")
        except:
            pass

# ── Cog ─────────────────────────────────────────────────────────
class TemplateUse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="templateuse",
        description="Clone a server layout from the template server (DELETES EVERYTHING)"
    )
    async def templateuse(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                "❌ Only the server owner can use this.",
                ephemeral=True
            )

        source_guild = self.bot.get_guild(SOURCE_SERVER_ID)
        if not source_guild:
            return await interaction.response.send_message(
                "❌ Template source server not found or bot is not in it.",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"⚠️ This will delete **all channels and roles** in **{interaction.guild.name}** and clone **{source_guild.name}** layout. Continue?",
            view=ConfirmView(interaction.guild, source_guild),
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(TemplateUse(bot))