# What the dashboard covers

✅ = working end-to-end (page → API → MongoDB → bot where Discord is involved). Everything below was built; the "limits" column says exactly what each page does and doesn't do.

| Spec item | Page | Notes / limits |
|---|---|---|
| Server overview | Overview | Live counts, bot-online indicator, recent audit entries, manual "Sync with Discord" |
| Server configuration | Settings | The same keys `/setup` uses (roles, channels, timezone, activity tracking) |
| Teams & rosters | Teams, Rosters | Roster rules: one row per player per team, unique jersey numbers. Also `/roster` in Discord |
| Team records | Team Records | +Win/+Loss/+Tie, set, reset, full history; live leaderboards in Discord refresh automatically |
| Competitions & divisions | Competitions, Divisions | Teams pick from them; can't delete one that teams still use |
| Match history | Match History | Optional "update team records" (adds W/L/T to both teams, logged). Also `/match` in Discord |
| Statistics voice channels | Statistics Channels | Create/edit/remove; the bot creates the locked, visible voice channels |
| Moderation | Moderation | Lock/unlock channels (original permission restored) + searchable log. **Kick/ban are not exposed on the web by design** (use Discord's own tools) |
| Polls, poll archives | Polls, Poll Archive | Create/end/delete; voting happens on the Discord message |
| Giveaways | Giveaways | Create, end, reroll, role + account-age requirements |
| Countdowns & events | Countdowns, Events | Countdowns publish a live-ticking message; events are a calendar list with a 24 h notification |
| Leaderboards | Leaderboard | Sort by wins / win % / fewest losses, filter by competition & division |
| Member activity / statistics | Member Activity, Member Stats | Activity tracking is opt-in per server; member stats use hourly snapshots |
| Custom embeds / announcements | Announcements & Embeds | Full embed (author, image, thumbnail, footer, fields, colour, role ping) |
| Role panels | Role Panels | Buttons or dropdown, role limit; refuses roles with moderator/admin permissions |
| Backups | Backups | Roles/channels/permissions/basic settings. Restore: "add missing" (safe) or full (owner only, typed confirmation) |
| Suggestions | Suggestions | Approve/deny with a response; the author is DM'd |
| Scheduled messages, reminders | Scheduled Messages, Reminders | Once / daily / weekly; sent by the bot; no `@everyone` pings |
| Automation workflows | Workflows | Triggers: member joins / leaves. Actions: send message, add role, DM member |
| Webhooks | Webhooks | URLs encrypted, never shown again; "Send message" posts through the webhook |
| Player profiles, tryouts, scouting, awards, hall of fame | Players section | Full CRUD. Awards share data with `/award` + `/awardcheck` |
| Verification | Verification | Posts a button message that gives a (non-privileged) role |
| Staff management, shifts, reports | Staff section | Dashboard Access grants; shifts and reports are records |
| Appeals | Appeals | Approve/deny, optional **unban in Discord** |
| Themes, timezone | Preferences | Per-user accent colour + display timezone; server timezone in Settings |
| Poll archives, data exports | Poll Archive, Data Export | JSON or zipped CSV of any collection (secrets excluded) |
| Audit log | Audit Log | Every dashboard change |

## Deliberately not built
* **Command aliases.** Discord slash commands can't have user-defined aliases; the only way is prefix/text commands, which need the privileged *Message Content* intent the bot intentionally doesn't use. Rather than ship a button that does nothing, it is left out.
* **Arbitrary automation (if-this-then-that).** Workflows are limited to the join/leave triggers and three actions above.
* **Kick/ban/timeout from the web.** Available only through Discord commands.
* **Member search.** Discord doesn't let a bot's website list members without the token, so user fields take a Discord ID (right-click user → Copy User ID) or are filled by the bot.
