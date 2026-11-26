"""shared/resources.py — declarative description of every dashboard resource + a pure validator.

The frontend renders tables/forms from these specs (served at /api/meta), and the backend validates every write
against the same spec — so a field exists in exactly one place. No database or web-framework imports here.
"""

import re
from datetime import datetime, timezone

from shared.timeutil import parse_duration


class ValidationError(Exception):
    def __init__(self, errors: dict):
        super().__init__(errors)
        self.errors = errors


def F(name: str, type: str = "str", label: str | None = None, **kw) -> dict:
    return {"name": name, "type": type, "label": label or name.replace("_", " ").title(), **kw}


SNOWFLAKE = re.compile(r"^\d{15,21}$")
HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _bad(msg: str):
    raise ValueError(msg)


def clean_value(f: dict, v):
    """Validate/normalise one value. Raises ValueError with a user-facing message."""
    t = f["type"]
    if v is None or (isinstance(v, str) and not v.strip() and t not in ("lines", "bool")):
        if f.get("required"):
            _bad("Required")
        return f.get("default")
    if t in ("str", "text"):
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v)).strip()
        mx = f.get("max", 200 if t == "str" else 4000)
        if len(s) > mx:
            _bad(f"Max {mx} characters")
        if len(s) < f.get("min", 0):
            _bad(f"Min {f['min']} characters")
        return s
    if t == "int":
        try:
            n = int(v)
        except (TypeError, ValueError):
            _bad("Must be a whole number")
        if n < f.get("min", -(10 ** 9)) or n > f.get("max", 10 ** 9):
            _bad(f"Must be between {f.get('min', '-∞')} and {f.get('max', '∞')}")
        return n
    if t == "bool":
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    if t == "select":
        if str(v) not in [str(c) for c in f["choices"]]:
            _bad("Pick one of: " + ", ".join(map(str, f["choices"])))
        return str(v)
    if t == "snowflake":
        if not SNOWFLAKE.match(str(v).strip()):
            _bad("Must be a Discord ID (15-21 digits)")
        return str(v).strip()
    if t == "snowflakes":
        items = v if isinstance(v, list) else [x for x in re.split(r"[\s,]+", str(v)) if x]
        items = [str(x).strip() for x in items]
        if any(not SNOWFLAKE.match(x) for x in items):
            _bad("Contains an invalid Discord ID")
        if len(items) > f.get("max_items", 25):
            _bad(f"At most {f.get('max_items', 25)} items")
        if len(items) < f.get("min_items", 0):
            _bad(f"At least {f['min_items']} required")
        return list(dict.fromkeys(items))
    if t == "datetime":
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            _bad("Invalid date/time")
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if t == "url":
        s = str(v).strip()
        if not re.match(r"^https?://[^\s]+$", s) or len(s) > 500:
            _bad("Must be an http(s):// URL")
        return s
    if t == "lines":
        items = v if isinstance(v, list) else str(v).splitlines()
        items = [re.sub(r"\s+", " ", str(x)).strip() for x in items]
        items = [x for x in items if x]
        if len(items) < f.get("min_items", 0) or len(items) > f.get("max_items", 50):
            _bad(f"Between {f.get('min_items', 0)} and {f.get('max_items', 50)} lines")
        if any(len(x) > f.get("item_max", 100) for x in items):
            _bad(f"Each line max {f.get('item_max', 100)} characters")
        if f.get("unique") and len({x.lower() for x in items}) != len(items):
            _bad("Lines must be unique")
        return items
    if t == "color":
        m = HEX.match(str(v).strip())
        if not m:
            _bad("Use a hex colour like #FF8800")
        return int(m.group(1), 16)
    if t == "duration":
        td = parse_duration(str(v))
        if not td or td.days > f.get("max_days", 60):
            _bad(f"Use e.g. 30m, 2h, 1d (max {f.get('max_days', 60)}d)")
        return str(v).strip().lower()
    if t == "embed_fields":  # lines of "name | value | inline(yes/no)"
        lines = v if isinstance(v, list) else str(v).splitlines()
        out = []
        for line in lines:
            if isinstance(line, dict):
                name, value, inline = line.get("name", ""), line.get("value", ""), bool(line.get("inline"))
            else:
                parts = [p.strip() for p in str(line).split("|")]
                if not any(parts):
                    continue
                if len(parts) < 2:
                    _bad("Each field must look like: name | value | yes/no")
                name, value, inline = parts[0], parts[1], (len(parts) > 2 and parts[2].lower() in ("y", "yes", "true", "1"))
            if not name or not value or len(name) > 256 or len(value) > 1024:
                _bad("Field name ≤256 and value ≤1024 characters, both required")
            out.append({"name": name, "value": value, "inline": inline})
        if len(out) > 25:
            _bad("At most 25 fields")
        return out
    raise ValueError(f"unknown field type {t}")


