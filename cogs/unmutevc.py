"""cogs/unmutevc.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed, success_embed
from utils.permissions import has_staff_perms


class UnmuteVC(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="unmutevc", description="Unmute everyone in your current voice channel (Staff only)")
    async def unmutevc(self, interaction: discord.Interaction):
        if not await has_staff_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Staff role or higher."), ephemeral=True
            )

        voice = getattr(interaction.user, "voice", None)
        vc = voice.channel if voice else None
        if not vc:
            return await interaction.response.send_message(
                embed=error_embed("Not in Voice", "You must be in a voice channel."), ephemeral=True
            )

        await interaction.response.defer()

        unmuted = failed = 0
        for m in list(vc.members):
            if m.voice and m.voice.mute:  # VoiceState.mute == server mute; there is no `server_mute` attribute
                try:
                    await m.edit(mute=False, reason=f"/unmutevc by {interaction.user}")
                    unmuted += 1
                except discord.HTTPException:
                    failed += 1

        text = f"Unmuted **{unmuted}** member(s) in {vc.name}"
        if failed:
            text += f"\n⚠️ Couldn't unmute {failed} member(s) — check my **Mute Members** permission."
        await interaction.followup.send(embed=success_embed("Voice Channel Unmuted", text))


async def setup(bot: commands.Bot):
    await bot.add_cog(UnmuteVC(bot))
