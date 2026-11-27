# 🏈 TeamCore Bot — discord.py Edition

A Discord bot for league teams, built in Python using `discord.py`.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Your bot token from the Discord Developer Portal |
| `DATABASE_URL` | MongoDB connection string |
| `PORT` | HTTP health-check port (default: 3000) |

See `.env.example` for the optional overrides (owner ID, support link, etc).

### 3. Run the bot
```bash
python bot.py
```

Slash commands are synced automatically on startup. `bot.py` is the single entry point — it loads every cog, exposes the `/health` and `/api/stats` HTTP endpoints used for uptime monitoring, and wires up all persistent button/component handling.

---

## Commands

| Command | Description | Permission |
|---------|-------------|------------|
| `/ping` | Check latency | Everyone |
| `/help` | Show command list | Everyone |
| `/invite` | Get bot invite link | Everyone |
| `/bold <text>` | Convert text to bold Unicode | Everyone |
| `/randomnumber <min> <max>` | Random number | Everyone |
| `/flipcoin` | Flip a coin | Everyone |
| `/fban` / `/fkick` | Fake ban/kick (joke) | Everyone |
| `/awardcheck` | View awards & rings | Everyone |
| `/leaderboard` | Top players by rings/awards | Everyone |
| `/contract view` | View your contract | Everyone |
| `/suggest` | Submit a suggestion | Everyone |
| `/premium` | View premium pricing | Everyone |
| `/balance [member]` | Show Cash (yours or another member's) | Everyone |
| `/pay <member> <amount>` | Send Cash to another member | Everyone |
| `/casino <game> <entry>` | Play Coin Flip, Dice, Slots, Wheel, High Card or Lucky Sevens | Everyone |
| `/cash add\|remove\|set <member> <amount>` | Add, remove or set one member's Cash | Bot owner |
| `/cash add-all\|remove-all\|set-all <amount>` | The same for every member in the server | Bot owner |
| `/matchmaking` | Open the Core Match Making board | Everyone |
| `/matchmaking-queue` | View who's currently looking for a match | Everyone |
| `/matchmaking-cancel` | Cancel your pending matchmaking request(s) | Everyone |
| `/mutevc` / `/unmutevc` | Mute/unmute voice channel | Staff |
| `/dmmembers` | DM members with selected roles | Coach |
| `/activitycheck` | Create activity check | Manager |
| `/gametime` | Game time attendance poll | Coach |
| `/times` | Multi-slot time poll | Manager |
| `/award` / `/award-remove` | Give or remove an award | Manager |
| `/announce` | Post to the announcement channel | Manager |
| `/say` | Send a message as the bot | Manager |
| `/ring-add` / `/ring-remove` | Grant or remove championship rings | Manager |
| `/lineup ...` | Lineup management | Manager |
| `/depthchart ...` | Depth chart management | Coach |
| `/league ...` | League management | Coach |
| `/contract ...` | Player contract management | Coach |
| `/role` / `/unrole` | Assign/remove roles | Admin |
| `/kick` / `/ban` / `/timeout` | Moderation | Admin |
| `/setup` | Configure bot channels & roles | Server Owner |
| `/templateuse` | Apply a server template | Server Owner |
| `/botstats` | View bot statistics | Bot Owner |
| `/setpremium` | Grant/revoke Premium for a server | Bot Owner |
| `/guilds` | View all servers | Bot Owner |
| `/join` | Get an invite for a server the bot is in | Bot Owner |
| `/botkick` | Leave a server | Bot Owner |
| `/globalannouncement` | Send a message to every server | Bot Owner |
| `/disableglobalmessages` | Opt a server out of owner pings | Server Owner |

---

## Matchmaking

`/matchmaking` creates (or reuses) a **Core Match Making** channel and posts a board with two game buttons (Football Fusion 3 / Ultimate Football). Clicking a game shows an ephemeral menu — visible only to the person who clicked — to choose a match type (`2v2`–`11v11`, or `1v1`–`5v5` for Basketball) and a time (type `now` for available immediately). Once submitted, the bot checks (across all servers) for someone else already waiting on the same game and match type:

- **Match found** → both players are DMed with each other's info and the request is removed from the queue.
- **No match yet** → the request is added to the queue, posted on the board, and the player is DMed automatically as soon as someone else queues for the same game and match type.

The channel also gets a **Matchmaking Pings** message: reacting with ⚽ / 🏈 / 🏀 gives you the `Matchmaking Ping - <game>` role (created automatically), and un-reacting removes it. Every request posted for a game pings that game's role.

Matchmaking is pooled across every server the bot is in; requests are auto-removed after 24 hours.

---

## Cash

Every member starts with **$5,000 Cash** the first time their balance is needed. Cash is per server,
there is no bank or wallet, and a balance can reach exactly $0 but never go below it. Every amount is
shown the same way everywhere: `$1,000 Cash` (`/balance`, `/pay`, casino results and the dashboard).

* `/balance [member]` — shows a balance.
* `/pay <member> <amount>` — sends Cash. You can send your whole balance, but not more; zero,
  negative, self- and bot-payments are refused. Success reads `Successfully sent $400 Cash.` and
  shows both updated balances.
* `/casino <game> <entry>` — Coin Flip (50%, pays 1.9×), Dice (roll 5–6, pays 2.75×), Slots
  (three of a kind, pays 8×), Wheel (💎, 1 in 5, pays 4.5×), High Card (J/Q/K/A, pays 2.9×) or
  Lucky Sevens (two dice total 7, pays 5.25×). The entry is taken when the game opens, the **Play**
  button settles it exactly once (a win credits the configured reward, a loss pays nothing back), and
  a game nobody plays expires after 90 seconds with its entry refunded — including after a bot
  restart, because a sweep refunds any expired game.

`/leaderboard cash` is the one global board: Cash is stored per server, so it adds each member's
balances across every server the bot is in and says in how many of them they hold Cash. Names come
from the bot's known users, with a mention as the fallback.

**Bot-owner tools.** `/cash add`, `/cash remove` and `/cash set` change one member's Cash, and
`/cash add-all`, `/cash remove-all` and `/cash set-all` do the same for every non-bot member in the
server. They are limited to the owner's ID (`BOT_OWNER_ID` or `OWNER_ID` in the bot's `.env`) and
always answer privately.
Removing never overdraws: members holding less than the amount are skipped and counted in the reply.
Every edit is a ledger row tagged with the owner and a batch id, so an add-all run can be traced.