def validate(spec: dict, data: dict, partial: bool = False, creating: bool = True) -> dict:
    """Validate `data` against a resource spec. `partial` skips absent fields (PATCH). Unknown keys are dropped,
    which is also what stops a client from setting guild_id, _id or counters."""
    errors, out = {}, {}
    for f in spec["fields"]:
        n = f["name"]
        if f.get("create_only") and not creating:
            continue
        if f.get("readonly"):
            continue
        if n not in data:
            if partial:
                continue
            if f.get("required"):
                errors[n] = "Required"
            elif "default" in f:
                out[n] = f["default"]
            continue
        try:
            out[n] = clean_value(f, data[n])
        except ValueError as e:
            errors[n] = str(e)
    if not errors and spec.get("check"):
        try:
            spec["check"](out, partial)
        except ValueError as e:
            errors["_"] = str(e)
    if errors:
        raise ValidationError(errors)
    return out


def _workflow_check(d: dict, partial: bool):
    if partial:
        return
    a = d.get("action")
    if a == "send_message" and not (d.get("channel_id") and d.get("message")):
        _bad("'Send message' needs a channel and a message")
    if a == "add_role" and not d.get("role_id"):
        _bad("'Add role' needs a role")
    if a == "dm_member" and not d.get("message"):
        _bad("'DM member' needs a message")


def _reminder_check(d: dict, partial: bool):
    if not partial and not (d.get("channel_id") or d.get("user_id")):
        _bad("Choose a channel or a user to remind")


def _match_check(d: dict, partial: bool):
    if partial:
        return
    away_id, away_name = d.get("away_team_id"), (d.get("away_name") or "").strip()
    if not away_id and not away_name:
        _bad("Pick an away team or type your opponent's name")
    if away_id and away_name:
        _bad("Use either an away team or an opponent's name, not both")
    if away_id and away_id == d.get("home_team_id"):
        _bad("A team can't play itself")


STATUS = lambda *c: F("status", "select", "Status", choices=list(c), default=c[0])  # noqa: E731

RESOURCES: dict[str, dict] = {}


def R(key: str, label: str, icon: str, group: str, collection: str, fields: list, columns: list, **kw):
    RESOURCES[key] = {"key": key, "label": label, "icon": icon, "group": group, "collection": collection, "fields": fields,
                      "columns": columns, "read": "viewer", "write": "staff", "delete": "manager", "create": True, "update": True,
                      "sort": ["created_at", -1], "actions": [], "search": [], **kw}


# ───────────── Competition ─────────────
R("leagues", "Leagues", "", "Competition", "leagues",
  [F("league_name", "str", "League name", required=True, max=100),
   F("league_abbr", "str", "Abbreviation", required=True, max=10, help="2–10 letters/numbers, e.g. NFA (the bot also creates a role with this name)."),
   F("signup", "url", "Signup link"), F("role_id", "snowflake", "League role", picker="role"),
   F("team_count", "int", "Teams", readonly=True)],
  ["league_abbr", "league_name", "role_id", "team_count"], search=["league_name", "league_abbr"], sort=["league_name", 1],
  write="manager", delete="manager",
  help="The same leagues as /league add in Discord. Give a team a league by setting its league to this name.")
