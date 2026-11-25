# TeamCore — Architecture

```
 Browser (static site)              FastAPI backend                     MongoDB Atlas (free)               Discord bot
 frontend/ ──Bearer token──▶  backend/  ──reads/writes──▶  one shared database  ◀──reads/writes──  bot/
 (GitHub Pages / any host)     (no bot token)  └─ writes `bot_jobs` ─────────────▶  change stream wakes the bot ─▶ does the Discord action
                                                 ◀─ polls the job result (≤10 s) ◀── bot writes status/result
```

| Folder | What it is |
|---|---|
| `/bot` | The discord.py bot (all existing commands preserved) + `cogs/dashboard_bridge.py` (job worker, server cache) and `cogs/automation.py` (scheduled messages, reminders, workflows) |
| `/backend` | FastAPI app: Discord OAuth2, sessions, per-server permission checks, generic CRUD, bot bridge |
| `/frontend` | Static site: `index.html` (landing), `dashboard/` (the app). No build step, no secrets |
| `/database` | `__init__.py` (the bot's Motor layer + migrations), `teams.py` (teams/rosters/competitions/matches — used by **both** bot and backend), `schema.py` (indexes) |
| `/shared` | Settings, access levels, the website→bot job contract, resource field specs, security helpers |

## How a dashboard action works
1. The browser sends `Authorization: Bearer <session token>` — never a bot token.
2. `backend/deps.py` verifies the session, confirms the user is in that server (Discord's guild list), that the bot is in it, and computes their level (see below).
3. The request is validated against `shared/resources.py` (unknown fields are dropped — a client can't set `guild_id`).
4. MongoDB is updated, always scoped by `guild_id`.
5. If Discord must change (post a poll, create a channel…), a **job** is written to `bot_jobs`. The bot (the only holder of the token) claims it atomically, runs it, and writes the result; the API waits up to 10 s and returns it. If the bot is offline the job stays queued and the UI says so.
6. An `audit_logs` entry is written.

The bot runs perfectly without the website: the bridge cog is additive and every job type is a fixed allow-list (`shared/jobs.py`).

## Access levels
| Level | Who |
|---|---|
| `admin` | Server owner, or Discord **Administrator** / **Manage Server** (decided from Discord's own permission bits at login) |
| `manager`, `staff`, `viewer` | Granted by an admin on **Staff → Dashboard Access** (`dashboard_permissions`) |

Each resource declares the level needed to read / write / delete (`shared/resources.py`), and the backend enforces it on every request; hiding buttons in the UI is only cosmetic.

## Collection map (spec name → real collection)
The bot's existing collections were kept (renaming would orphan live data). New ones follow your list.

| Spec | Collection | | Spec | Collection |
|---|---|---|---|---|
| users | `users` | | polls / poll_votes | `polls`, `poll_votes` |
| servers | `servers` (bot publishes name/channels/roles) | | giveaways / entries | `giveaways`, `giveaway_entries` |
| server_settings | `guild_config` (shared with `/setup`) | | suggestions | `suggestions`, `suggestion_votes` |
| dashboard_permissions | `dashboard_permissions` | | moderation_logs | `moderation_logs` |
| teams, team_records | `teams`, `team_history` | | member_activity | `activity_daily` |
| team_members | `team_members` | | role_panels | `role_panels` |
| competitions / divisions | `competitions`, `divisions` | | announcements | `announcements` |
| matches | `matches` | | scheduled_messages / reminders / workflows | same names |
| statistics_channels | `stats_channels` | | player_profiles, tryouts, scouting_reports, staff_shifts, staff_reports, appeals, awards, audit_logs | same names (+ `hall_of_fame`, `webhooks`, `backups`, `countdowns`, `events`) |

Internal: `sessions` (hashed tokens, TTL), `oauth_states` (TTL), `bot_jobs` (TTL 24 h), `counters`, `meta` (schema version).

Every server-specific document carries `guild_id`; all queries filter on it and every list has a `(guild_id, …)` index (`database/schema.py`).

## Free-tier friendliness (MongoDB Atlas M0)
* The bot is woken by a **change stream**, not polling (falls back to a 10-second poll if change streams are unavailable).
* Bot writes: one heartbeat/minute; server cache only on change (debounced 20 s); activity counters buffered and flushed every 30 s; scheduled-message loop is one indexed query per 30 s.
* Backend: pool of ≤10 connections, per-client rate limit (180 req/min), bounded page sizes, exports capped at 20 000 rows per collection, TTL indexes delete sessions/jobs automatically.
* The dashboard polls only the notification bell (once every 2 minutes).

## Security summary
* Discord OAuth2 with one-time `state` (login CSRF), tokens delivered in the URL **fragment**, only a SHA-256 hash of the session token is stored.
* Bearer-token auth (nothing is sent automatically by the browser ⇒ classic CSRF doesn't apply) **plus** an `Origin` check on every write and a strict CORS allow-list (never `*`).
* Discord access tokens and webhook URLs are encrypted at rest (Fernet, `DATA_ENCRYPTION_KEY`); webhook URLs are never returned to the browser.
* Channel/role IDs are checked against the server's own cache, and the bot re-checks `channel.guild == job.guild` — a request can't target another server.
* Role panels, verification and workflows refuse roles with moderator/admin permissions (checked at save time **and** at click time).
* Frontend builds all DOM with `textContent` (no `innerHTML`), has a strict CSP, and contains no secrets.
* The backend never reads `DISCORD_TOKEN`.
