"""utils/backup_io.py — Serialise a guild's structure and (re)create it. Only what Discord's API exposes and bots may recreate."""

import discord

NOT_SAVED = ("messages, members and which roles they hold, emojis/stickers, webhooks, integrations, bans, invites, "
             "audit log, server icon/banner, and permission overwrites that target individual members")

_TYPE_NAMES = {
    discord.ChannelType.text: "text", discord.ChannelType.voice: "voice", discord.ChannelType.news: "news",
    discord.ChannelType.stage_voice: "stage", discord.ChannelType.forum: "forum",
}


def _overwrites(channel, guild: discord.Guild) -> list:
    out = []
    for target, ow in channel.overwrites.items():
        if isinstance(target, discord.Role):
            allow, deny = ow.pair()
            out.append({"role_id": str(target.id), "role_name": target.name, "everyone": target.is_default(), "allow": allow.value, "deny": deny.value})
    return out


def serialize(guild: discord.Guild) -> dict:
    roles = [
        {"id": str(r.id), "name": r.name, "color": r.color.value, "hoist": r.hoist, "mentionable": r.mentionable,
         "permissions": r.permissions.value, "position": r.position}
        for r in guild.roles if not r.is_default() and not r.managed
    ]
    categories = [{"id": str(c.id), "name": c.name, "position": c.position, "overwrites": _overwrites(c, guild)} for c in guild.categories]
    channels = []
    for c in sorted(guild.channels, key=lambda c: c.position):
        kind = _TYPE_NAMES.get(c.type)
        if kind is None:  # categories handled above; threads aren't structure
            continue
        channels.append({
            "name": c.name, "type": kind, "category_id": str(c.category_id) if c.category_id else None, "position": c.position,
            "topic": getattr(c, "topic", None), "nsfw": getattr(c, "nsfw", False), "slowmode": getattr(c, "slowmode_delay", 0) or 0,
            "bitrate": getattr(c, "bitrate", None), "user_limit": getattr(c, "user_limit", None), "overwrites": _overwrites(c, guild),
        })
    return {
        "version": 1, "guild_name": guild.name,
        "settings": {
            "verification_level": guild.verification_level.value, "default_notifications": guild.default_notifications.value,
            "explicit_content_filter": guild.explicit_content_filter.value, "afk_timeout": guild.afk_timeout,
        },
        "everyone_permissions": guild.default_role.permissions.value,
        "roles": roles, "categories": categories, "channels": channels,
    }


def summarize(data: dict) -> dict:
    return {"roles": len(data["roles"]), "categories": len(data["categories"]), "channels": len(data["channels"])}


async def restore(guild: discord.Guild, data: dict, wipe: bool) -> dict:
    """wipe=False: only create what's missing (non-destructive). wipe=True: delete existing channels/roles first."""
    report = {"roles_created": 0, "roles_existing": 0, "channels_created": 0, "channels_existing": 0, "deleted": 0, "failed": []}
    me_perms = guild.me.guild_permissions.value

    if wipe:
        for ch in list(guild.channels):
            try:
                await ch.delete(reason="TeamCore backup restore")
                report["deleted"] += 1
            except discord.HTTPException:
                report["failed"].append(f"delete #{ch.name}")
        for role in list(guild.roles):
            if role.is_default() or role.managed or role >= guild.me.top_role:
                continue
            try:
                await role.delete(reason="TeamCore backup restore")
                report["deleted"] += 1
            except discord.HTTPException:
                report["failed"].append(f"delete @{role.name}")
        try:
            s = data["settings"]
            await guild.edit(
                verification_level=discord.VerificationLevel(s["verification_level"]),
                default_notifications=discord.NotificationLevel(s["default_notifications"]),
                explicit_content_filter=discord.ContentFilter(s["explicit_content_filter"]), afk_timeout=s["afk_timeout"],
                reason="TeamCore backup restore",
            )
            await guild.default_role.edit(permissions=discord.Permissions(data["everyone_permissions"] & me_perms), reason="TeamCore backup restore")
        except (discord.HTTPException, ValueError, KeyError):
            report["failed"].append("server settings")

    # Roles: highest first, because new roles are inserted at the bottom, which reproduces the hierarchy
    role_by_old_id: dict[str, discord.Role] = {}
    for r in sorted(data["roles"], key=lambda r: -r["position"]):
        existing = discord.utils.get(guild.roles, name=r["name"]) if not wipe else None
        if existing:
            role_by_old_id[r["id"]] = existing
            report["roles_existing"] += 1
            continue
        try:
            new = await guild.create_role(
                name=r["name"], colour=discord.Colour(r["color"]), hoist=r["hoist"], mentionable=r["mentionable"],
                permissions=discord.Permissions(r["permissions"] & me_perms),  # can't grant permissions the bot lacks
                reason="TeamCore backup restore",
            )
            role_by_old_id[r["id"]] = new
            report["roles_created"] += 1
        except discord.HTTPException:
            report["failed"].append(f"role {r['name']}")

    def overwrites(items: list) -> dict:
        out = {}
        for o in items:
            target = guild.default_role if o["everyone"] else role_by_old_id.get(o["role_id"])
            if target is not None:
                out[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(o["allow"]), discord.Permissions(o["deny"]))
        return out

    cat_by_old_id: dict[str, discord.CategoryChannel] = {}
    for c in sorted(data["categories"], key=lambda c: c["position"]):
        existing = discord.utils.get(guild.categories, name=c["name"]) if not wipe else None
        if existing:
            cat_by_old_id[c["id"]] = existing
            report["channels_existing"] += 1
            continue
        try:
            cat_by_old_id[c["id"]] = await guild.create_category(c["name"], overwrites=overwrites(c["overwrites"]), reason="TeamCore backup restore")
            report["channels_created"] += 1
        except discord.HTTPException:
            report["failed"].append(f"category {c['name']}")

    for ch in sorted(data["channels"], key=lambda c: c["position"]):
        category = cat_by_old_id.get(ch["category_id"]) if ch["category_id"] else None
        siblings = category.channels if category else [c for c in guild.channels if getattr(c, "category_id", None) is None]
        if not wipe and any(s.name == ch["name"] and _TYPE_NAMES.get(s.type) == ch["type"] for s in siblings):
            report["channels_existing"] += 1
            continue
        kw = {"category": category, "overwrites": overwrites(ch["overwrites"]), "reason": "TeamCore backup restore"}
        try:
            kind = ch["type"]
            if kind in ("text", "news"):
                extra = dict(topic=ch["topic"], nsfw=ch["nsfw"], slowmode_delay=ch["slowmode"])
                try:
                    await guild.create_text_channel(ch["name"], news=(kind == "news"), **extra, **kw)
                except (discord.HTTPException, TypeError):  # announcement channels need the Community feature
                    await guild.create_text_channel(ch["name"], **extra, **kw)
            elif kind == "voice":
                await guild.create_voice_channel(ch["name"], bitrate=min(ch["bitrate"] or 64000, int(guild.bitrate_limit)), user_limit=ch["user_limit"] or 0, **kw)
            elif kind == "stage":
                await guild.create_stage_channel(ch["name"], **kw)
            elif kind == "forum":
                await guild.create_forum(ch["name"], topic=ch["topic"] or "", **kw)
            report["channels_created"] += 1
        except discord.HTTPException:
            report["failed"].append(f"{ch['type']} #{ch['name']}")
    return report