DEFAULT_TEAM_COLOR = 0x5865F2   # the colour a team role gets when nobody picked one (Discord blurple)

R("teams", "Teams", "", "Competition", "teams",
  [F("name", required=True, max=50), F("competition", picker="competition", max=50, default="General", label="League / competition"),
   F("division", picker="division", max=50), F("logo", "url"),
   F("color", "color", "Team colour", default=DEFAULT_TEAM_COLOR, help="The bot creates a Discord role named after the team in this colour.")],
  ["name", "competition", "division", "record", "win_pct"], search=["name"], sort=["name_lower", 1],
  help="Change a record from the Teams tab of the Competition page so every change is logged.")
R("team_members", "Rosters", "👥", "Competition", "team_members",
  [F("team_id", "int", "Team", picker="team", required=True), F("user_id", "snowflake", "Discord user", picker="user", required=True),
   F("username", max=80), F("position", max=40), F("jersey", "int", min=0, max=999),
   F("status", "select", choices=["active", "reserve", "injured", "inactive"], default="active"), F("notes", "text", max=1000)],
  ["team_name", "username", "position", "jersey", "status"], search=["username", "position"], sort=["team_name", 1],
  help="Build a whole roster at once from the Rosters tab — up to 25 players per team, each with a Discord ID, username and position.")
R("competitions", "Competitions", "", "Competition", "competitions",
  [F("name", required=True, max=50, help="Renaming a competition updates its divisions and the teams in it."),
   F("season", max=30), STATUS("active", "upcoming", "completed"), F("description", "text", max=500)],
  ["name", "season", "status"], search=["name"], write="manager", delete="manager")
R("divisions", "Divisions", "🧩", "Competition", "divisions",
  [F("competition", picker="competition", required=True, max=50),
   F("name", required=True, max=50, help="Renaming a division (or moving it to another competition) moves the teams in it too.")],
  ["competition", "name"], search=["name", "competition"], write="manager")
R("matches", "Match History", "🏟️", "Competition", "matches",
  [F("competition", picker="competition", max=50), F("home_team_id", "int", "Home team", picker="team", required=True),
   F("away_team_id", "int", "Away team", picker="team", help="Pick a team already in the dashboard, or leave this empty and name the opponent below."),
   F("away_name", "str", "Opponent name", max=50, help="For an opponent that isn't a dashboard team — e.g. a team you only know by name."),
   F("home_score", "int", required=True, min=0, max=9999),
   F("away_score", "int", required=True, min=0, max=9999), F("played_at", "datetime", required=True),
   F("notes", "text", max=1000),
   F("apply_record", "bool", "Update team records", default=False, create_only=True,
     help="Adds a win/loss/tie to your team — and to the opponent's too when it's a dashboard team (logged in record history)."),
   F("ff", "text", "Game stats", readonly=True)],
  ["played_at", "home_name", "score", "away_name", "competition", "ff"], sort=["played_at", -1], search=["notes", "competition"],
  check=_match_check, help="Import a Football Fusion report, or scan a Practical Basketball / Ultimate Football screenshot, from the Matches tab to attach the full game stats sheet to a match.")

# ───────────── Community ─────────────
R("polls", "Polls", "📊", "Community", "polls",
  [F("question", required=True, max=200), F("options", "lines", required=True, min_items=2, max_items=10, item_max=70, unique=True,
                                          help="One option per line"),
   F("duration", "duration", default="1d", max_days=30), F("anonymous", "bool", default=False), F("multiple", "bool", "Multiple choice", default=False),
   F("channel_id", "snowflake", "Channel", picker="channel", required=True)],
  ["poll_id", "question", "status", "voters", "ends_at"], update=False, search=["question"], write="staff",
  actions=[{"name": "end", "label": "End now", "when": {"status": "open"}, "confirm": True}],
  help="Posted by the bot into the chosen channel. Votes are cast with the buttons on the Discord message.")
