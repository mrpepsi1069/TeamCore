"""cogs/stats.py — Auto-updating statistic voice channels (members, boosts, team records, custom)."""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import database as db
from utils.embeds import error_embed, success_embed
from utils.permissions import is_server_admin
from utils.records import fmt_pct, fmt_record, get_team, team_autocomplete
from utils.ui import brand

DEFAULT_TEMPLATES = {
    "members": "👥 Members: {value}",
    "humans": "🙋 Humans: {value}",
    "bots": "🤖 Bots: {value}",
    "boosts": "🚀 Boosts: {value}",
    "team": "🏆 {name}: {record}",
    "record": "🏆 Overall: {record} · {pct}",
    "rings": "💍 Rings: {value}",
    "custom": "{name}: {value}",
}
STAT_CHOICES = [
    app_commands.Choice(name="Total members", value="members"),
    app_commands.Choice(name="Humans", value="humans"),
    app_commands.Choice(name="Bots", value="bots"),
    app_commands.Choice(name="Server boosts", value="boosts"),
    app_commands.Choice(name="Team record", value="team"),
    app_commands.Choice(name="Overall team record (all leagues)", value="record"),
    app_commands.Choice(name="Championship rings", value="rings"),
    app_commands.Choice(name="Custom", value="custom"),
]
CATEGORY_NAME = "📊 Server Stats"


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_template(template: str, values: dict) -> str:
    """Fill {placeholders}; unknown ones are left as-is instead of raising. Max 100 chars (Discord limit)."""
    try:
        return template.format_map(_Safe(values))[:100] or "stat"
    except (ValueError, IndexError):
        return template[:100] or "stat"


def make_key(stat: str, team_id=None, custom_name: str = None) -> str:
    if stat == "team":
        return f"team:{team_id}"
    if stat == "custom":
        return f"custom:{(custom_name or '').strip().lower()}"
    return stat


