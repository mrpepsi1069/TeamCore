# main.py - TeamCore Discord Bot
import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database

load_dotenv()

# ===================== BOT SETUP =====================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

COGS = [
    "cogs.award",
    "cogs.awardcheck",
    "cogs.bold",
    "cogs.botstats",
    "cogs.flipcoin",
    "cogs.gametime",
    "cogs.help",
    "cogs.lineup",
    "cogs.ping",
    "cogs.ring_add",
    "cogs.setup",
]

# ===================== READY =====================

@bot.event
async def on_ready():
    print(f"🤖 {bot.user} is online")
    print(f"📊 Servers: {len(bot.guilds)}")

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="/help | TeamCore"
    ))

    try:
        await database.initialize()
    except Exception as e:
        print(f"⚠️ DB failed during startup: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

# ===================== GUILD EVENTS =====================

@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"➕ Joined: {guild.name}")
    await database.create_guild(str(guild.id), guild.name)

# ===================== INTERACTION LOGGING =====================

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.application_command:
        cmd_name = interaction.command.name if interaction.command else "unknown"
        await database.log_command(cmd_name, str(interaction.guild_id), str(interaction.user.id))

# ===================== ERROR HANDLING =====================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    print(f"❌ Command error: {error}")
    msg = discord.Embed(title="❌ Error", description="Command failed.", color=0xED4245)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
    except Exception:
        pass

# ===================== STARTUP =====================

async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"✅ Loaded cog: {cog}")
            except Exception as e:
                print(f"❌ Failed to load {cog}: {e}")

        await bot.start(os.environ["DISCORD_TOKEN"])

if __name__ == "__main__":
    asyncio.run(main())