R("giveaways", "Giveaways", "🎉", "Community", "giveaways",
  [F("prize", required=True, max=200), F("winners_count", "int", "Winners", required=True, min=1, max=20, default=1),
   F("duration", "duration", required=True, max_days=60), F("channel_id", "snowflake", "Channel", picker="channel", required=True),
   F("required_role_id", "snowflake", "Required role", picker="role"), F("min_account_days", "int", "Min account age (days)", min=0, max=3650, default=0)],
  ["giveaway_id", "prize", "status", "entries", "ends_at"], update=False, write="manager", search=["prize"],
  actions=[{"name": "end", "label": "End & draw", "when": {"status": "open"}, "confirm": True},
           {"name": "reroll", "label": "Reroll", "when": {"status": "ended"}, "params": [F("count", "int", min=1, max=20, default=1)]}])
R("countdowns", "Countdowns", "⏳", "Community", "countdowns",
  [F("title", required=True, max=100), F("date", "str", "Date (YYYY-MM-DD)", required=True, max=10, create_only=True),
   F("time", "str", "Time (HH:MM, 24h)", required=True, max=5, create_only=True), F("timezone", "str", picker="timezone", default="UTC", max=60, create_only=True),
   F("channel_id", "snowflake", "Channel", picker="channel", required=True), F("role_id", "snowflake", "Role to ping", picker="role")],
  ["countdown_id", "title", "status", "ends_at"], update=False, search=["title"])
R("events", "Events", "📅", "Community", "events",
  [F("title", required=True, max=100), F("starts_at", "datetime", required=True), F("ends_at", "datetime"),
   F("channel_id", "snowflake", "Channel", picker="channel"), F("description", "text", max=2000)],
  ["title", "starts_at", "ends_at"], sort=["starts_at", 1], search=["title"])
R("suggestions", "Suggestions", "💡", "Community", "suggestions", [], ["suggestion_id", "text", "status", "author_id"],
  create=False, update=False, delete="manager", sort=["suggestion_id", -1], search=["text"],
  actions=[{"name": "approve", "label": "Approve", "when": {"status": "pending"}, "params": [F("response", "text", max=1000)]},
           {"name": "deny", "label": "Deny", "when": {"status": "pending"}, "params": [F("response", "text", max=1000)], "danger": True}],
  help="Members submit suggestions with /suggestion submit in Discord; staff respond here or in Discord.")
R("announcements", "Announcements & Embeds", "📢", "Community", "announcements",
  [F("title", required=True, max=256), F("description", "text", required=True, max=3500), F("color", "color", default=0xFF7A1A),
   F("author_name", max=256), F("image", "url"), F("thumbnail", "url"), F("footer", max=200),
   F("fields", "embed_fields", "Fields", help="One per line: name | value | yes/no (inline)"),
   F("ping_role_id", "snowflake", "Ping role", picker="role"), F("channel_id", "snowflake", "Channel", picker="channel", required=True)],
  ["announcement_id", "title", "channel_id", "created_at"], update=False, write="manager", search=["title"],
  help="Sends a formatted embed through the bot. Use fields for a fully custom embed.")
R("role_panels", "Role Panels", "🎭", "Community", "role_panels",
  [F("title", required=True, max=200), F("description", "text", max=1000), F("style", "select", choices=["buttons", "dropdown"], default="buttons"),
   F("max_roles", "int", min=0, max=25, default=0), F("channel_id", "snowflake", "Channel", picker="channel", required=True, create_only=True),
   F("role_ids", "snowflakes", "Roles", picker="roles", required=True, min_items=1, max_items=25)],
  ["panel_id", "title", "style", "role_count"], write="manager", search=["title"])
R("scheduled_messages", "Scheduled Messages", "🗓️", "Automation", "scheduled_messages",
  [F("channel_id", "snowflake", "Channel", picker="channel", required=True), F("title", max=256), F("content", "text", required=True, max=2000),
   F("send_at", "datetime", required=True), F("repeat", "select", choices=["none", "daily", "weekly"], default="none"),
   F("enabled", "bool", default=True)],
  ["title", "content", "send_at", "repeat", "enabled"], sort=["send_at", 1], search=["title", "content"], write="manager")
