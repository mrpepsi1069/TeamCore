"""cogs/activitycheck.py — Reaction-based activity checks that report their results when they expire."""

from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
from utils.embeds import success_embed, error_embed
from utils.permissions import has_manager_perms
import database as db

CHECK_EMOJI = "✅"


def _mention_list(user_ids: list[int], limit: int = 1000) -> str:
    """Join mentions, truncating so the text always fits in an embed field (1024 chars)."""
    if not user_ids:
        return "*Nobody*"
    out, used = [], 0
    for i, uid in enumerate(user_ids):
        piece = f"<@{uid}>"
        if used + len(piece) + 1 > limit:
            out.append(f"…and {len(user_ids) - i} more")
            break
        out.append(piece)
        used += len(piece) + 1
    return " ".join(out)


class ActivityCheck(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.close_due_checks.start()

    def cog_unload(self):
        self.close_due_checks.cancel()

    @app_commands.command(name="activitycheck", description="Create an activity check (Manager only)")
    @app_commands.describe(duration="Duration in hours (1-168)", role="Role to check (optional)")
    async def activitycheck(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Range[int, 1, 168],
        role: discord.Role = None,
    ):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(
                embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True
            )

        expires_at = datetime.now(timezone.utc) + timedelta(hours=duration)
        ts = int(expires_at.timestamp())

        embed = discord.Embed(
            title="✅ Activity Check",
            description=(
                f"{role.mention if role else '@everyone'}\n\n"
                f"**React with {CHECK_EMOJI} to confirm you're active!**\n\n"
                f"Expires: <t:{ts}:R>"
            ),
            color=0x57F287,
        )

        try:
            msg = await interaction.channel.send(
                content=role.mention if role else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            await msg.add_reaction(CHECK_EMOJI)
        except discord.HTTPException:
            return await interaction.response.send_message(
                embed=error_embed("Couldn't Post", "I need Send Messages, Embed Links and Add Reactions in this channel."),
                ephemeral=True,
            )

        await db.create_activity_check(
            str(interaction.guild_id), str(msg.id), str(interaction.channel_id),
            expires_at, str(interaction.user.id), str(role.id) if role else None,
        )

        await interaction.response.send_message(
            embed=success_embed(
                "Activity Check Created",
                f"Expires <t:{ts}:R>. I'll post the results here when it ends.",
            ),
            ephemeral=True,
        )

    # ── Results ──────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def close_due_checks(self):
        """The old command stored an expiry time that nothing ever looked at, so the
        check just sat there forever with no results."""
        for chk in await db.get_due_activity_checks(datetime.now(timezone.utc)):
            try:
                await self._finish(chk)
            except Exception as e:
                print(f"⚠️ Activity check {chk.get('message_id')} could not be finished: {e}")
            finally:
                # Always close it, otherwise a deleted channel/message would be retried every minute.
                await db.close_activity_check(chk["_id"])

    @close_due_checks.before_loop
    async def _before_close(self):
        await self.bot.wait_until_ready()

    async def _finish(self, chk: dict) -> None:
        guild = self.bot.get_guild(int(chk["guild_id"]))
        if guild is None:
            return
        channel = guild.get_channel(int(chk["channel_id"]))
        if channel is None:
            return
        try:
            message = await channel.fetch_message(int(chk["message_id"]))
        except discord.NotFound:
            return

        reactor_ids: list[int] = []
        reaction = discord.utils.get(message.reactions, emoji=CHECK_EMOJI)
        if reaction:
            reactor_ids = [u.id async for u in reaction.users() if not u.bot]

        embed = discord.Embed(title="✅ Activity Check Results", color=0x5865F2)
        role = guild.get_role(int(chk["role_id"])) if chk.get("role_id") else None
        if role:
            if not guild.chunked:
                await guild.chunk()
            members = [m for m in role.members if not m.bot]
            active = [m.id for m in members if m.id in set(reactor_ids)]
            inactive = [m.id for m in members if m.id not in set(reactor_ids)]
            embed.description = f"Results for {role.mention}"
            embed.add_field(name=f"✅ Active ({len(active)})", value=_mention_list(active), inline=False)
            embed.add_field(name=f"❌ Didn't respond ({len(inactive)})", value=_mention_list(inactive), inline=False)
        else:
            embed.description = "Results for everyone"
            embed.add_field(name=f"✅ Active ({len(reactor_ids)})", value=_mention_list(reactor_ids), inline=False)

        # Mark the original message closed, then post results as a reply to it.
        try:
            closed = discord.Embed.from_dict(message.embeds[0].to_dict()) if message.embeds else discord.Embed(title="Activity Check")
            closed.description = "🔒 **This activity check has ended.**"
            closed.color = 0x95A5A6
            await message.edit(embed=closed)
        except discord.HTTPException:
            pass

        await message.reply(
            content=f"<@{chk['created_by']}>",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityCheck(bot))
