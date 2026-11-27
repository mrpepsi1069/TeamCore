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
| `/database` | `__init__.py` (the bot's Motor layer + migrations), `teams.py` (teams/rosters/competitions/matches — used by **both** bot and backend), `economy.py` (Cash accounts, the transaction ledger, casino games — also used by both), `schema.py` (indexes) |
| `/shared` | Settings, access levels, the website→bot job contract, resource field specs, security helpers |

## How a dashboard action works
1. The browser sends `Authorization: Bearer <session token>` — never a bot token.
2. `backend/deps.py` verifies the session, confirms the user is in that server (Discord's guild list), that the bot is in it, and computes their level (see below).
3. The request is validated against `shared/resources.py` (unknown fields are dropped — a client can't set `guild_id`).
4. MongoDB is updated, always scoped by `guild_id`.
5. If Discord must change (post a poll, create a channel…), a **job** is written to `bot_jobs`. The bot (the only holder of the token) claims it atomically, runs it, and writes the result; the API waits up to 10 s and returns it. If the bot is offline the job stays queued and the UI says so. A few jobs are fire-and-forget (`wait=0`) because the database write is the source of truth — a suggestion decision is stored first and only then does the bot edit the Discord message, so it stands even with the bot offline.
6. An `audit_logs` entry is written.

The bot runs perfectly without the website: the bridge cog is additive and every job type is a fixed allow-list (`shared/jobs.py`).

### Members: the one place that needs the bot to read Discord
Dashboard access, team, position, jersey and roster status are plain writes to `dashboard_permissions` / `team_members` — the API does those itself. Two things it cannot do without the token:

* **`members_sync`** (staff) — the bot publishes Discord's member list into `members` (bulk upsert, stale rows removed) and records the sync time in `member_sync`. The Members page shows what was synced last.
* **`member_roles`** (admin) — the API diffs the requested role set against `members.role_ids`, rejects ids that are unknown, `@everyone` or managed by Discord, and asks the bot to add/remove the rest; the bot applies the same role-order checks as its own commands, and `members.role_ids` is updated only if it succeeded.

So the directory is as fresh as the last sync, and a role edit for someone missing from the synced roster is refused — without their current roles the API can't compute a safe diff.

### Gametimes and time polls
The dashboard writes `gametime_create` / `times_create`; the bot builds **the same embed and buttons the slash commands use** (both cogs export their embed builder, so the two paths can't drift) and posts into `guild_config.gametime_channel`. Either path then records the message link through `bot/utils/schedule.py`, so the page's recent list shows dashboard and Discord polls together. Gametime DMs to the pinged role run as a background task after the job has answered — one message per member is far slower than the 10 s the browser waits.

## Access levels
| Level | Who |
|---|---|
| `admin` | Server owner, or Discord **Administrator** / **Manage Server** (decided from Discord's own permission bits at login) |
| `manager`, `staff`, `viewer` | Granted by an admin on **Members** → Manage (`dashboard_permissions`). Team and Discord-role assignment live in the same dialog |

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
| leagues | `leagues` (the bot's `/league` data: name, abbreviation, signup link, role) | | members / member_sync | `members` (bot-published roster snapshot), `member_sync` (last sync per server) |

Teams are not linked to a league by an id: a team belongs to a league when its `competition` / `competition_lower` matches the league's name or abbreviation. That keeps teams created in Discord grouped correctly, and it is why a league can't be deleted while any team still matches it.

A team is also a Discord role, whichever side creates it. From the dashboard, the picked colour is stored on the team document and the bot is asked (`team_role_create`) to create or re-colour `@Team name`; `/record create` makes the role itself, inline, in the default colour (`DEFAULT_TEAM_COLOR` in `shared/resources.py`, the same default the dashboard form offers). Either way the role id is written back to `teams.role_id`, and the role is best-effort: a bot without **Manage Roles** still creates the team, just without the role.

The sync afterwards never blocks a save: editing fires the job only when the name or colour changed (which is also how a team created before the colour field gets its role), and deleting fires `team_role_delete` so no orphan role is left behind. A bot that is offline just leaves the job queued.

Internal: `sessions` (hashed tokens, TTL), `oauth_states` (TTL), `bot_jobs` (TTL 24 h), `counters`, `meta` (schema version), `schedule_polls` (gametimes/times posted from the dashboard, with their message links).

Cash: `economy_accounts` (one balance per member per server, opened with $5,000), `economy_transactions` (append-only ledger, `_id` = the unique transaction id, so a payment or casino payout can only ever be applied once) and `casino_games` (pending/won/lost/expired games). The bot moves Cash; the backend only reads balances (`database/economy.py` works with both database handles).

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
