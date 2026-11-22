"""utils/channels.py — Resolve where a command should post, honouring /setup."""

import discord
import database as db


async def poll_channel(interaction: discord.Interaction) -> discord.abc.Messageable:
    """The server's configured Gametime Channel if it has one the bot can post in, else the current channel.

    /setup has always offered a "Gametime Channel" picker, but nothing ever read it.
    """
    cfg = await db.get_guild_config(str(interaction.guild_id)) or {}
    cid = cfg.get("gametime_channel")
    if cid:
        channel = interaction.guild.get_channel(int(cid))
        if channel is not None and channel.permissions_for(interaction.guild.me).send_messages:
            return channel
    return interaction.channel
