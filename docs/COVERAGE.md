# What the dashboard covers

✅ = working end-to-end (page → API → MongoDB → bot where Discord is involved). Everything below was built; the "limits" column says exactly what each page does and doesn't do.

The navigation is grouped: **Overview**, then **Club · Community · Scheduling · Players · Automation · Insights · Server**, then **Preferences**. Pages that overlapped were merged into tabs, and old links still land on the right one:

* **Leagues & results** (nav entry: *Competition*) — Leagues, Teams, Rosters, Matches, Standings, Competitions, Divisions. `teams`, `records`, `team_members`, `matches`, `leaderboard`, `competitions` and `divisions` deep links all open the matching tab.
* **Polls** — Active and Archive tabs; the old `poll_archive` link opens Archive.
* **Scheduling** — gametimes and time-option polls (tabs) sit in one sidebar group together with events, countdowns, scheduled messages and reminders.

| Spec item | Page | Notes / limits |
|---|---|---|
| Server overview | Overview | Live counts, bot-online indicator, recent audit entries, manual "Sync with Discord" |
| Server configuration | Settings | The same keys `/setup` uses (roles, channels, timezone, activity tracking) |
| Leagues | Leagues & results → Leagues | A league is a row in the bot's own `leagues` collection (`/league`): name, abbreviation, signup link, league role. A team belongs to a league when its **League / competition** value matches the league's name or abbreviation, so teams created in Discord group correctly. Can't delete a league while teams still belong to it |
| Teams & records | Leagues & results → Teams | +Win/+Loss/+Tie, "Set…", reset and full history inline, with teams grouped under their league. **History** is a timeline: besides record changes it takes dated notes (what happened, on the day it happened), which `/record history` shows too. Roster rules: one row per player per team, unique jersey numbers. Creating a team also creates the Discord role named after it (the bot needs **Manage Roles**), and the role id is stored on the team: from this page it uses the colour picked in the form, and `/record create` in Discord creates it too, in the default colour. Renaming or re-colouring the team updates that role, deleting the team removes it, and saving an older team gives it one. Also `/roster` in Discord |
| Rosters | Leagues & results → Rosters | **New roster** builds a whole team in one go: pick the team, then enter a Discord ID, username and position per player (blank rows are ignored), up to 25 players per team — backups included. One row per player per team, unique jersey numbers. Players pick their team from the same list members do |
| Match history | Leagues & results → Matches | Pick both teams, or leave the away team empty and type the opponent's name when they aren't in the dashboard (the record update then only touches your team). Optional "update team records" adds W/L/T and is logged. Also `/match` in Discord |
| Standings | Leagues & results → Standings | Sort by wins / win % / fewest losses, filter by league & division |
| Competitions & divisions | Leagues & results → Competitions, Divisions | Teams pick from them. A competition can be renamed, which renames it on its divisions and teams; a division can be renamed or moved to another competition, and the teams in it move with it. Neither can be deleted while teams still use it |
| Member directory | Members | Searchable list of everyone the dashboard knows (access grants, roster rows and the last synced member list) with access level, team, position, jersey and roster status. **Sync members** asks the bot to publish Discord's member list; **Discord roles** are diffed and applied by the bot. Staff can view and sync, managers edit team/roster fields, only admins change access levels or roles |
| Statistics voice channels | Statistics Channels | Create/edit/remove; the bot creates the locked, visible voice channels. Types: members, humans, bots, boosts, one team's record, **overall team record** (every team's wins/losses/ties added up across all leagues, with win %), **championship rings** (server total, plus distinct leagues), and custom. Templates take `{value} {name} {wins} {losses} {ties} {record} {pct} {teams} {rings} {leagues}` |
| Moderation | Moderation | Lock/unlock channels (original permission restored) + searchable log. **Kick/ban are not exposed on the web by design** (use Discord's own tools) |
| Polls | Polls (Active / Archive tabs) | Create/end/delete; voting happens on the Discord message, results and closed polls are read back here |
| Gametimes & time polls | Scheduling → Gametime, Time options | Posted by the bot to the server's **gametime channel**, exactly like `/gametime` (Can make / Can't make / Unsure buttons, optional DMs to the pinged role) and `/times` (2–6 options, one button each). Staff post gametimes, managers post time polls. The recent list covers polls created here **and** with the slash commands, each with a jump link |
| Giveaways | Giveaways | Create, end, reroll, role + account-age requirements |
| Countdowns & events | Countdowns, Events | Countdowns publish a live-ticking message; events are a calendar list with a 24 h notification |
| Member activity / growth | Member activity, Member growth | Activity tracking is opt-in per server; member growth uses hourly snapshots |
| Custom embeds / announcements | Announcements & Embeds | Full embed (author, image, thumbnail, footer, fields, colour, role ping) |
| Role panels | Role Panels | Buttons or dropdown, role limit; refuses roles with moderator/admin permissions |
| Backups | Backups | Roles/channels/permissions/basic settings. Restore: "add missing" (safe) or full (owner only, typed confirmation) |
| Suggestions | Suggestions | Approve/deny with a response. The decision is saved immediately, then the bot updates the message and DMs the author — if the bot is offline the decision still stands |
| Scheduled messages, reminders | Scheduled Messages, Reminders | Once / daily / weekly; sent by the bot; no `@everyone` pings |
| Automation workflows | Workflows | Triggers: member joins / leaves. Actions: send message, add role, DM member |
| Webhooks | Webhooks | URLs encrypted, never shown again; "Send message" posts through the webhook |
| Player profiles, tryouts, scouting, awards, hall of fame | Players section | Full CRUD. Awards share data with `/award` + `/awardcheck` |
| Verification | Verification | Posts a button message that gives a (non-privileged) role |
| Staff shifts, reports | Staff shifts, Staff reports | Records only — who worked when, and staff reports against members. Access itself is granted on **Members** |
| Appeals | Appeals | Approve/deny, optional **unban in Discord** |
| Themes, timezone | Preferences | Per-user accent colour + display timezone; server timezone in Settings |
| Data exports | Data export | JSON or zipped CSV of any collection (secrets excluded), capped at 20 000 rows per collection |
| Audit log | Audit Log | Every dashboard change |

## Deliberately not built
* **Command aliases.** Discord slash commands can't have user-defined aliases; the only way is prefix/text commands, which need the privileged *Message Content* intent the bot intentionally doesn't use. Rather than ship a button that does nothing, it is left out.
* **Arbitrary automation (if-this-then-that).** Workflows are limited to the join/leave triggers and three actions above.
* **Kick/ban/timeout from the web.** Available only through Discord commands.
* **A live member list without the bot.** Discord only gives a server's member list to a bot holding the token, so **Members** shows a snapshot the bot publishes when someone presses *Sync members* (`members_sync`). The API can't refresh it on its own, and Discord role changes need the bot online. User fields elsewhere still take a Discord ID (right-click user → Copy User ID).
