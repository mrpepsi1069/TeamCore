"""cogs/premium.py"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from config import SUPPORT_SERVER_URL as DISCORD_LINK
from utils.permissions import has_owner_perms
from utils.premium import get_premium_status
import database as db


class Premium(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="premium", description="View premium plans and pricing")
    async def premium(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💎 TeamCore Custom Premium",
            description="Unlock exclusive features for your team!",
            color=0xFFD700,
        )
        embed.add_field(
            name="💰 Pricing Plans",
            value=(
                "**Monthly** — $4/month\n"
                "**Lifetime** — $12 (Best Value 🔥)\n"
                "**Custom Commands** — $1-3"
            ),
            inline=False,
        )
        embed.add_field(
            name="✨ Premium Features",
            value=(
                "• Auto-DM game times to team members\n"
                "• Custom commands for your bot\n"
                "• Customizable bot name and avatar\n"
                "• DM all members with announcements\n"
                "• Priority support\n"
                "• Early access to new features\n"
                "• Advanced data backup\n"
                "• Detailed usage statistics\n"
                "• More future features in the works"
            ),
            inline=False,
        )
        embed.add_field(
            name="💳 Payment Methods",
            value="• CashApp\n• PayPal\n• Venmo",
            inline=True,
        )
        embed.add_field(
            name="🛒 How to Purchase",
            value="Click **Purchase Premium** below to join our Discord and open a ticket!",
            inline=False,
        )
        embed.set_footer(text="Made by Ghostie | Premium Support Available 24/7")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Purchase Premium", style=discord.ButtonStyle.link, url=DISCORD_LINK, emoji="💎"))
        view.add_item(discord.ui.Button(label="View Demo", style=discord.ButtonStyle.link, url=DISCORD_LINK, emoji="🎬"))

        # Show this server's current status (a premium server used to see the sales pitch only)
        if interaction.guild_id and db.is_connected():
            status = await get_premium_status(str(interaction.guild_id))
            if status["is_premium"]:
                if status["expires_at"]:
                    value = f"✅ Active — expires {discord.utils.format_dt(status['expires_at'], 'R')}"
                else:
                    value = "✅ Active — lifetime"
            else:
                value = "❌ This server doesn't have Premium"
            embed.insert_field_at(0, name="📌 This Server", value=value, inline=False)

        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="setpremium", description="[Owner] Grant or revoke Premium for a server")
    @app_commands.describe(
        server_id="Server ID",
        days="Days of premium (0 = lifetime)",
        revoke="Remove premium instead",
    )
    async def setpremium(self, interaction: discord.Interaction, server_id: str, days: app_commands.Range[int, 0, 3650] = 0, revoke: bool = False):
        # Premium could previously only be granted by editing the database by hand.
        if not await has_owner_perms(interaction):
            return await interaction.response.send_message("❌ You do not have permission to use this.", ephemeral=True)
        if not server_id.isdigit():
            return await interaction.response.send_message("❌ Invalid server ID.", ephemeral=True)
        if not db.is_connected():
            return await interaction.response.send_message("❌ Database unavailable.", ephemeral=True)

        guild = self.bot.get_guild(int(server_id))
        await db.create_guild(server_id, guild.name if guild else "Unknown")  # make sure a doc exists
        if revoke:
            await db.set_premium(server_id, False, None)
            return await interaction.response.send_message(f"✅ Premium removed for `{server_id}`.", ephemeral=True)
        expires = datetime.now(timezone.utc) + timedelta(days=days) if days else None
        await db.set_premium(server_id, True, expires)
        await interaction.response.send_message(
            f"✅ Premium granted to `{server_id}` — " + (f"{days} day(s)" if days else "lifetime"), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Premium(bot))