All balance changes are single conditional MongoDB updates: a debit only applies while
`balance >= amount`, and each transaction's ledger row is claimed by its unique id before money
moves. That is what makes simultaneous commands unable to spend the same Cash twice, overdraw an
account, or pay a casino win twice. Accounts live in `economy_accounts`, every movement is in
`economy_transactions`, and pending games in `casino_games`.

---

## Project Structure

```
TeamCore/
├── bot.py              # Main (only) entry point
├── database.py         # MongoDB wrapper (Motor)
├── config.py            # Constants & settings
├── requirements.txt
├── .env.example
├── cogs/                # Slash commands (one per file)
│   ├── ping.py
│   ├── help.py
│   ├── gametime.py
│   ├── league.py
│   ├── contract.py
│   ├── matchmaking.py
│   └── ...
└── utils/
    ├── embeds.py        # Embed builders
    ├── permissions.py   # Role-based auth
    ├── validation.py    # Input validation
    └── premium.py       # Premium checks
```

## Server tools (v2)

All of these are per-server, stored in MongoDB (collections are keyed by `guild_id`), and survive restarts — polls, giveaways and
countdowns are re-checked from the database every 20 seconds, and their buttons are handled by persistent listeners.

| Command | What it does | Who |
|---|---|---|
| `/serverinfo` | Server details | Everyone |
| `/record create/delete/edit/rename/reset/win/loss/tie` | Multi-team records (competitions, logos, history) | Manager |
| `/record view/list/history` | View records | Everyone |
| `/leaderboard teams/rings/awards` | Paginated rankings | Everyone |
| `/leaderboard cash` | The richest members across every server the bot is in | Everyone |
| `/leaderboard post` | Live leaderboard that edits itself when records change | Manager |
| `/stats setup/add/remove/config/update` | Auto-updating, non-joinable stat voice channels (members, boosts, team records, custom) | Admin |
| `/lock`, `/unlock` | Lock a channel (or `server_wide`); the original @everyone overwrite is restored exactly | Manage Channels / Manager |
| `/poll create/end/results` | Polls: anonymous, multi-choice, auto-close | Staff |
| `/giveaway create/end/reroll/list/history` | Giveaways with role / account-age requirements | Manager |
| `/countdown create/edit/delete/list` | Live countdowns with timezone + end ping | Staff |
| `/activity top/user/config/leaderboard` | Message activity (**off until enabled**), daily/weekly/all-time | Everyone / Manager |
| `/memberstats view/history/channel` | Joins, leaves, growth, history chart | Everyone / Manager |
| `/suggestion submit/approve/deny/channel/history` | Suggestions with voting and author DMs | Everyone / Staff |
| `/announcement send/channel` | Announcement with preview | Manager |
| `/embed create/edit` | Interactive embed builder | Manager |
| `/rolepanel create/add/remove/delete/refresh/list` | Button or dropdown self-assign roles (never moderator-level roles) | Manager |
| `/backup create/list/view/restore/delete` | Save/restore roles, channels, permissions, basic settings | Admin |

Notes
- `/backup` only stores what Discord's API allows: not messages, members, their role assignments, emojis, webhooks or bans.
  "Full" restore deletes existing channels/roles and needs a typed `RESTORE` confirmation; "add missing" is non-destructive.
- Win % counts a tie as half a win.
- Stats channel names are renamed only when the text changes (Discord limits renames to 2 per 10 minutes per channel).
- The database schema is versioned (`meta` collection); new migrations are appended to `_MIGRATIONS` in `database.py`.
- `/countdown` needs timezone data; on Windows that comes from the `tzdata` package in `requirements.txt`.