R("reminders", "Reminders", "⏰", "Automation", "reminders",
  [F("text", required=True, max=500), F("remind_at", "datetime", required=True), F("channel_id", "snowflake", "Channel", picker="channel"),
   F("user_id", "snowflake", "DM this user", picker="user"), F("repeat", "select", choices=["none", "daily", "weekly"], default="none")],
  ["text", "remind_at", "repeat", "status"], sort=["remind_at", 1], check=_reminder_check, search=["text"])
R("workflows", "Workflows", "⚙️", "Automation", "workflows",
  [F("name", required=True, max=100), F("trigger", "select", choices=["member_join", "member_leave"], required=True),
   F("action", "select", choices=["send_message", "add_role", "dm_member"], required=True),
   F("channel_id", "snowflake", "Channel", picker="channel"), F("role_id", "snowflake", "Role", picker="role"),
   F("message", "text", max=1000, help="Placeholders: {user} {username} {server}"), F("enabled", "bool", default=True)],
  ["name", "trigger", "action", "enabled"], write="manager", check=_workflow_check, search=["name"])
R("webhooks", "Webhooks", "🔗", "Automation", "webhooks",
  [F("name", required=True, max=80), F("url", "url", "Webhook URL", required=True, secret=True), F("username", max=80)],
  ["name", "username", "url_masked"], write="admin", read="manager", delete="admin",
  actions=[{"name": "send", "label": "Send message", "level": "manager",
            "params": [F("content", "text", required=True, max=2000)]}],
  help="Webhook URLs are encrypted at rest and never shown again after saving.")

# ───────────── Staff & moderation ─────────────
R("moderation_logs", "Moderation Logs", "🛡️", "Staff", "moderation_logs", [], ["created_at", "title", "text", "moderator"],
  create=False, update=False, read="staff", delete="admin", search=["text", "title"])
R("appeals", "Appeals", "⚖️", "Staff", "appeals",
  [F("user_id", "snowflake", "Appellant (Discord ID)", picker="user", required=True), F("username", max=80),
   F("type", "select", choices=["ban", "mute", "suspension", "other"], default="ban"), F("reason", "text", "Their appeal", required=True, max=2000),
   STATUS("pending", "approved", "denied"), F("response", "text", max=1000)],
  ["username", "type", "status", "created_at"], read="staff", write="staff", search=["username", "reason"],
  actions=[{"name": "approve", "label": "Approve", "when": {"status": "pending"}, "level": "manager",
            "params": [F("response", "text", max=1000), F("unban", "bool", "Also unban them in Discord", default=False)]},
           {"name": "deny", "label": "Deny", "when": {"status": "pending"}, "level": "manager", "danger": True,
            "params": [F("response", "text", max=1000)]}])
R("staff_shifts", "Staff Shifts", "🕒", "Staff", "staff_shifts",
  [F("user_id", "snowflake", "Staff member", picker="user", required=True), F("username", max=80), F("role", max=60),
   F("start", "datetime", required=True), F("end", "datetime"), F("notes", "text", max=500)],
  ["username", "role", "start", "end"], sort=["start", -1], read="staff", search=["username", "role"])
R("staff_reports", "Staff Reports", "📝", "Staff", "staff_reports",
  [F("title", required=True, max=100), F("category", "select", choices=["activity", "conduct", "performance", "other"], default="activity"),
   F("subject_user_id", "snowflake", "About (Discord ID)", picker="user"), F("body", "text", required=True, max=4000)],
  ["title", "category", "author_name", "created_at"], read="manager", write="staff", search=["title", "body"])
