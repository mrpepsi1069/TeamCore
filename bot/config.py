"""config.py — Bot-wide constants and settings."""

import os

from dotenv import load_dotenv

load_dotenv()  # safe to call more than once; makes env vars available at import time


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ── Hard-coded IDs (previously scattered across 6 files) ─────────────────────
# Override any of these with an environment variable; defaults keep the old behaviour.
OWNER_ID              = _int_env("BOT_OWNER_ID",         1314823988253560852)
SUGGESTION_CHANNEL_ID = _int_env("SUGGESTION_CHANNEL_ID", 1466284506385219686)
LOG_GUILD_ID          = _int_env("LOG_GUILD_ID",          1575685125516296194)
TEMPLATE_GUILD_ID     = _int_env("TEMPLATE_GUILD_ID",     1487243792917463072)

# Private logging channels, addressed by ID so renaming them in Discord keeps them working.
LOG_JOINS_CHANNEL_ID  = _int_env("LOG_JOINS_CHANNEL_ID",  1575685127361527958)
LOG_LEAVES_CHANNEL_ID = _int_env("LOG_LEAVES_CHANNEL_ID", 1575685127361527959)
LOG_ERRORS_CHANNEL_ID = _int_env("LOG_ERRORS_CHANNEL_ID", 1575685127361527960)

SUPPORT_SERVER_URL = os.getenv("SUPPORT_SERVER_URL", "https://discord.gg/3cWdPsCPC8")

# Permissions the bot actually needs, instead of requesting Administrator (8):
# create invites, kick, ban, manage channels, manage server, add reactions, view
# channels, send messages, manage messages, embed links, read history, mention
# everyone/roles, external emoji, mute members, manage roles, moderate members.
INVITE_PERMISSIONS = 1099784744055

COLORS = {
    "primary": 0x5865F2,   # Discord blurple
    "success": 0x57F287,   # Green
    "error":   0xED4245,   # Red
    "warning": 0xFEE75C,   # Yellow
    "gold":    0xFFD700,
}

EMOJIS = {
    "ring":   "\U0001F48D",
    "trophy": "\U0001F3C6",
    "check":  "\u2705",
    "cross":  "\u274C",
}

PREMIUM = {
    "price":    4,
    "currency": "USD",
    "features": [
        "Auto-DM game times to team members",
        "Custom commands",
        "Customizable bot name and avatar",
        "DM all members with announcements",
        "Priority support",
        "Early access to new features",
        "Advanced data backup",
        "Detailed usage statistics",
    ],
}
