"""database/schema.py — indexes for the collections the dashboard introduced.

One shared MongoDB database serves the bot and the website. The bot's own collections (teams, polls, giveaways, …) are
indexed by the bot's migration 1; this file covers everything else. `apply_indexes` works with both Motor (bot) and
PyMongo's async client (backend), and is idempotent, so either process can run it at startup.

Collection map (spec name -> real collection, kept stable so existing bot data keeps working):
  server_settings -> guild_config        statistics_channels -> stats_channels      team_records -> teams + team_history
  member_activity -> activity_daily      servers -> servers (bot publishes name/channels/roles for the website)
"""

# (collection, keys, options)
INDEXES = [
    ("users", [("user_id", 1)], {"unique": True}),
    ("sessions", [("token_hash", 1)], {"unique": True}),
    ("sessions", [("expires_at", 1)], {"expireAfterSeconds": 0}),          # sessions delete themselves
    ("oauth_states", [("state", 1)], {"unique": True}),
    ("oauth_states", [("expires_at", 1)], {"expireAfterSeconds": 0}),
    ("servers", [("guild_id", 1)], {"unique": True}),
    ("bot_jobs", [("status", 1), ("created_at", 1)], {}),
    ("bot_jobs", [("guild_id", 1), ("created_at", -1)], {}),
    ("bot_jobs", [("expires_at", 1)], {"expireAfterSeconds": 0}),
    ("audit_logs", [("guild_id", 1), ("created_at", -1)], {}),
    ("moderation_logs", [("guild_id", 1), ("created_at", -1)], {}),
    ("dashboard_permissions", [("guild_id", 1), ("user_id", 1)], {"unique": True}),
    ("members", [("guild_id", 1), ("user_id", 1)], {"unique": True}),                       # the bot's member roster for the directory
    ("members", [("guild_id", 1), ("username", 1)], {}),
    ("member_sync", [("guild_id", 1)], {"unique": True}),
    ("leagues", [("guild_id", 1), ("league_abbr", 1)], {"unique": True}),
    ("team_members", [("guild_id", 1), ("team_id", 1), ("user_id", 1)], {"unique": True}),   # a player is on a roster once
    ("team_members", [("guild_id", 1), ("user_id", 1)], {}),                                  # "which teams is this player on?"
    ("matches", [("guild_id", 1), ("home_team_id", 1), ("played_at", -1)], {}),
    ("matches", [("guild_id", 1), ("away_team_id", 1), ("played_at", -1)], {}),
    ("competitions", [("guild_id", 1), ("name_lower", 1)], {"unique": True}),
    ("divisions", [("guild_id", 1), ("competition_lower", 1), ("name_lower", 1)], {"unique": True}),
    ("matches", [("guild_id", 1), ("played_at", -1)], {}),
    ("events", [("guild_id", 1), ("starts_at", 1)], {}),
    ("scheduled_messages", [("enabled", 1), ("send_at", 1)], {}),
    ("scheduled_messages", [("guild_id", 1), ("send_at", 1)], {}),
    ("reminders", [("status", 1), ("remind_at", 1)], {}),
    ("reminders", [("guild_id", 1), ("remind_at", 1)], {}),
    ("workflows", [("guild_id", 1), ("trigger", 1), ("enabled", 1)], {}),
    ("webhooks", [("guild_id", 1), ("name_lower", 1)], {"unique": True}),
    ("appeals", [("guild_id", 1), ("status", 1), ("created_at", -1)], {}),
    ("staff_shifts", [("guild_id", 1), ("start", -1)], {}),
    ("staff_reports", [("guild_id", 1), ("created_at", -1)], {}),
    ("player_profiles", [("guild_id", 1), ("name", 1)], {}),
    ("tryouts", [("guild_id", 1), ("created_at", -1)], {}),
    ("scouting_reports", [("guild_id", 1), ("created_at", -1)], {}),
    ("hall_of_fame", [("guild_id", 1), ("created_at", -1)], {}),
    ("announcements", [("guild_id", 1), ("created_at", -1)], {}),
]


async def apply_indexes(db) -> list:
    """Create all indexes; returns a list of error strings (never raises, so one bad index can't stop startup)."""
    errors = []
    for coll, keys, opts in INDEXES:
        try:
            await db[coll].create_index(keys, **opts)
        except Exception as e:  # e.g. pre-existing duplicate data blocking a unique index
            errors.append(f"{coll} {keys}: {e}")
    return errors
