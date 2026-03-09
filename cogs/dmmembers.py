"""cogs/dmmembers.py"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import success_embed, error_embed
from utils.permissions import has_coach_perms


class DmMembers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="dmmembers", description="DM all members with a role (Coach only)")
    @app_commands.describe(role="Role to DM", message="Message to send (max 1000 chars)")
    async def dmmembers(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        message: app_commands.Range[str, 1, 1000],
    ):
        if not await has_coach_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Coach role or higher."), ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        members = [m for m in role.members if not m.bot]
        if not members:
            return await interaction.followup.send(
                embed=error_embed("No Members", f"No members found with the {role.mention} role!"), ephemeral=True
            )

        ok = fail = 0
        for m in members:
            try:
                embed = discord.Embed(
                    title="Message Received",
                    description=f"🔔 **Message:** {message}",
                    color=0x5865F2,
                )
                embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
                embed.add_field(name="👤 Sent By", value=str(interaction.user), inline=False)
                embed.add_field(name="🏠 Server", value=f"{interaction.guild.name} • #{interaction.channel.name}", inline=False)
                await m.send(embed=embed)
                ok += 1
                await asyncio.sleep(1)
            except Exception:
                fail += 1

        text = f"Successfully sent DM to **{ok}** member(s) with {role.mention}"
        if fail:
            text += f"\n\n⚠️ Failed to DM **{fail}** member(s) (DMs disabled)"
        await interaction.followup.send(embed=success_embed("📨 DMs Sent", text), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DmMembers(bot))
