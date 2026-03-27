"""cogs/dmmembers.py"""

import asyncio
import time
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms

COOLDOWN_SECONDS = 300  # 5 minutes
_cooldowns: dict[int, float] = {}


class RoleSelectView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, message: str):
        super().__init__(timeout=60)
        self.original_interaction = interaction
        self.message = message

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Select roles to DM...",
        min_values=1,
        max_values=10,
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        await interaction.response.defer(ephemeral=True)
        self.stop()

        roles = [r for r in select.values if not r.is_default()]
        if not roles:
            return await interaction.followup.send(
                embed=error_embed("No Roles", "Please select at least one valid role."),
                ephemeral=True
            )

        # Collect unique non-bot members across all selected roles
        seen = set()
        members = []
        for role in roles:
            for m in role.members:
                if not m.bot and m.id not in seen:
                    seen.add(m.id)
                    members.append(m)

        if not members:
            return await interaction.followup.send(
                embed=error_embed("No Members", "No members found in the selected roles."),
                ephemeral=True
            )

        channel = self.original_interaction.channel
        channel_link = f"https://discord.com/channels/{interaction.guild_id}/{channel.id}"
        guild = interaction.guild

        _cooldowns[interaction.guild_id] = time.time()

        ok = fail = 0
        for m in members:
            try:
                embed = discord.Embed(
                    title="Message Received",
                    description=f"🔔 **Message:** {self.message}",
                    color=0x5865F2,
                )
                embed.set_author(
                    name=guild.name,
                    icon_url=guild.icon.url if guild.icon else None
                )
                embed.add_field(name="👤 Sent By", value=self.original_interaction.user.mention, inline=False)
                embed.add_field(
                    name="🏠 Server",
                    value=f"[{guild.name} • #{channel.name}]({channel_link})",
                    inline=False
                )
                await m.send(embed=embed)
                ok += 1
                await asyncio.sleep(1)
            except Exception:
                fail += 1

        role_mentions = " ".join(r.mention for r in roles)
        text = f"Successfully sent DM to **{ok}** member(s) across {role_mentions}"
        if fail:
            text += f"\n\n⚠️ Failed to DM **{fail}** member(s) (DMs disabled)"
        await interaction.followup.send(embed=success_embed("📨 DMs Sent", text), ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content="⏱️ Role selection timed out.",
                view=self
            )
        except Exception:
            pass


class DmMembers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="dmmembers", description="DM all members with selected roles (Coach only)")
    @app_commands.describe(message="Message to send to all members (max 1000 chars)")
    async def dmmembers(
        self,
        interaction: discord.Interaction,
        message: app_commands.Range[str, 1, 1000],
    ):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Coach role or higher."),
                ephemeral=True
            )

        now = time.time()
        last_used = _cooldowns.get(interaction.guild_id, 0)
        remaining = COOLDOWN_SECONDS - (now - last_used)
        if remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            return await interaction.response.send_message(
                embed=error_embed("Cooldown", f"This command is on cooldown. Try again in **{mins}m {secs}s**."),
                ephemeral=True
            )

        view = RoleSelectView(interaction, message)
        await interaction.response.send_message(
            content="📋 **Select the roles you want to DM:**",
            view=view,
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DmMembers(bot))