R("dashboard_permissions", "Dashboard Access", "🔑", "Staff", "dashboard_permissions",
  [F("user_id", "snowflake", "Discord user ID", picker="user", required=True), F("username", max=80),
   F("level", "select", choices=["viewer", "staff", "manager"], default="staff"), F("notes", max=200)],
  ["username", "user_id", "level"], read="admin", write="admin", delete="admin", search=["username", "user_id"],
  help="Server owners and members with Administrator / Manage Server always have full access. Grant everyone else here.")
R("audit_logs", "Audit Log", "🧾", "Staff", "audit_logs", [], ["created_at", "user_name", "action", "target"],
  create=False, update=False, read="manager", delete="admin", search=["user_name", "action", "target"])

# ───────────── Players ─────────────
R("player_profiles", "Player Profiles", "🪪", "Players", "player_profiles",
  [F("name", required=True, max=80), F("user_id", "snowflake", "Discord user", picker="user"), F("team_id", "int", "Team", picker="team"),
   F("position", max=40), F("jersey", "int", min=0, max=999), STATUS("active", "free_agent", "retired"), F("bio", "text", max=2000)],
  ["name", "position", "jersey", "team_name", "status"], search=["name", "position"])
R("tryouts", "Tryouts", "🧪", "Players", "tryouts",
  [F("player_name", required=True, max=80), F("user_id", "snowflake", "Discord user", picker="user"), F("team_id", "int", "Team", picker="team"),
   F("position", max=40), F("date", "datetime"), STATUS("scheduled", "passed", "failed", "no_show"), F("score", "int", min=0, max=100), F("notes", "text", max=1000)],
  ["player_name", "position", "team_name", "date", "status", "score"], search=["player_name", "position"])
R("awards", "Awards", "🏅", "Players", "awards",
  [F("user_id", "snowflake", "Winner (Discord ID)", picker="user", required=True), F("award_name", required=True, max=100), F("season", required=True, max=20)],
  ["user_id", "award_name", "season"], sort=["given_at", -1], search=["award_name", "season"], write="manager",
  help="Shared with the bot's /award and /awardcheck commands.")
R("scouting_reports", "Scouting", "🔍", "Players", "scouting_reports",
  [F("player_name", required=True, max=80), F("user_id", "snowflake", "Discord user", picker="user"), F("position", max=40),
   F("rating", "int", min=0, max=100), F("strengths", "text", max=1000), F("weaknesses", "text", max=1000), F("report", "text", max=3000)],
  ["player_name", "position", "rating", "scout_name"], search=["player_name", "position"])
R("hall_of_fame", "Hall of Fame", "🏛️", "Players", "hall_of_fame",
  [F("name", required=True, max=100), F("user_id", "snowflake", "Discord user", picker="user"), F("category", max=60),
   F("season", max=30), F("achievement", "text", required=True, max=2000), F("image", "url")],
  ["name", "category", "season"], write="manager", search=["name", "category"])

# ───────────── Statistics channels ─────────────
R("stats_channels", "Statistics Channels", "📈", "Competition", "stats_channels",
  [F("type", "select", "Statistic", choices=["members", "humans", "bots", "boosts", "team", "record", "rings", "rings_league", "custom"],
     required=True, create_only=True),
   F("team_id", "int", "Team", picker="team", create_only=True), F("label", "str", "Custom name", max=40, create_only=True),
   F("league", "str", "League", max=20, create_only=True, help="For Rings by league: the league's abbreviation or name, e.g. NFA."),
   F("value", "str", "Custom value", max=60), F("template", "str", "Channel name template", max=100,
                                              help="e.g. 👥 Members: {value}   •   placeholders: {value} {name} {record} {wins} {losses} {ties} {pct} {teams} {rings} {leagues}")],
  ["key", "type", "channel_id", "last_name"], write="manager",
  help="Overall team record adds up every team's wins/losses/ties across all leagues; Championship rings counts the rings in this server, or just one league's.")

RESOURCE_ORDER = list(RESOURCES)


def public_spec(spec: dict) -> dict:
    """What the frontend is allowed to see (no callables)."""
    return {k: v for k, v in spec.items() if k != "check"}
