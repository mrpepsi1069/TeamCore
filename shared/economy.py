"""shared/economy.py — Cash: the one place the currency is named, formatted and played.

Both the bot and the website read the numbers from this module, so "$5,000 Cash" means the
same thing everywhere. No database, Discord or web-framework imports here — just rules.
"""

import random

CURRENCY_NAME = "Cash"
CURRENCY_SYMBOL = "$"
STARTING_CASH = 5000          # what every member has the first time their balance is needed
MAX_CASH = 1_000_000_000      # sanity cap for a single entry / payment (not a balance cap)
CASINO_GAME_TIMEOUT = 90      # seconds a casino game waits for "Play" before it is refunded
DAILY_BASE = 500              # what /daily pays on the first day of a streak
DAILY_STREAK_BONUS = 100      # extra Cash for each consecutive day claimed
DAILY_MAX_STREAK = 7          # the bonus stops growing here (day 7 pays the most)
DAILY_MIN_ACCOUNT_AGE_DAYS = 7  # a Discord account must be this old to claim (stops fresh alts)
DAILY_MIN_MEMBER_HOURS = 24     # and must have been in this server this long


def format_cash(amount) -> str:
    """The only Cash format: $1,000 Cash (zero is $0 Cash)."""
    return f"{CURRENCY_SYMBOL}{int(amount):,} {CURRENCY_NAME}"


def check_amount(value) -> int:
    """A positive, whole number of Cash — the only amounts that may ever move."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{CURRENCY_NAME} amounts are whole numbers.")
    if value <= 0:
        raise ValueError(f"An amount must be more than {format_cash(0)}.")
    if value > MAX_CASH:
        raise ValueError(f"That is more than the {format_cash(MAX_CASH)} limit.")
    return value


def check_balance(value) -> int:
    """A whole Cash balance, exactly 0 allowed — used when an owner sets a balance directly."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{CURRENCY_NAME} balances are whole numbers.")
    if value < 0:
        raise ValueError(f"A balance can't be negative.")
    if value > MAX_CASH:
        raise ValueError(f"That is more than the {format_cash(MAX_CASH)} limit.")
    return value


def payout(entry: int, payout_bps: int) -> int:
    """A win pays `payout_bps` basis points of the entry (19000 = 1.9x), never less than 1."""
    return max(1, int(entry) * int(payout_bps) // 10000)


def daily_reward(streak: int) -> int:
    """What one `/daily` claim pays on a given streak day (day 1 = the base amount)."""
    day = max(1, min(int(streak), DAILY_MAX_STREAK))
    return DAILY_BASE + DAILY_STREAK_BONUS * (day - 1)


def theoretical_rtp(game: str) -> float:
    """A game's long-run payback: its win chance times the payout, as a fraction (0.95 = 95%)."""
    spec = CASINO_GAMES[game]
    return spec["win_rate"] * spec["payout_bps"] / 10000


SLOT_SYMBOLS = ("🍒", "🔔", "7️⃣")
WHEEL_SEGMENTS = ("💎", "🍒", "🔔", "🍋", "⭐")
CARD_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
HIGH_CARD_WINS = frozenset(("J", "Q", "K", "A"))

# Every game pays back less than the entry on average (RTP 87-95%), so the house keeps a
# margin; the numbers are the "configured reward" a win credits, in basis points.
CASINO_GAMES = {
    "coinflip": {
        "label": "Coin Flip",
        "emoji": "🪙",
        "odds": "50% chance · pays 1.9×",
        "payout_bps": 19000,
        "win_rate": 0.5,
    },
    "dice": {
        "label": "Dice",
        "emoji": "🎲",
        "odds": "Roll 5 or 6 (2 in 6) · pays 2.75×",
        "payout_bps": 27500,
        "win_rate": 2 / 6,
    },
    "slots": {
        "label": "Slots",
        "emoji": "🎰",
        "odds": "Three of a kind (1 in 9) · pays 8×",
        "payout_bps": 80000,
        "win_rate": 1 / 9,
    },
    "wheel": {
        "label": "Wheel",
        "emoji": "🎡",
        "odds": "Lands on 💎 (1 in 5) · pays 4.5×",
        "payout_bps": 45000,
        "win_rate": 1 / 5,
    },
    "highcard": {
        "label": "High Card",
        "emoji": "🃏",
        "odds": "Draw J, Q, K or A · pays 2.9×",
        "payout_bps": 29000,
        "win_rate": 4 / 13,
    },
    "sevens": {
        "label": "Lucky Sevens",
        "emoji": "7️⃣",
        "odds": "Two dice total 7 · pays 5.25×",
        "payout_bps": 52500,
        "win_rate": 6 / 36,
    },
}


def play(game: str, entry: int, rng=None) -> dict:
    """Play one round. Pure except for the RNG, so the outcome is decided once and can be tested.

    Returns {"won": bool, "detail": str, "reward": int}; reward is 0 on a loss.
    """
    spec = CASINO_GAMES.get(game)
    if spec is None:
        raise ValueError(f"Unknown casino game: {game}")
    entry = check_amount(entry)
    rng = rng or random

    if game == "coinflip":
        won = rng.random() < 0.5
        detail = "Heads" if won else "Tails"
    elif game == "dice":
        roll = rng.randint(1, 6)
        won = roll >= 5
        detail = f"rolled a {roll}"
    elif game == "wheel":
        segment = rng.choice(WHEEL_SEGMENTS)
        won = segment == WHEEL_SEGMENTS[0]
        detail = f"landed on {segment}"
    elif game == "highcard":
        card = rng.choice(CARD_RANKS)
        won = card in HIGH_CARD_WINS
        detail = f"drew a {card}"
    elif game == "sevens":
        first, second = rng.randint(1, 6), rng.randint(1, 6)
        won = first + second == 7
        detail = f"rolled {first} + {second} = {first + second}"
    else:  # slots
        reels = [rng.choice(SLOT_SYMBOLS) for _ in range(3)]
        won = reels[0] == reels[1] == reels[2]
        detail = " ".join(reels)

    return {"won": won, "detail": detail, "reward": payout(entry, spec["payout_bps"]) if won else 0}
