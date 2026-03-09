# 🏈 LockerRoom Bot — discord.py Edition

A Discord bot for league teams, fully rewritten in Python using `discord.py`.

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
| `CLIENT_ID` | Your bot's application/client ID |
| `PORT` | HTTP health-check port (default: 3000) |

### 3. Run the bot
```bash
python bot.py
```

Slash commands are synced automatically on startup.

---

## Commands

| Command | Description | Permission |
|---------|-------------|------------|
| `/ping` | Check latency | Everyone |
| `/help` | Show command list | Everyone |
| `/invite` | Get bot invite link | Everyone |
| `/bold <text>` | Convert text to bold Unicode | Everyone |
| `/randomnumber <min> <max>` | Random number | Everyone |
| `/fban` / `/fkick` | Fake ban/kick (joke) | Everyone |
| `/awardcheck` | View awards & rings | Everyone |
| `/suggest` | Submit a suggestion | Everyone |
| `/premium` | View premium pricing | Everyone |
| `/mutevc` / `/unmutevc` | Mute/unmute voice channel | Staff |
| `/dmmembers` | DM all members with a role | Coach |
| `/activitycheck` | Create activity check | Manager |
| `/gametime` | Game time attendance poll | Coach |
| `/times` | Multi-slot time poll | Manager |
| `/award` | Give a player an award | Manager |
| `/ring-add` | Grant championship rings | Manager |
| `/lineup ...` | Lineup management | Manager |
| `/depthchart ...` | Depth chart management | Coach |
| `/league ...` | League management | Coach |
| `/contract ...` | Player contract management | Coach |
| `/role` / `/unrole` | Assign/remove roles | Admin |
| `/kick` / `/ban` / `/timeout` | Moderation | Admin |
| `/setup` | Configure bot channels & roles | Admin |
| `/botstats` | View bot statistics | Owner |
| `/guilds` | View all servers | Owner |

---

## Project Structure

```
LockerRoom-py/
├── bot.py              # Main entry point
├── database.py         # MongoDB wrapper (Motor)
├── config.py           # Constants & settings
├── requirements.txt
├── .env.example
├── cogs/               # Slash commands (one per file)
│   ├── ping.py
│   ├── help.py
│   ├── gametime.py
│   ├── league.py
│   ├── contract.py
│   └── ...
└── utils/
    ├── embeds.py       # Embed builders
    ├── permissions.py  # Role-based auth
    ├── validation.py   # Input validation
    └── premium.py      # Premium checks
```