async def compute_values(guild: discord.Guild, cfg: dict) -> dict | None:
    t = cfg["type"]
    if t in ("members", "humans", "bots"):
        bots = sum(1 for m in guild.members if m.bot)
        total = guild.member_count or len(guild.members)
        return {"value": f"{ {'members': total, 'humans': max(total - bots, 0), 'bots': bots}[t]:,}"}
    if t == "boosts":
        return {"value": str(guild.premium_subscription_count), "tier": str(guild.premium_tier)}
    if t == "custom":
        return {"name": cfg.get("label", ""), "value": cfg.get("value", "")}
    if t == "team":
        team = await db.col("teams").find_one({"guild_id": str(guild.id), "team_id": cfg["team_id"]})
        if not team:
            return None
        return {"name": team["name"], "wins": team["wins"], "losses": team["losses"], "ties": team["ties"],
                "record": fmt_record(team), "pct": fmt_pct(team), "value": fmt_record(team)}
    if t == "record":  # every team's record added up, across all leagues
        teams = await db.col("teams").find({"guild_id": str(guild.id)}).to_list(None)
        total = {k: sum(int(x.get(k) or 0) for x in teams) for k in ("wins", "losses", "ties")}
        return {"name": "Overall", **total, "record": fmt_record(total), "pct": fmt_pct(total), "teams": str(len(teams)), "value": fmt_record(total)}
    if t == "rings":
        q = {"guild_id": str(guild.id)}
        count = await db.col("championship_rings").count_documents(q)
        return {"value": str(count), "rings": str(count), "leagues": str(len(await db.col("championship_rings").distinct("league_id", q)))}
    return None


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}
        self.refresh_loop.start()

    def cog_unload(self):
        self.refresh_loop.cancel()

    group = app_commands.Group(name="stats", description="Auto-updating statistics voice channels", guild_only=True)

    # ── channel plumbing ─────────────────────────────────────
    async def _category(self, guild: discord.Guild):
        cfg = await db.get_guild_config(str(guild.id)) or {}
        cat = guild.get_channel(int(cfg["stats_category_id"])) if cfg.get("stats_category_id") else None
        if isinstance(cat, discord.CategoryChannel):
            return cat
        cat = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if cat is None:
            cat = await guild.create_category(
                CATEGORY_NAME, overwrites={guild.default_role: discord.PermissionOverwrite(connect=False)}, reason="TeamCore stats"
            )
        await db.set_guild_config(str(guild.id), {"stats_category_id": cat.id})
        return cat

    async def _ensure_channel(self, guild: discord.Guild, cfg: dict, name: str):
        """Return the stat's channel, creating it only if the stored one is gone (no duplicates)."""
        ch = guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
        if isinstance(ch, discord.VoiceChannel):
            return ch
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),  # visible but not joinable
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
        }
        ch = await guild.create_voice_channel(name, category=await self._category(guild), overwrites=overwrites, reason="TeamCore stats channel")
        await db.col("stats_channels").update_one({"_id": cfg["_id"]}, {"$set": {"channel_id": ch.id, "last_name": name}})
        return ch

    async def update_one(self, guild: discord.Guild, cfg: dict) -> bool:
        values = await compute_values(guild, cfg)
        if values is None:  # e.g. the team was deleted
            ch = guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
            if ch:
                try:
                    await ch.delete(reason="TeamCore stat no longer valid")
                except discord.HTTPException:
                    pass
            await db.col("stats_channels").delete_one({"_id": cfg["_id"]})
            return False
        name = render_template(cfg.get("template") or DEFAULT_TEMPLATES[cfg["type"]], values)
        ch = await self._ensure_channel(guild, cfg, name)
        if ch.name != name:  # renames are rate limited (2/10min) — only touch the API when something changed
            try:
                await ch.edit(name=name, reason="TeamCore stats update")
                await db.col("stats_channels").update_one({"_id": cfg["_id"]}, {"$set": {"last_name": name}})
            except discord.HTTPException:
                return False
        return True

    async def update_guild(self, guild: discord.Guild, only_team_id=None):
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            query = {"guild_id": str(guild.id)}
            if only_team_id is not None:
                query["key"] = {"$in": [f"team:{only_team_id}", "record"]}  # the overall record moves too
            for cfg in await db.col("stats_channels").find(query).to_list(None):
                try:
                    await self.update_one(guild, cfg)
                except discord.Forbidden:
                    print(f"⚠️ stats: missing permissions in {guild.name}")
                    break
                except Exception as e:
                    print(f"⚠️ stats update failed ({guild.id}, {cfg.get('key')}): {e}")

    # ── background updates ───────────────────────────────────
    @tasks.loop(minutes=10)
    async def refresh_loop(self):
        if not db.is_connected():
            return
        for gid in await db.col("stats_channels").distinct("guild_id"):
            guild = self.bot.get_guild(int(gid))
            if guild:
                await self.update_guild(guild)

    @refresh_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_team_record_changed(self, guild_id: str, team_id):
        guild = self.bot.get_guild(int(guild_id))
        if guild and db.is_connected():
            await self.update_guild(guild, only_team_id=team_id)

    @commands.Cog.listener()
    async def on_teamcore_stats_changed(self, guild_id: str):
        """Something outside the record changed a number (e.g. a championship ring was awarded)."""
        guild = self.bot.get_guild(int(guild_id))
        if guild and db.is_connected():
            await self.update_guild(guild)

    # ── commands ─────────────────────────────────────────────
    async def _admin(self, interaction):
        if await is_server_admin(interaction):
            return True
        await interaction.response.send_message(embed=error_embed("Permission Denied", "Only server admins can manage stats channels."), ephemeral=True)
        return False

    @group.command(name="setup", description="Create the default stats channels (members + boosts)")
    async def setup_(self, interaction: discord.Interaction):
        if not await self._admin(interaction):
            return
        if not interaction.guild.me.guild_permissions.manage_channels:
            return await interaction.response.send_message(embed=error_embed("Missing Permission", "I need **Manage Channels**."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        made = [await self._add(interaction.guild, "members"), await self._add(interaction.guild, "boosts")]
        await interaction.followup.send(embed=success_embed("Stats Ready", "\n".join(m for m in made if m) or "Already set up."), ephemeral=True)

    async def _add(self, guild, stat, team=None, custom_name=None, custom_value=None, template=None):
        """Returns a status line, or None if it already existed."""
        gid = str(guild.id)
        key = make_key(stat, team["team_id"] if team else None, custom_name)
        if await db.col("stats_channels").find_one({"guild_id": gid, "key": key}):
            return None
        cfg = {"guild_id": gid, "key": key, "type": stat, "channel_id": None, "template": template or None}
        if team:
            cfg["team_id"] = team["team_id"]
        if stat == "custom":
            cfg.update(label=custom_name.strip(), value=custom_value or "")
        r = await db.col("stats_channels").insert_one(cfg)
        cfg["_id"] = r.inserted_id
        await self.update_one(guild, cfg)
        return f"✅ Created **{key}**"

    @group.command(name="add", description="Add a stats channel")
    @app_commands.describe(stat="What to display", team="Team (for Team record)", custom_name="Name (for Custom)",
                           custom_value="Value (for Custom)", template="Channel name, e.g. `Members: {value}`")
    @app_commands.choices(stat=STAT_CHOICES)
    @app_commands.autocomplete(team=team_autocomplete)
    async def add(self, interaction: discord.Interaction, stat: str, team: str = None, custom_name: app_commands.Range[str, 1, 40] = None,
                  custom_value: app_commands.Range[str, 0, 60] = None, template: app_commands.Range[str, 1, 100] = None):
        if not await self._admin(interaction):
            return
        t = None
        if stat == "team":
            t = await get_team(interaction.guild_id, team)
            if not t:
                return await interaction.response.send_message(embed=error_embed("Team Needed", "Pick an existing team in the `team` option."), ephemeral=True)
        if stat == "custom" and not custom_name:
            return await interaction.response.send_message(embed=error_embed("Name Needed", "Custom stats need a `custom_name`."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            line = await self._add(interaction.guild, stat, t, custom_name, custom_value, template)
        except discord.Forbidden:
            return await interaction.followup.send(embed=error_embed("Missing Permission", "I need **Manage Channels**."), ephemeral=True)
        await interaction.followup.send(embed=success_embed("Stats Channel", line or "That stat already exists — use `/stats config` to change it."), ephemeral=True)

    async def _find(self, interaction, stat, team, custom_name):
        t = await get_team(interaction.guild_id, team) if stat == "team" else None
        key = make_key(stat, t["team_id"] if t else None, custom_name)
        return await db.col("stats_channels").find_one({"guild_id": str(interaction.guild_id), "key": key})

    @group.command(name="remove", description="Remove a stats channel")
    @app_commands.choices(stat=STAT_CHOICES)
    @app_commands.autocomplete(team=team_autocomplete)
    async def remove(self, interaction: discord.Interaction, stat: str, team: str = None, custom_name: str = None):
        if not await self._admin(interaction):
            return
        cfg = await self._find(interaction, stat, team, custom_name)
        if not cfg:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No such stats channel is configured."), ephemeral=True)
        ch = interaction.guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
        if ch:
            try:
                await ch.delete(reason=f"Stats channel removed by {interaction.user}")
            except discord.HTTPException:
                pass
        await db.col("stats_channels").delete_one({"_id": cfg["_id"]})
        await interaction.response.send_message(embed=success_embed("Removed", f"Removed **{cfg['key']}**."), ephemeral=True)

    @group.command(name="config", description="View stats channels, or change a template/custom value")
    @app_commands.describe(stat="Stat to change (leave empty to list all)", template="New channel name template", value="New value (Custom stats)")
    @app_commands.choices(stat=STAT_CHOICES)
    @app_commands.autocomplete(team=team_autocomplete)
    async def config(self, interaction: discord.Interaction, stat: str = None, team: str = None, custom_name: str = None,
                     template: app_commands.Range[str, 1, 100] = None, value: app_commands.Range[str, 0, 60] = None):
        if not await self._admin(interaction):
            return
        if stat is None:
            rows = await db.col("stats_channels").find({"guild_id": str(interaction.guild_id)}).to_list(None)
            if not rows:
                return await interaction.response.send_message(embed=error_embed("No Stats Channels", "Use `/stats setup` or `/stats add`."), ephemeral=True)
            lines = [f"• `{r['key']}` → <#{r['channel_id']}> — `{r.get('template') or DEFAULT_TEMPLATES[r['type']]}`" for r in rows if r.get("channel_id")]
            e = brand("⚙️ Stats Channels", "\n".join(lines) or "None yet.")
            e.add_field(name="Placeholders", value="`{value}` `{name}` `{wins}` `{losses}` `{ties}` `{record}` `{pct}` `{teams}` `{rings}` `{leagues}` `{tier}`", inline=False)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        cfg = await self._find(interaction, stat, team, custom_name)
        if not cfg:
            return await interaction.response.send_message(embed=error_embed("Not Found", "No such stats channel is configured."), ephemeral=True)
        updates = {}
        if template:
            updates["template"] = template
        if value is not None and cfg["type"] == "custom":
            updates["value"] = value
        if not updates:
            return await interaction.response.send_message(embed=error_embed("Nothing to Change", "Give a `template` or `value`."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await db.col("stats_channels").update_one({"_id": cfg["_id"]}, {"$set": updates})
        await self.update_one(interaction.guild, {**cfg, **updates})
        await interaction.followup.send(embed=success_embed("Updated", f"`{cfg['key']}` updated."), ephemeral=True)

    @group.command(name="update", description="Refresh all stats channels now")
    async def update(self, interaction: discord.Interaction):
        if not await self._admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self.update_guild(interaction.guild)
        await interaction.followup.send(embed=success_embed("Updated", "Stats channels refreshed. (Discord limits channel renames, so a name can take a few minutes to change.)"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
