"""cogs/leaderboard.py — Team leaderboards (live panels + paginated), plus ring/award leaderboards."""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from database import economy as economy_db
from shared.economy import format_cash
from utils.embeds import error_embed, success_embed
from utils.permissions import has_manager_perms
from utils.records import (
    competition_autocomplete, fmt_pct, fmt_record, list_teams, sort_teams,
)
from utils.ui import brand, send_paged

SORTS = [
    app_commands.Choice(name="Wins", value="wins"),
    app_commands.Choice(name="Winning %", value="percentage"),
    app_commands.Choice(name="Losses (fewest first)", value="losses"),
]
SORT_LABEL = {"wins": "Wins", "percentage": "Winning %", "losses": "Fewest Losses"}
MEDALS = ["🥇", "🥈", "🥉"]


def build_pages(teams: list, sort: str, competition: str | None, per_page: int = 10) -> list:
    ranked = sort_teams(teams, sort)
    pages = []
    for i in range(0, max(len(ranked), 1), per_page):
        chunk = ranked[i:i + per_page]
        lines = [
            f"{MEDALS[i + n] if i + n < 3 else f'`{i + n + 1}.`'} **{t['name']}** — {fmt_record(t)} · {fmt_pct(t)}"
            for n, t in enumerate(chunk)
        ]
        e = brand(f"🏅 Leaderboard — {SORT_LABEL[sort]}" + (f" ({competition})" if competition else ""), "\n".join(lines) or "No teams yet.", 0xFFD700)
        logo = next((t["logo"] for t in chunk if t.get("logo")), None)  # top logo on this page
        if logo:
            e.set_thumbnail(url=logo)
        e.set_footer(text=f"TeamCore • Page {len(pages) + 1}/{max((len(ranked) + per_page - 1) // per_page, 1)}")
        pages.append(e)
    return pages


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="leaderboard", description="Leaderboards", guild_only=True)

    @group.command(name="teams", description="Rank teams by record")
    @app_commands.describe(sort="What to rank by", competition="Only one competition/division")
    @app_commands.choices(sort=SORTS)
    @app_commands.autocomplete(competition=competition_autocomplete)
    async def teams(self, interaction: discord.Interaction, sort: str = "wins", competition: str = None):
        teams = await list_teams(interaction.guild_id, competition)
        if not teams:
            return await interaction.response.send_message(embed=error_embed("No Teams", "No teams registered yet. Use `/record create`."), ephemeral=True)
        await send_paged(interaction, build_pages(teams, sort, competition))

    @group.command(name="post", description="Post a leaderboard that updates automatically (Manager only)")
    @app_commands.choices(sort=SORTS)
    @app_commands.autocomplete(competition=competition_autocomplete)
    async def post(self, interaction: discord.Interaction, sort: str = "wins", competition: str = None, channel: discord.TextChannel = None):
        if not await has_manager_perms(interaction):
            return await interaction.response.send_message(embed=error_embed("Permission Denied", "You need Manager role or higher."), ephemeral=True)
        channel = channel or interaction.channel
        if not channel.permissions_for(interaction.user).send_messages:
            return await interaction.response.send_message(embed=error_embed("Permission Denied", f"You can't post in {channel.mention}."), ephemeral=True)
        teams = await list_teams(interaction.guild_id, competition)
        try:
            msg = await channel.send(embed=build_pages(teams, sort, competition, 15)[0])
        except discord.HTTPException:
            return await interaction.response.send_message(embed=error_embed("Couldn't Post", f"I can't post in {channel.mention}."), ephemeral=True)
        await db.col("leaderboard_panels").insert_one({
            "guild_id": str(interaction.guild_id), "channel_id": str(channel.id), "message_id": str(msg.id),
            "sort": sort, "competition": competition,
        })
        await interaction.response.send_message(embed=success_embed("Live Leaderboard Posted", f"{msg.jump_url}\nIt updates whenever a record changes."), ephemeral=True)

    @group.command(name="rings", description="Most championship rings")
    async def rings(self, interaction: discord.Interaction):
        await self._awards_board(interaction, "rings")

    @group.command(name="awards", description="Most individual awards")
    async def awards(self, interaction: discord.Interaction):
        await self._awards_board(interaction, "awards")

    @group.command(name="cash", description="Richest members across every server the bot is in")
    async def cash(self, interaction: discord.Interaction):
        # Cash is per server, so this is the only board that adds balances up across servers.
        # It scans every account, so acknowledge before the aggregation outlasts Discord's window.
        await interaction.response.defer()
        rows = await economy_db.global_leaderboard(db.handle(), limit=10)
        if not rows:
            return await interaction.followup.send(
                embed=error_embed("No Cash Yet", "Nobody in any server has any Cash yet."), ephemeral=True)
        names = {
            u["user_id"]: u.get("username")
            for u in await db.col("users").find(
                {"user_id": {"$in": [r["user_id"] for r in rows]}}, {"username": 1}
            ).to_list(None)
        }
        lines = []
        for i, row in enumerate(rows):
            who = names.get(row["user_id"]) or f"<@{row['user_id']}>"
            medal = MEDALS[i] if i < 3 else f"`{i + 1}.`"
            servers = f" · {row['servers']} servers" if row["servers"] > 1 else ""
            lines.append(f"{medal} **{who}** — {format_cash(row['total'])}{servers}")
        embed = brand("🌍 Global Cash Leaderboard", "\n".join(lines), 0xFFD700)
        embed.set_footer(text="The richest members across every server TeamCore is in")
        await interaction.edit_original_response(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _awards_board(self, interaction, kind):
        rows = await db.get_leaderboard(str(interaction.guild_id), kind, limit=10)
        if not rows:
            return await interaction.response.send_message(embed=error_embed("Nothing Yet", "No one has any of those yet."), ephemeral=True)
        icon = "💍" if kind == "rings" else "🏆"
        lines = [f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{r['_id']}> — {icon} **{r['count']}**" for i, r in enumerate(rows)]
        await interaction.response.send_message(
            embed=brand(f"🏅 {'Championship Rings' if kind == 'rings' else 'Individual Awards'} Leaderboard", "\n".join(lines), 0xFFD700),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ── live panels ──────────────────────────────────────────
    @commands.Cog.listener()
    async def on_team_record_changed(self, guild_id: str, team_id):
        if not db.is_connected():
            return
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return
        for panel in await db.col("leaderboard_panels").find({"guild_id": guild_id}).to_list(None):
            channel = guild.get_channel(int(panel["channel_id"]))
            try:
                msg = await channel.fetch_message(int(panel["message_id"])) if channel else None
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                continue
            if msg is None:  # panel was deleted — stop tracking it
                await db.col("leaderboard_panels").delete_one({"_id": panel["_id"]})
                continue
            teams = await list_teams(guild_id, panel.get("competition"))
            try:
                await msg.edit(embed=build_pages(teams, panel["sort"], panel.get("competition"), 15)[0])
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
