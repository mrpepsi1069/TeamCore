"""cogs/guilds.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import has_owner_perms


class Guilds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="guilds", description="View all servers the bot is in")
    async def guilds(self, interaction: discord.Interaction):
        if not await has_owner_perms(interaction):
            return await interaction.response.send_message(
                content="❌ You do not have permission to use this.", ephemeral=True
            )

        sorted_guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)

        guild_lines = [
            f"`{i+1}.` **{g.name}**\n　ID: `{g.id}`\n　Members: `{g.member_count}`"
            for i, g in enumerate(sorted_guilds)
        ]

        # Split into chunks if too long
        chunks = []
        current = ""
        for line in guild_lines:
            if len(current) + len(line) + 2 > 4000:
                chunks.append(current.strip())
                current = line + "\n\n"
            else:
                current += line + "\n\n"
        if current:
            chunks.append(current.strip())

        await interaction.response.defer(ephemeral=True)

        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📊 Bot Guilds {f'({i+1}/{len(chunks)})' if len(chunks) > 1 else ''}",
                description=chunk,
                color=0x5865F2
            )
            if i == 0:
                embed.set_footer(text=f"Total Servers: {len(self.bot.guilds)} • Sorted by member count")
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Guilds(bot))