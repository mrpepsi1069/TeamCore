"""shared/access.py — dashboard access levels and Discord permission bits (pure functions)."""

LEVELS = {"viewer": 1, "staff": 2, "manager": 3, "admin": 4}
LEVEL_NAMES = {v: k for k, v in LEVELS.items()}

ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20


def level_from_discord(owner: bool, permissions: str | int | None) -> str | None:
    """Discord server owners, Administrators and Manage Server members get full ('admin') dashboard access.
    Everyone else needs an explicit grant in `dashboard_permissions`."""
    try:
        bits = int(permissions or 0)
    except (TypeError, ValueError):
        bits = 0
    if owner or bits & ADMINISTRATOR or bits & MANAGE_GUILD:
        return "admin"
    return None


def has_level(have: str | None, need: str) -> bool:
    return LEVELS.get(have or "", 0) >= LEVELS[need]

# Permissions a self-assign role must never carry (mirrors bot/cogs/rolepanel.py).
DANGEROUS_BITS = (0x8 | 0x20 | (1 << 28) | (1 << 4) | (1 << 1) | (1 << 2) | (1 << 40) | (1 << 29) | (1 << 13) | (1 << 7))
