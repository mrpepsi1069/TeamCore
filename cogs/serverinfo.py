"""cogs/serverinfo.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.ui import brand


class ServerInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="serverinfo", description="Show detailed information about this server")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        await interaction.response.defer()
        if not g.chunked:  # human/bot split needs the full member list
            try:
                await g.chunk()
            except Exception:
                pass
        bots = sum(1 for m in g.members if m.bot)
        total = g.member_count or len(g.members)
        humans = max(total - bots, 0)

        e = brand(f"📋 {g.name}")
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        e.add_field(name="👑 Owner", value=f"<@{g.owner_id}>", inline=True)
        e.add_field(name="🆔 Server ID", value=f"`{g.id}`", inline=True)
        e.add_field(name="📅 Created", value=f"{discord.utils.format_dt(g.created_at, 'D')}\n({discord.utils.format_dt(g.created_at, 'R')})", inline=True)
        e.add_field(name="👥 Members", value=f"**{total:,}** total\n🙋 {humans:,} humans\n🤖 {bots:,} bots", inline=True)
        e.add_field(name="💬 Channels", value=f"📝 {len(g.text_channels)} text\n🔊 {len(g.voice_channels)} voice", inline=True)
        e.add_field(name="🏷️ Roles", value=str(max(len(g.roles) - 1, 0)), inline=True)  # excludes @everyone
        e.add_field(name="🚀 Boosts", value=f"{g.premium_subscription_count} (Level {g.premium_tier})", inline=True)
        e.add_field(name="🔒 Verification", value=str(g.verification_level).replace("_", " ").title(), inline=True)
        await interaction.followup.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerInfo(bot))
