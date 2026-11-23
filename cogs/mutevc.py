"""cogs/mutevc.py"""

import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import error_embed, success_embed
from utils.permissions import has_staff_perms, member_is_staff


class MuteVC(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="mutevc", description="Mute everyone in your current voice channel (Staff only)")
    async def mutevc(self, interaction: discord.Interaction):
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

        # Muting a big channel takes several API calls; defer so the interaction doesn't expire.
        await interaction.response.defer()

        muted = skipped = failed = 0
        for m in list(vc.members):
            if m.bot or m.id == interaction.user.id or await member_is_staff(interaction.guild, m):
                skipped += 1  # the old code only ever skipped the bot and the caller, never other staff
                continue
            if m.voice and m.voice.mute:
                continue  # already server-muted
            try:
                await m.edit(mute=True, reason=f"/mutevc by {interaction.user}")
                muted += 1
            except discord.HTTPException:
                failed += 1

        text = f"Muted **{muted}** member(s) in {vc.name}"
        if skipped:
            text += f"\n*Skipped {skipped} staff/bot(s)*"
        if failed:
            text += f"\n⚠️ Couldn't mute {failed} member(s) — check my **Mute Members** permission."
        await interaction.followup.send(embed=success_embed("Voice Channel Muted", text))


async def setup(bot: commands.Bot):
    await bot.add_cog(MuteVC(bot))
