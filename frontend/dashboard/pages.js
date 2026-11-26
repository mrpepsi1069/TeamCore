// Hand-built pages (everything else is generated from specs by resource.js).
import { state, can } from "./state.js";
import { api, g } from "./api.js";
import { h, clear, spinner, toast, modal, confirmDialog, fmtDate, nameOfChannel, nameOfRole, barChart, meter, errorBox,
  setAccent, pageHead, tabPage, readImageFile, readTextFile } from "./ui.js";
import { renderResource, openForm } from "./resource.js";
import { showFusion } from "./fusion.js";

const opt = (v, l, sel) => h("option", { value: v, selected: sel || null }, l);

// Renders a page head immediately, then swaps in the loaded body (or an error).
function wrap(c, title, sub, fn, kicker) {
  clear(c).append(pageHead({ kicker, title, sub }));
  const body = h("div", {});
  c.append(body, spinner());
  const loading = c.lastChild;
  fn().then((node) => { loading.remove(); if (node) body.append(node); })
    .catch((e) => { loading.remove(); clear(body).append(errorBox(e)); });
}

// ───────── overview ─────────
export function overviewPage(c) {
  return wrap(c, state.gmeta.guild.name, "Everything happening in this server, in one place.", async () => {
    const o = await g("/overview");
    const stat = (value, label) => h("div", { class: "stat" }, h("b", {}, value ?? "—"), h("span", {}, label));
    const link = (value, label, page) => h("a", { class: "stat clickable", href: `#/g/${state.gid}/${page}` },
      h("b", {}, value ?? "—"), h("span", {}, label));
    return h("div", {},
      h("div", { class: "card tight row" },
        h("span", { class: `badge ${o.bot_online ? "open" : "failed"}` }, o.bot_online ? "Bot online" : "Bot offline"),
        h("span", { class: "badge plain" }, `Your access: ${o.level}`),
        h("span", { class: "spacer" }),
        h("span", { class: "sub" }, o.server.synced_at ? `Discord data synced ${fmtDate(o.server.synced_at)}` : "Not synced from Discord yet"),
        can("staff") ? h("button", { class: "btn small", onclick: async (e) => {
          e.target.disabled = true;
          const r = await g("/refresh", { method: "POST" }).catch((err) => ({ error: err.message }));
          toast(r.error || "Sync requested — Discord data refreshes in a moment", r.error ? "err" : "");
          e.target.disabled = false;
        } }, "Sync with Discord") : null),
      h("div", { class: "grid" },
        stat(o.server.member_count?.toLocaleString(), "Members"),
        stat(o.server.boosts, `Boosts · tier ${o.server.tier ?? 0}`),
        link(o.counts.teams, "Teams", "competition"),
        link(o.counts.matches, "Matches logged", "matches"),
        link(o.counts.open_polls, "Open polls", "polls"),
        link(o.counts.open_giveaways, "Live giveaways", "giveaways"),
        link(o.counts.pending_suggestions, "Suggestions pending", "suggestions"),
        link(o.counts.pending_appeals, "Appeals pending", "appeals"),
        link(o.counts.players, "Player profiles", "player_profiles")),
      h("div", { class: "card" }, h("h2", {}, "Recent dashboard activity"),
        o.recent.length
          ? h("div", { class: "list" }, o.recent.map((r) => h("div", { class: "list-row" },
            h("b", {}, r.user_name || "—"), h("span", {}, r.action), h("span", { class: "sub" }, r.target || ""),
            h("span", { class: "spacer" }), h("span", { class: "sub" }, fmtDate(r.created_at)))))
          : h("p", { class: "page-sub" }, "No dashboard changes logged yet.")));
  }, "Server");
}

// ───────── settings + verification ─────────
const LABELS = {
  admin_role: "Admin role", franchise_owner_role: "Franchise owner role", general_manager_role: "General manager role",
  head_coach_role: "Head coach role", assistant_coach_role: "Assistant coach role", verify_role: "Verification role",
  log_channel: "Log channel", announce_channel: "Announcements channel", gametime_channel: "Gametime channel",
  matchmaking_channel: "Matchmaking channel", contract_channel: "Contract channel", awards_channel: "Awards channel",
  league_log_channel: "League log channel", suggestion_channel: "Suggestions channel", memberstats_channel: "Member report channel",
  results_channel: "Results channel (imported matches are posted here)",
  verify_channel: "Verification channel", activity_enabled: "Track message activity",
  activity_channels: "Only track these channels (empty = all)", timezone: "Server timezone",
};

function settingsForm(keys, saved) {
  const m = state.gmeta, inputs = {};
  const row = (key, type) => {
    let el;
    const val = saved[key];
    if (type === "role") el = h("select", {}, opt("", "— not set —"), m.roles.filter((r) => r.id !== state.gid).map((r) => opt(r.id, `@${r.name}`, val === r.id)));
    else if (type === "channel") el = h("select", {}, opt("", "— not set —"), m.channels.filter((x) => ["text", "news"].includes(x.type)).map((x) => opt(x.id, `#${x.name}`, val === x.id)));
    else if (type === "channels") el = h("select", { multiple: true }, m.channels.filter((x) => ["text", "news"].includes(x.type)).map((x) => opt(x.id, `#${x.name}`, (val || []).includes(x.id))));
    else if (type === "bool") el = h("input", { type: "checkbox", checked: !!val });
    else el = h("input", { list: "dl_tz", value: val || "", placeholder: "e.g. America/New_York" });
    inputs[key] = { el, type };
    return h("div", { class: "field" }, h("label", {}, LABELS[key] || key), el);
  };
  const types = Object.fromEntries(state.settingsSpec.map((s) => [s.key, s.type]));
  const form = h("div", { class: "split" }, keys.map((k) => row(k, types[k])));
  form.append(h("datalist", { id: "dl_tz" }, m.timezones.map((z) => h("option", { value: z }))));
  const read = () => Object.fromEntries(Object.entries(inputs).map(([k, { el, type }]) =>
    [k, type === "bool" ? el.checked : type === "channels" ? [...el.selectedOptions].map((o) => o.value) : el.value]));
  return { form, read };
}

export function settingsPage(c) {
  return wrap(c, "Server settings", "The same settings as /setup in Discord — changing them here changes the bot.", async () => {
    const { settings } = await g("/settings");
    const keys = ["admin_role", "franchise_owner_role", "general_manager_role", "head_coach_role", "assistant_coach_role",
      "log_channel", "announce_channel", "gametime_channel", "matchmaking_channel", "contract_channel", "awards_channel",
      "league_log_channel", "suggestion_channel", "memberstats_channel", "results_channel", "activity_enabled", "activity_channels", "timezone"];
    const { form, read } = settingsForm(keys, settings);
    const save = h("button", { class: "btn primary", disabled: !can("admin") || null, onclick: async (e) => {
      e.target.disabled = true;
      try { await g("/settings", { method: "PUT", body: read() }); toast("Settings saved"); }
      catch (err) { toast(err.errors ? Object.entries(err.errors).map(([k, v]) => `${LABELS[k] || k}: ${v}`).join(" · ") : err.message, "err"); }
      e.target.disabled = !can("admin");
    } }, "Save settings");
    return h("div", { class: "card" }, form,
      h("div", { class: "row end" }, can("admin") ? null : h("span", { class: "sub" }, "Only admins can change settings."), save));
  }, "Configuration");
}

export function verificationPage(c) {
  return wrap(c, "Verification", "Members press one button to receive a role. That role can never carry moderator or admin permissions.", async () => {
    const { settings } = await g("/settings");
    const { form, read } = settingsForm(["verify_role", "verify_channel"], settings);
    return h("div", {},
      h("div", { class: "card" }, form, h("div", { class: "row end" },
        h("button", { class: "btn", disabled: !can("admin") || null, onclick: async () => {
          try { await g("/settings", { method: "PUT", body: read() }); toast("Saved"); }
          catch (e) { toast(Object.values(e.errors || {})[0] || e.message, "err"); }
        } }, "Save"))),
      h("div", { class: "card" }, h("h2", {}, "Publish the verification message"),
        h("p", { class: "page-sub" }, "Posts a message with a Verify button in the channel above."),
        h("div", { class: "row end" },
          h("button", { class: "btn primary", disabled: !can("manager") || null, onclick: async (e) => {
            e.target.disabled = true;
            try { const r = await g("/verification/publish", { method: "POST" }); toast(r.notice || "Verification message posted", r.queued ? "warn" : ""); }
            catch (err) { toast(err.message, "err"); }
            e.target.disabled = false;
          } }, "Post verification message"))));
  }, "Configuration");
}

// ───────── competition hub: leagues, teams, rosters, matches, standings ─────────
const lower = (v) => String(v ?? "").trim().toLowerCase();
const localDateTime = (d) => {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

function leagueMatchesTeam(league, team) {
  const key = lower(team.competition);
  return !!key && (key === lower(league.league_name) || key === lower(league.league_abbr));
}

function teamRecordRow(t, { onChanged, compact = false } = {}) {
  const act = async (body, label) => {
    try {
      await g(`/teams/${t.team_id}/record`, { method: "POST", body });
      toast(`${t.name}: ${label}`);
      onChanged?.();
    } catch (e) { toast(e.message, "err"); }
  };
  const btn = (label, onclick, cls = "") => h("button", { class: `btn small ${cls}`, disabled: !can("staff") || null, onclick }, label);
  const setRecord = () => {
    const input = (k) => h("input", { type: "number", min: 0, max: 9999, value: t[k] });
    const w = input("wins"), l = input("losses"), ti = input("ties");
    modal(`Set ${t.name}'s record`, h("div", {}, h("label", {}, "Wins"), w, h("label", {}, "Losses"), l, h("label", {}, "Ties"), ti), [
      { label: "Cancel", onClick: (x) => x() },
      { label: "Save", cls: "primary", onClick: (x) => { x(); act({ action: "set", wins: +w.value, losses: +l.value, ties: +ti.value }, "record set"); } },
    ]);
  };
  const history = async () => {
    try {
      const { items } = await g(`/teams/${t.team_id}/history`);
      const note = h("textarea", { placeholder: "What happened? A signing, a venue change, a big win…", maxLength: 500, rows: 2 });
      const when = h("input", { id: "note-when", type: "datetime-local", value: localDateTime(new Date()) });
      const err = h("div", { class: "err" });
      const add = h("button", { class: "btn primary small", type: "button" }, "Add to timeline");
      const timeline = h("div", {},
        h("h2", {}, "Timeline"),
        items.length ? h("div", { class: "list" }, items.map((x) => h("div", { class: "list-row" },
          h("b", {}, x.action === "note" ? "Note" : x.action),
          x.note ? h("span", {}, x.note) : null,
          x.after && "wins" in x.after ? h("span", { class: "record" }, `${x.after.wins}-${x.after.losses}-${x.after.ties}`) : null,
          h("span", { class: "spacer" }), h("span", { class: "sub" }, fmtDate(x.timestamp)),
          x.action === "note" ? h("div", { class: "row end" },
            h("button", { class: "btn small ghost", type: "button", onclick: () => editNote(x) }, "Edit"),
            h("button", { class: "btn small danger", type: "button", onclick: () => deleteNote(x) }, "Delete")) : null)))
          : h("p", { class: "page-sub" }, "Nothing on the timeline yet."));
      const m = modal(`${t.name} — history`, h("div", {},
        h("div", { class: "field" }, h("label", {}, "Note"), note,
          h("label", { for: "note-when" }, "When it happened"), when,
          h("div", { class: "help" }, "Saved on the timeline at the date you pick — backdate it for things that already happened."), err),
        h("div", { class: "row" }, add), timeline),
        [{ label: "Close", onClick: (x) => x() }]);
      add.addEventListener("click", async () => {
        err.textContent = "";
        if (!note.value.trim()) { err.textContent = "Write something first."; return; }
        add.disabled = true;
        try {
          await g(`/teams/${t.team_id}/history`, { method: "POST", body: { note: note.value.trim(), happened_at: when.value ? new Date(when.value).toISOString() : undefined } });
          toast("Note added to the timeline");
          m.close();
          history();
        } catch (e) {
          err.textContent = e.errors?.note || e.errors?.happened_at || e.message;
          add.disabled = false;
        }
      });
      const reload = () => { m.close(); history(); };
      const editNote = (x) => {
        const text = h("textarea", { maxLength: 500, rows: 2, value: x.note || "" });
        const at = h("input", { id: "edit-note-when", type: "datetime-local", value: localDateTime(new Date(x.timestamp)) });
        const errBox = h("div", { class: "err" });
        modal("Edit timeline note", h("div", {}, h("div", { class: "field" }, h("label", {}, "Note"), text,
          h("label", { for: "edit-note-when" }, "When it happened"), at, errBox)),
          [{ label: "Cancel", onClick: (c) => c() },
            { label: "Save note", cls: "primary", onClick: async (c, btns) => {
              errBox.textContent = "";
              if (!text.value.trim()) { errBox.textContent = "Write something first."; return; }
              btns.forEach((b) => (b.disabled = true));
              try {
                await g(`/teams/${t.team_id}/history/${x.id}`, { method: "PATCH", body: { note: text.value.trim(), happened_at: at.value ? new Date(at.value).toISOString() : undefined } });
                c(); toast("Note updated"); reload();
              } catch (e) {
                errBox.textContent = e.errors?.note || e.errors?.happened_at || e.message;
                btns.forEach((b) => (b.disabled = false));
              }
            } }], { wide: true });
      };
      const deleteNote = async (x) => {
        if (!(await confirmDialog("Delete note", "Remove this note from the timeline?", { confirmLabel: "Delete" }))) return;
        try {
          await g(`/teams/${t.team_id}/history/${x.id}`, { method: "DELETE" });
          toast("Note deleted");
          reload();
        } catch (e) { toast(e.message, "err"); }
      };
    } catch (e) { toast(e.message, "err"); }
  };
  return h("div", { class: "team-row" },
    h("div", { class: "member-cell" },
      t.logo ? h("img", { class: "avatar", src: t.logo, alt: "", referrerpolicy: "no-referrer" }) : h("span", { class: "team-mark" }, (t.name || "?").charAt(0)),
      h("div", { class: "who" }, h("b", {}, t.name),
        h("span", {}, [t.competition || "No league", t.division].filter(Boolean).join(" · ")))),
    h("div", { class: "record" },
      h("b", {}, `${t.wins}-${t.losses}-${t.ties}`), h("span", { class: "sub" }, ` · ${t.win_pct || ".000"}`)),
    compact ? h("div", { class: "team-actions" },
      h("a", { class: "btn small ghost", href: `#/g/${state.gid}/rosters` }, "Roster"))
      : h("div", { class: "team-actions" },
        btn("+ W", () => act({ action: "win" }, "+1 win")),
        btn("+ L", () => act({ action: "loss" }, "+1 loss")),
        btn("+ T", () => act({ action: "tie" }, "+1 tie")),
        btn("Set", setRecord),
        btn("History", history),
        can("staff") && state.specs.teams?.update ? h("button", { class: "btn small ghost", onclick: () => openForm(state.specs.teams, t, onChanged) }, "Edit") : null,
        can("manager") ? h("button", { class: "btn small danger", onclick: async () => {
          if (!(await confirmDialog("Delete team", `Delete ${t.name}? Its record history and roster are removed too.`, { confirmLabel: "Delete" }))) return;
          try { await g(`/r/teams/${t.id}`, { method: "DELETE" }); toast("Team deleted"); onChanged?.(); } catch (e) { toast(e.message, "err"); }
        } }, "Delete") : null));
}

async function teamsBody(el) {
  const [{ items }, leagues] = await Promise.all([
    g("/r/teams?per_page=100"),
    state.specs.leagues ? g("/r/leagues?per_page=100").then((r) => r.items).catch(() => []) : Promise.resolve([]),
  ]);
  clear(el);
  const reload = () => { clear(el).append(spinner()); teamsBody(el); };
  el.append(h("div", { class: "row" },
    h("p", { class: "page-sub" }, `${items.length} team(s). Records are saved through the bot and logged in each team's history.`),
    h("span", { class: "spacer" }),
    state.specs.teams && can(state.specs.teams.write) ? h("button", { class: "btn primary small", onclick: () => openForm(state.specs.teams, null, reload) }, "New team") : null));
  if (!items.length) { el.append(h("div", { class: "card empty" }, "No teams yet. Create the first one.")); return; }

  const groups = new Map();
  for (const t of items) {
    const league = leagues.find((l) => leagueMatchesTeam(l, t));
    const key = league ? `L:${lower(league.league_abbr || league.league_name)}` : `C:${lower(t.competition || "No league")}`;
    if (!groups.has(key)) {
      groups.set(key, { league, name: league ? league.league_name : (t.competition || "No league"), teams: [] });
    }
    groups.get(key).teams.push(t);
  }
  const ordered = [...groups.values()].sort((a, b) => (a.league ? 0 : 1) - (b.league ? 0 : 1) || a.name.localeCompare(b.name));
  for (const g of ordered) {
    el.append(h("section", { class: "league" },
      h("div", { class: "league-head" },
        g.league ? h("span", { class: "league-abbr" }, g.league.league_abbr) : null,
        h("span", { class: "league-name" }, g.name),
        h("span", { class: "league-meta" }, `${g.teams.length} team(s)`),
        h("span", { class: "spacer" })),
      g.teams.map((t) => teamRecordRow(t, { onChanged: reload }))));
  }
}

// ───────── rosters: build a whole team's roster at once ─────────
const ROSTER_MAX = 25;

function rosterRow() {
  const id = h("input", { placeholder: "Discord ID", inputmode: "numeric", maxLength: 21, "aria-label": "Discord ID" });
  const name = h("input", { placeholder: "Username", maxLength: 80, "aria-label": "Username" });
  const pos = h("input", { placeholder: "Position", maxLength: 40, "aria-label": "Position" });
  return { row: h("div", { class: "roster-row" }, id, name, pos), id, name, pos };
}

function openRosterForm(teams, onSaved) {
  const teamSel = h("select", { "aria-label": "Team" }, teams.map((t) => opt(String(t.id), t.name)));
  const list = h("div", { class: "roster-rows" });
  const err = h("div", { class: "err" });
  const entries = [];
  const addBtn = h("button", { class: "btn small", type: "button" }, "Add player");
  const sync = () => { addBtn.disabled = entries.length >= ROSTER_MAX; };
  const addRow = () => {
    if (entries.length >= ROSTER_MAX) return;
    const e = rosterRow();
    const remove = h("button", { class: "btn small ghost", type: "button", "aria-label": "Remove this player" }, "Remove");
    remove.addEventListener("click", () => { e.row.remove(); entries.splice(entries.indexOf(e), 1); sync(); });
    e.row.append(remove);
    entries.push(e);
    list.append(e.row);
    sync();
  };
  addBtn.addEventListener("click", addRow);
  for (let i = 0; i < 3; i++) addRow();

  const save = async (close, btns) => {
    err.textContent = "";
    const players = [];
    for (const [i, e] of entries.entries()) {
      const uid = e.id.value.trim(), username = e.name.value.trim(), position = e.pos.value.trim();
      if (!uid && !username && !position) continue;
      if (!/^\d{15,21}$/.test(uid)) { err.textContent = `Row ${i + 1}: enter the player's Discord ID (15–21 digits).`; e.id.focus(); return; }
      players.push({ user_id: uid, username, position });
    }
    if (!players.length) { err.textContent = "Add at least one player."; return; }
    if (new Set(players.map((p) => p.user_id)).size !== players.length) { err.textContent = "The same Discord ID is in the list twice."; return; }
    btns.forEach((b) => (b.disabled = true));
    try {
      const res = await g(`/teams/${teamSel.value}/roster`, { method: "POST", body: { players } });
      close();
      toast(`Added ${res.added} player(s) to ${teamSel.selectedOptions[0]?.textContent || "the roster"}`);
      onSaved?.();
    } catch (e) {
      err.textContent = e.errors?.players || e.message;
      btns.forEach((b) => (b.disabled = false));
    }
  };
  modal("New roster", h("div", {},
    h("div", { class: "field" }, h("label", {}, "Team"), teamSel),
    h("p", { class: "help" }, `One row per player — up to ${ROSTER_MAX} per team. Blank rows are ignored.`),
    list, h("div", { class: "row" }, addBtn), err),
    [{ label: "Cancel", onClick: (close) => close() }, { label: "Add roster", cls: "primary", onClick: save }], { wide: true });
}

// Every roster row, page by page — the tab groups them by team, so it needs them all rather than the first page.
async function allRosterRows() {
  const rows = [];
  for (let page = 1; page <= 20; page++) {
    const data = await g(`/r/team_members?page=${page}&per_page=100`);
    rows.push(...(data.items || []));
    if (!(data.items || []).length || rows.length >= (data.total || 0)) break;
  }
  return rows;
}

const ROSTER_ORDER = { active: 0, reserve: 1, injured: 2, inactive: 3 };
const byRoster = (a, b) => (ROSTER_ORDER[a.status] ?? 9) - (ROSTER_ORDER[b.status] ?? 9)
  || (a.jersey ?? 9999) - (b.jersey ?? 9999) || String(a.username || "").localeCompare(String(b.username || ""));

async function rostersBody(el) {
  clear(el);
  const reload = () => { clear(el).append(spinner()); rostersBody(el); };
  const spec = state.specs.team_members, teams = state.gmeta.teams || [];
  const search = h("input", { type: "search", placeholder: "Search players, teams or positions…", "aria-label": "Search rosters",
    oninput: () => { clearTimeout(search._t); search._t = setTimeout(paint, 150); } });
  const out = h("div", {});
  el.append(h("div", { class: "row" },
      h("p", { class: "page-sub" }, "Rosters, grouped by team: the team and its league once, then each player's Discord ID, position, jersey and status."),
      h("span", { class: "spacer" }),
      spec && can(spec.write) && teams.length ? h("button", { class: "btn primary small", onclick: () => openRosterForm(teams, reload) }, "New roster") : null),
    h("div", { class: "card tight filters" }, h("div", { class: "search" }, search)),
    out);

  let rows;
  try {
    rows = await allRosterRows();
  } catch (e) {
    return clear(out).append(errorBox(e));
  }
  const teamOf = new Map(teams.map((t) => [String(t.id), t]));
  const teamOrder = new Map(teams.map((t, i) => [String(t.id), i]));
  const writable = spec && can(spec.write);

  async function removePlayer(r, group) {
    if (!(await confirmDialog("Delete", `Remove ${r.username || "this player"} from ${group.name}'s roster?`, { confirmLabel: "Remove" }))) return;
    try { await g(`/r/team_members/${r.id}`, { method: "DELETE" }); reload(); } catch (e) { toast(e.message, "err"); }
  }

  function playerRow(r, group) {
    const actions = writable
      ? h("td", {}, h("div", { class: "row end" },
          h("button", { class: "btn small", onclick: () => openForm(spec, r, reload) }, "Edit"),
          can(spec.delete) ? h("button", { class: "btn small danger", onclick: () => removePlayer(r, group) }, "Delete") : null))
      : null;
    return h("tr", {},
      h("td", {}, h("div", { class: "identity" }, h("b", {}, r.username || "—"), h("div", { class: "sub" }, String(r.user_id || "")))),
      h("td", {}, r.position || "—"),
      h("td", { class: "num" }, r.jersey ?? "—"),
      h("td", {}, h("span", { class: `badge ${r.status || "active"}` }, String(r.status || "active").replace(/^./, (c) => c.toUpperCase()))),
      actions);
  }

  function paint() {
    const q = search.value.trim().toLowerCase();
    const groups = new Map();
    for (const r of rows) {
      const team = teamOf.get(String(r.team_id));
      const key = String(r.team_id);
      if (!groups.has(key)) {
        groups.set(key, { key, name: team?.name || r.team_name || "Team removed from the dashboard",
                          competition: team?.competition || "", players: [] });
      }
      groups.get(key).players.push(r);
    }
    const ordered = [...groups.values()].sort((a, b) => (teamOrder.get(a.key) ?? 9999) - (teamOrder.get(b.key) ?? 9999) || a.name.localeCompare(b.name));
    clear(out);
    let shown = 0;
    for (const group of ordered) {
      const players = group.players
        .filter((r) => !q || [r.username, r.user_id, r.position, r.jersey, r.status, group.name, group.competition].some((v) => String(v ?? "").toLowerCase().includes(q)))
        .sort(byRoster);
      if (!players.length) continue;
      shown += players.length;
      out.append(h("section", { class: "league" },
        h("div", { class: "league-head" },
          h("span", { class: "league-name" }, group.name),
          group.competition ? h("span", { class: "league-meta" }, `– ${group.competition}`) : null,
          h("span", { class: "spacer" }),
          h("span", { class: "league-meta" }, `${players.length} player(s)`),
          writable ? h("button", { class: "btn small", onclick: () => openForm(spec, null, reload, { prefill: { team_id: Number(group.key) } }) }, "Add player") : null),
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, ["Player", "Position", "Jersey", "Status"].map((x) => h("th", {}, x)), writable ? h("th", {}, "") : null)),
          h("tbody", {}, players.map((r) => playerRow(r, group)))))));
    }
    if (!shown) out.append(h("div", { class: "card empty" }, rows.length ? "No players match your search." : "No rosters yet. Use New roster to build one."));
  }
  paint();
}

// ───────── matches: import a report, or log a box score from its screenshot ─────────
const SPORT_NAMES = { ff: "Football Fusion", pb: "Practical Basketball", uf: "Ultimate Football" };

function openStatsImport(onSaved) {
  const teams = state.gmeta.teams || [];
  const sport = h("select", {}, Object.entries(SPORT_NAMES).map(([v, l]) => opt(v, l)));
  const report = h("textarea", { rows: 9, maxLength: 20000, placeholder: "Paste the Football Fusion export here, or pick its file below." });
  const reportFile = h("input", { type: "file", accept: ".json,.txt,application/json,text/plain" });
  const reportFileNote = h("div", { class: "help" }, "Or pick the exported .json / .txt file — its text goes into the box above.");
  const file = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp" });
  const preview = h("img", { class: "shot-preview hidden", alt: "Screenshot preview" });
  const shotNote = h("div", { class: "help" });
  const home = h("select", {}, teams.map((t) => opt(String(t.id), t.name)));
  const away = h("select", {}, opt("", "— not in the dashboard —"), teams.map((t) => opt(String(t.id), t.name)));
  const awayName = h("input", { type: "text", maxLength: 50, placeholder: "Opponent name" });
  const hs = h("input", { type: "number", min: 0, max: 9999, placeholder: "88" });
  const as_ = h("input", { type: "number", min: 0, max: 9999, placeholder: "74" });
  const when = h("input", { id: "ff-when", type: "datetime-local", value: localDateTime(new Date()) });
  const records = h("input", { type: "checkbox" });
  const err = h("div", { class: "err" });
  let shot = null;
  let parsed = null;  // the /matches/ff/preview answer, once the report has been read
  let primary = null;

  // Shown once the report parses: what will be submitted, which stat-sheet team is yours, and the score flip.
  const confirmLead = h("p", {});
  const confirmScore = h("p", { class: "page-sub" });
  const mineSel = h("select", { onchange: () => paint() });
  const mineField = h("div", { class: "field hidden" }, h("label", {}, "My team"), mineSel);
  const flip = h("input", { type: "checkbox", onchange: () => paint() });
  const flipField = h("label", { class: "row hidden" }, flip, " My team lost — flip the score");
  const confirm = h("div", { class: "card tight hidden" }, confirmLead, confirmScore, mineField, flipField);

  function paint() {
    if (!parsed) return;
    const mine = parsed.sides.find((s) => s.name === mineSel.value) || parsed.sides[0];
    const theirs = parsed.sides.find((s) => s !== mine);
    const us = flip.checked ? theirs.score : mine.score;
    const them = flip.checked ? mine.score : theirs.score;
    confirmLead.textContent = `Will submit ${mine.team_name || mine.name} vs ${theirs.team_name || theirs.name}.`;
    confirmScore.textContent = `Score: ${us} – ${them}${parsed.competition ? ` · ${parsed.competition}` : ""}`;
  }

  function showConfirm(p) {
    parsed = p;
    clear(mineSel);
    p.sides.filter((s) => s.team_id).forEach((s) => mineSel.append(opt(s.name, s.team_name)));
    mineSel.value = (p.sides.find((s) => s.team_id === p.home_team_id) || p.sides[0]).name;
    mineField.classList.toggle("hidden", mineSel.options.length < 2);
    flip.checked = false;
    flipField.classList.toggle("hidden", p.sides_confirmed);
    confirm.classList.remove("hidden");
    paint();
  }

  function resetPreview() {
    parsed = null;
    confirm.classList.add("hidden");
    if (primary) primary.textContent = "Import match";
  }

  const ffFields = h("div", { class: "field" }, h("label", {}, "Game report"), report, reportFile, reportFileNote,
    h("div", { class: "help" }, "Both Football Fusion exports work, pasted or uploaded: the long report, or “27 - 25 /// {…}”. Teams are matched by name against your dashboard teams, and you will see what will be submitted before it is saved."));
  const scoreFields = h("div", {},
    h("div", { class: "field" }, h("label", {}, "Your team"), home),
    h("div", { class: "field" }, h("label", {}, "Opponent"), away, awayName,
      h("div", { class: "help" }, "Pick a dashboard team, or leave it on “not in the dashboard” and type the name.")),
    h("div", { class: "row" },
      h("div", { class: "field" }, h("label", {}, "Your score"), hs),
      h("div", { class: "field" }, h("label", {}, "Opponent score"), as_)));

  const sync = () => {
    const ff = sport.value === "ff";
    ffFields.classList.toggle("hidden", !ff);
    scoreFields.classList.toggle("hidden", ff);
    shotNote.textContent = ff
      ? "Optional — attach the report screenshot too and the bot posts it with the result."
      : "Required — the screenshot is this match's stat sheet, and the bot posts it with the result.";
  };
  sport.addEventListener("change", () => { sync(); resetPreview(); });
  report.addEventListener("input", resetPreview);
  sync();

  reportFile.addEventListener("change", async () => {
    err.textContent = "";
    const f = reportFile.files && reportFile.files[0];
    if (!f) return;
    try {
      const text = await readTextFile(f, report.maxLength);
      report.value = text;
      resetPreview();
      reportFileNote.textContent = `Loaded ${f.name} (${Math.max(1, Math.round(text.length / 1024))} KB) — it is in the box above, ready to import.`;
    } catch (e) {
      err.textContent = e.message;
      reportFile.value = "";
    }
  });

  file.addEventListener("change", async () => {
    err.textContent = "";
    shot = null;
    preview.classList.add("hidden");
    const f = file.files && file.files[0];
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) { err.textContent = "That screenshot is over 10 MB — crop it or take a smaller one."; file.value = ""; return; }
    try {
      const data = await readImageFile(f);
      shot = data;
      preview.src = data;
      preview.classList.remove("hidden");
      shotNote.textContent = `Attached (${Math.max(1, Math.round(data.length / 1024))} KB after resizing).`;
    } catch (e) {
      err.textContent = e.message;
      file.value = "";
    }
  });

  const box = h("div", {},
    h("div", { class: "field" }, h("label", {}, "Sport"), sport,
      h("div", { class: "help" }, "Football Fusion keeps its full stat sheet from the report. The other two keep the screenshot you attach.")),
    ffFields, scoreFields, confirm,
    h("div", { class: "field" }, h("label", {}, "Box-score screenshot"), file, shotNote, preview),
    h("div", { class: "field" }, h("label", { for: "ff-when" }, "When it was played"), when),
    h("label", { class: "row" }, records, " Also update team records"), err);

  modal("Import game stats", box,
    [{ label: "Cancel", onClick: (c) => c() },
      { label: "Import match", cls: "primary", onClick: async (c, btns) => {
        err.textContent = "";
        primary = btns[1];
        const ff = sport.value === "ff";
        if (ff && !report.value.trim()) { err.textContent = "Paste the Football Fusion report or pick its .json / .txt file first."; return; }
        if (!ff && !shot) { err.textContent = "Attach the box-score screenshot first."; return; }
        if (ff && !parsed) {  // parse it first and show what would be submitted — nothing is saved yet
          btns.forEach((b) => (b.disabled = true));
          try {
            showConfirm(await g("/matches/ff/preview", { method: "POST", body: { report: report.value } }));
            primary.textContent = "Confirm import";
          } catch (e) {
            err.textContent = e.errors?.report || e.message;
          }
          btns.forEach((b) => (b.disabled = false));
          return;
        }
        const body = { sport: sport.value, played_at: when.value ? new Date(when.value).toISOString() : undefined, apply_record: records.checked };
        if (ff) { body.report = report.value; body.my_team = mineSel.value; body.flip = flip.checked; }
        else {
          body.home_team_id = Number(home.value);
          body.home_score = hs.value === "" ? undefined : Number(hs.value);
          body.away_score = as_.value === "" ? undefined : Number(as_.value);
          if (away.value) body.away_team_id = Number(away.value);
          else body.away_name = awayName.value.trim() || undefined;
        }
        if (shot) body.image = shot;
        btns.forEach((b) => (b.disabled = true));
        try {
          const res = await g("/matches/import", { method: "POST", body });
          c(); toast(res.notice || "Match imported", res.queued ? "warn" : "");
          onSaved?.();
        } catch (e) {
          err.textContent = e.errors?.image || e.errors?.report || e.errors?.home_team_id || e.errors?.home_score
            || e.errors?.away_name || e.errors?.played_at || e.errors?.sport || e.message;
          btns.forEach((b) => (b.disabled = false));
        }
      } }], { wide: true });
}

async function matchesBody(el) {
  clear(el);
  const reload = () => { clear(el).append(spinner()); matchesBody(el); };
  const spec = state.specs.matches;
  const body = h("div", {});
  el.append(h("div", { class: "row" },
    h("p", { class: "page-sub" }, "Log a match against any opponent, or import a Football Fusion report / box-score screenshot. Matches with a stat sheet get Stats and Screenshot buttons."),
    h("span", { class: "spacer" }),
    spec && can(spec.write) ? h("button", { class: "btn primary small", onclick: () => openStatsImport(reload) }, "Import game stats") : null),
    body);
  return renderResource(body, "matches", { embedded: true });
}

// ───────── Football Fusion ─────────
const statName = (s) => String(s || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function ffPage(c) {
  clear(c).append(pageHead({ kicker: "Competition", title: "Football Fusion",
    sub: "Only Football Fusion game reports are parsed into stats, so this is the one place with a stat comparison leaderboard." }));
  const comp = h("select", { onchange: () => load() }, opt("", "All competitions"), state.gmeta.competitions.map((x) => opt(x, x)));
  const stat = h("select", { onchange: () => load() });
  const out = h("div", {});
  c.append(h("div", { class: "card tight filters" }, comp, stat, h("span", { class: "spacer" })), out);

  async function load() {
    clear(out).append(spinner());
    try {
      const res = await g(`/ff?competition=${encodeURIComponent(comp.value)}&stat=${encodeURIComponent(stat.value)}`);
      clear(stat).append(...(res.stats.length ? res.stats.map((s) => opt(s, statName(s), s === res.stat)) : [opt("", "No stats yet")]));
      clear(out);
      if (!res.players.length) {
        out.append(h("div", { class: "card empty" }, "No Football Fusion stats yet — import a game report from the Matches tab."));
        return;
      }
      const leaderRows = res.players.map((p, i) => h("tr", {},
        h("td", {}, String(i + 1)),
        h("td", {}, h("b", {}, p.name), h("div", { class: "sub" }, p.user_id ? "Linked" : "Not linked")),
        h("td", {}, p.team || "—"),
        h("td", {}, p.position || "—"),
        h("td", {}, String(p.games)),
        h("td", {}, String(p.totals[res.stat] ?? 0)),
        h("td", {}, String(p.averages[res.stat] ?? 0))));
      const leaderHead = ["#", "Player", "Team", "Pos", "GP", statName(res.stat), "Per game"].map((x) => h("th", {}, x));
      const leaders = h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, leaderHead)),
        h("tbody", {}, leaderRows)));
      out.append(h("h2", {}, `${statName(res.stat)} leaders`), leaders);

      if (res.games.length) {
        const gameHead = ["Date", "Match", "Score", "Reported by", ""].map((x) => h("th", {}, x));
        const gameRows = res.games.map((gm) => h("tr", {},
          h("td", {}, fmtDate(gm.played_at)),
          h("td", {}, `${gm.home} vs ${gm.away}`),
          h("td", {}, `${gm.home_score} – ${gm.away_score}`),
          h("td", {}, gm.ff?.reporter?.name || "—"),
          h("td", {}, h("button", { class: "btn small", onclick: () => showFusion(gm.ff) }, "Stats"))));
        const games = h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, gameHead)),
          h("tbody", {}, gameRows)));
        out.append(h("h2", {}, "Recent Football Fusion games"), games);
      }
    } catch (e) { clear(out).append(errorBox(e)); }
  }
  load();
}

export { ffPage };

async function leaguesBody(el) {
  const [{ items: teams }, leaguesRes] = await Promise.all([
    g("/r/teams?per_page=100"),
    state.specs.leagues ? g("/r/leagues?per_page=100").catch((e) => ({ error: e })) : Promise.resolve({ items: [] }),
  ]);
  clear(el);
  const reload = () => { clear(el).append(spinner()); leaguesBody(el); };
  const spec = state.specs.leagues;
  const leagues = leaguesRes.items || [];
  el.append(h("div", { class: "row" },
    h("p", { class: "page-sub" }, "A league is the competition your teams belong to. The bot's /league add creates one with a signup role."),
    h("span", { class: "spacer" }),
    spec && can(spec.write) ? h("button", { class: "btn primary small", onclick: () => openForm(spec, null, reload) }, "New league") : null));

  if (!spec) {
    el.append(h("div", { class: "card" }, h("h2", {}, "Leagues aren't available yet"),
      h("p", { class: "page-sub" }, "The API this dashboard is talking to doesn't serve leagues. Deploy the current backend and reload.")));
    return;
  }
  if (leaguesRes.error) { el.append(errorBox(leaguesRes.error)); return; }

  const used = new Set();
  for (const league of leagues) {
    const mine = teams.filter((t) => leagueMatchesTeam(league, t));
    mine.forEach((t) => used.add(t.id));
    const deleteBtn = can(spec.delete) ? h("button", { class: "btn small danger", onclick: async () => {
      if (!(await confirmDialog("Delete league", `Delete ${league.league_name}? The Discord role is left alone.`, { confirmLabel: "Delete" }))) return;
      try { await g(`/r/leagues/${league.id}`, { method: "DELETE" }); toast("League deleted"); reload(); } catch (e) { toast(e.message, "err"); }
    } }, "Delete") : null;
    const head = h("div", { class: "league-head" },
      h("span", { class: "league-abbr" }, league.league_abbr || "—"),
      h("span", { class: "league-name" }, league.league_name),
      h("span", { class: "league-meta" }, `${mine.length} team(s)`),
      league.role_id ? h("span", { class: "chip" }, nameOfRole(league.role_id)) : null,
      league.signup ? h("a", { class: "chip", href: league.signup, target: "_blank", rel: "noopener noreferrer" }, "Signup link") : null,
      h("span", { class: "spacer" }),
      can(spec.write) ? h("button", { class: "btn small", onclick: () => openForm(state.specs.teams, null, reload, { prefill: { competition: league.league_name } }) }, "Add team") : null,
      can(spec.write) ? h("button", { class: "btn small ghost", onclick: () => openForm(spec, league, reload) }, "Edit") : null,
      deleteBtn);
    const rows = mine.length
      ? mine.map((t) => teamRecordRow(t, { onChanged: reload, compact: true }))
      : h("div", { class: "league-meta" }, "No teams in this league yet.");
    el.append(h("section", { class: "league" }, head, rows));
  }

  const unassigned = teams.filter((t) => !used.has(t.id));
  if (unassigned.length) {
    el.append(h("section", { class: "league" },
      h("div", { class: "league-head" },
        h("span", { class: "league-name" }, leagues.length ? "Teams outside a league" : "All teams"),
        h("span", { class: "league-meta" }, `${unassigned.length} team(s)`),
        h("span", { class: "spacer" })),
      unassigned.map((t) => teamRecordRow(t, { onChanged: reload, compact: true }))));
  }
  if (!teams.length) el.append(h("div", { class: "card empty" }, "No teams yet — add one from the Teams tab."));
}

// A resource table with a plain-language line above it, for tabs whose name alone doesn't say what the thing is.
function describedTab(text, res) {
  return (el) => {
    clear(el).append(h("p", { class: "page-sub" }, text));
    const body = h("div", {});
    el.append(body);
    return renderResource(body, res, { embedded: true });
  };
}

export function competitionPage(c, initial = "leagues") {
  return tabPage(c, {
    kicker: "Competition",
    title: "Leagues & results",
    sub: "Leagues hold your teams; matches and standings keep the record straight.",
    tabs: [
      { key: "leagues", label: "Leagues", render: (el) => leaguesBody(el) },
      { key: "teams", label: "Teams", render: (el) => teamsBody(el) },
      { key: "rosters", label: "Rosters", render: (el) => rostersBody(el) },
      { key: "matches", label: "Matches", render: (el) => matchesBody(el) },
      { key: "standings", label: "Standings", render: (el) => leaderboardBody(el) },
      { key: "seasons", label: "Competitions", render: describedTab("A competition is one season or tournament your teams play in — “Fall 2026” or “NFA Season 3”. Teams and divisions name the competition they belong to, and a match can be tagged with one; renaming a competition renames it on its teams and divisions.", "competitions") },
      { key: "divisions", label: "Divisions", render: describedTab("A division is a smaller group inside a competition — East/West, Varsity/JV and so on. Teams pick their division, and Standings can be filtered by it.", "divisions") },
    ],
    initial,
  });
}

// ───────── standings ─────────
function leaderboardBody(c) {
  clear(c).append(h("p", { class: "page-sub" }, "Every team's record from the matches you log, ranked. Win % is wins divided by games played (ties count as half a win), so a team that plays more can rank differently — pick a sort, or filter to one league or division to see a single group."));
  const sort = h("select", {}, opt("wins", "Most wins"), opt("percentage", "Winning %"), opt("losses", "Fewest losses"));
  const comp = h("select", {}, opt("", "All leagues"), state.gmeta.competitions.map((x) => opt(x, x)));
  const div = h("select", {}, opt("", "All divisions"), [...new Set(state.gmeta.divisions.map((d) => d.name))].map((x) => opt(x, x)));
  const out = h("div", {});
  const load = async () => {
    clear(out).append(spinner());
    try {
      const qs = new URLSearchParams({ sort: sort.value, ...(comp.value ? { competition: comp.value } : {}), ...(div.value ? { division: div.value } : {}) });
      const { items } = await g(`/leaderboard?${qs}`);
      clear(out).append(items.length
        ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, ["#", "Team", "League", "W", "L", "T", "Win %"].map((x, i) => h("th", { class: i > 2 ? "num" : "" }, x)))),
          h("tbody", {}, items.map((t) => h("tr", {},
            h("td", { class: "num" }, t.rank),
            h("td", {}, h("div", { class: "identity" },
              t.logo ? h("img", { class: "avatar sm", src: t.logo, alt: "", referrerpolicy: "no-referrer" }) : null, h("b", {}, t.name))),
            h("td", {}, `${t.competition || "—"}${t.division ? " · " + t.division : ""}`),
            h("td", { class: "num" }, t.wins), h("td", { class: "num" }, t.losses), h("td", { class: "num" }, t.ties),
            h("td", { class: "num" }, t.pct))))))
        : h("div", { class: "card empty" }, "No teams match those filters."));
    } catch (e) { clear(out).append(errorBox(e)); }
  };
  [sort, comp, div].forEach((s) => s.addEventListener("change", load));
  const bar = h("div", { class: "card tight row" }, sort, comp, div);
  c.append(bar, out);
  return load();
}

// ───────── polls (+ archive) ─────────
function pollArchiveBody(c) {
  clear(c).append(spinner());
  return g("/poll-archive").then(({ items }) => {
    clear(c);
    if (!items.length) return c.append(h("div", { class: "card empty" }, "No closed polls yet."));
    c.append(...items.map((p) => {
      const total = p.results.reduce((a, r) => a + r.votes, 0) || 1;
      return h("div", { class: "card" },
        h("h2", {}, `#${p.poll_id} ${p.question}`),
        h("div", { class: "page-sub" }, `${p.voters} voter(s) · closed ${fmtDate(p.closed_at)}${p.anonymous ? " · anonymous" : ""}`),
        h("div", { style: null }, p.results.map((r) => h("div", { style: null },
          h("div", { class: "row" }, h("span", {}, r.option), h("span", { class: "spacer" }),
            h("b", {}, `${r.votes} · ${Math.round((r.votes / total) * 100)}%`)),
          meter(r.votes / total)))));
    }));
  }).catch((e) => { clear(c).append(errorBox(e)); });
}

export function pollsPage(c, initial = "active") {
  return tabPage(c, {
    kicker: "Community",
    title: "Polls",
    sub: "Members vote with the buttons on the Discord message; results collect here.",
    tabs: [
      { key: "active", label: "Polls", render: (el) => renderResource(el, "polls", { embedded: true }) },
      { key: "archive", label: "Archive", render: (el) => pollArchiveBody(el) },
    ],
    initial,
  });
}

// ───────── insights ─────────
export function activityPage(c) {
  clear(c).append(pageHead({ kicker: "Insights", title: "Member activity", sub: "Message counts per member, grouped by UTC day." }));
  const period = h("select", {}, opt("week", "Last 7 days"), opt("day", "Today"), opt("all", "All time"));
  const out = h("div", {});
  const load = async () => {
    clear(out).append(spinner());
    try {
      const d = await g(`/activity?period=${period.value}`);
      clear(out).append(
        d.enabled ? null : h("div", { class: "card" }, h("h2", {}, "Activity tracking is off"),
          h("p", { class: "page-sub" }, "Turn on “Track message activity” in Server settings to start counting. Bots are never counted.")),
        h("div", { class: "card" }, h("h2", {}, "Messages per day · last 14 days"),
          d.daily.length ? barChart(d.daily.map((x) => ({ label: x.date, value: x.count }))) : h("p", { class: "page-sub" }, "No data yet.")),
        h("div", { class: "card" }, h("h2", {}, "Most active members"),
          d.top.length
            ? h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, d.top.map((r, i) => h("tr", {},
              h("td", { class: "num" }, i + 1), h("td", {}, r.name), h("td", { class: "num" }, r.count.toLocaleString()))))))
            : h("p", { class: "page-sub" }, "Nobody yet.")));
    } catch (e) { clear(out).append(errorBox(e)); }
  };
  period.addEventListener("change", load);
  c.append(h("div", { class: "card tight row" }, period), out);
  return load();
}

export function memberStatsPage(c) {
  return wrap(c, "Member growth", "Daily snapshots of server size, plus joins and leaves.", async () => {
    const d = await g("/memberstats?days=30");
    const last = d.snapshots.at(-1), first = d.snapshots[0];
    const stat = (n, l) => h("div", { class: "stat" }, h("b", {}, n), h("span", {}, l));
    return h("div", {},
      h("div", { class: "grid" },
        stat(last ? last.total.toLocaleString() : "—", "Members now"),
        stat(`+${d.joined}`, "Joined · 30d"),
        stat(`−${d.left}`, "Left · 30d"),
        stat(first && last ? `${last.total - first.total >= 0 ? "+" : ""}${last.total - first.total}` : "—", "Net change · 30d")),
      h("div", { class: "card" }, h("h2", {}, "Total members"),
        d.snapshots.length ? barChart(d.snapshots.map((s) => ({ label: s.date, value: s.total })), (p) => `${p.label}: ${p.value}`)
          : h("p", { class: "page-sub" }, "Snapshots are taken hourly — check back soon.")),
      h("div", { class: "card" }, h("h2", {}, "Joins per day"),
        d.daily.length ? barChart(d.daily.map((s) => ({ label: s.date, value: s.joined })), (p) => `${p.label}: +${p.value}`)
          : h("p", { class: "page-sub" }, "No joins recorded yet.")));
  }, "Insights");
}

// ───────── moderation ─────────
export function moderationPage(c) {
  clear(c).append(pageHead({ kicker: "Staff", title: "Moderation", sub: "Lock channels from the dashboard — the original @everyone permission is restored exactly on unlock." }));
  const ch = h("select", {}, state.gmeta.channels.filter((x) => x.type === "text").map((x) => opt(x.id, `#${x.name}`)));
  const reason = h("input", { placeholder: "Reason (optional)", maxLength: 200 });
  const locks = h("div", {});
  const act = async (locked) => {
    try {
      const r = await g(`/channels/${ch.value}/lock`, { method: "POST", body: { locked, reason: reason.value } });
      toast(r.notice || (locked ? "Channel locked" : "Channel unlocked"), r.queued ? "warn" : "");
      loadLocks();
    } catch (e) { toast(e.message, "err"); }
  };
  const loadLocks = async () => {
    try {
      const { items } = await g("/locks");
      clear(locks).append(items.length
        ? h("div", { class: "list" }, items.map((l) => h("div", { class: "list-row" },
          h("b", {}, nameOfChannel(l.channel_id)), h("span", { class: "sub" }, `locked ${fmtDate(l.locked_at)}${l.reason ? " — " + l.reason : ""}`))))
        : h("p", { class: "page-sub" }, "No channels are locked by TeamCore."));
    } catch (e) { clear(locks).append(errorBox(e)); }
  };
  const logs = h("div", {});
  c.append(
    h("div", { class: "card" }, h("h2", {}, "Lock a channel"),
      h("div", { class: "row stretch" }, ch, reason),
      h("div", { class: "row end" },
        h("button", { class: "btn", disabled: !can("manager") || null, onclick: () => act(false) }, "Unlock"),
        h("button", { class: "btn danger", disabled: !can("manager") || null, onclick: () => act(true) }, "Lock"))),
    h("div", { class: "card" }, h("h2", {}, "Locked right now"), locks),
    h("div", { class: "card" }, h("h2", {}, "Moderation log"), logs));
  loadLocks();
  return renderResource(logs, "moderation_logs", { embedded: true });
}

// ───────── backups ─────────
export function backupsPage(c) {
  return wrap(c, "Backups", "Saves roles, channels, permission overwrites and basic settings. Messages, members, emojis, webhooks and bans are not included.", async () => {
    const root = h("div", {});
    const rowFor = (b) => h("tr", {},
      h("td", {}, b.name),
      h("td", {}, `${b.stats.roles} roles · ${b.stats.categories} categories · ${b.stats.channels} channels`),
      h("td", {}, fmtDate(b.created_at)),
      h("td", {}, h("div", { class: "row end" },
        h("button", { class: "btn small", onclick: () => restore(b) }, "Restore"),
        h("button", { class: "btn small danger", onclick: () => remove(b) }, "Delete"))));
    const table = (items) => h("div", { class: "table-wrap" },
      h("table", {},
        h("thead", {}, h("tr", {}, ["Name", "Contents", "Created", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, items.map(rowFor))));
    const remove = async (b) => {
      if (!(await confirmDialog("Delete backup", `Delete “${b.name}”?`, { confirmLabel: "Delete" }))) return;
      try { await g(`/backups/${encodeURIComponent(b.name)}`, { method: "DELETE" }); } catch (e) { toast(e.message, "err"); }
      draw();
    };
    const draw = async () => {
      const { items } = await g("/backups");
      clear(root).append(
        h("div", { class: "row" },
          h("p", { class: "page-sub" }, `${items.length} backup(s) kept · up to 10.`),
          h("span", { class: "spacer" }),
          h("button", { class: "btn primary small", disabled: !can("admin") || null, onclick: () => {
            const name = h("input", { placeholder: "e.g. before-season-4", maxLength: 50 });
            modal("Create backup", h("div", {}, h("label", {}, "Name"), name), [
              { label: "Cancel", onClick: (x) => x() },
              { label: "Create", cls: "primary", onClick: async (x) => {
                x(); toast("Creating backup…");
                try { const r = await g("/backups", { method: "POST", body: { name: name.value } }); toast(r.notice || "Backup saved", r.queued ? "warn" : ""); draw(); }
                catch (e) { toast(e.errors?.name || e.message, "err"); }
              } },
            ]);
          } }, "New backup")),
        items.length ? table(items) : h("div", { class: "card empty" }, "No backups yet."));
    };
    const restore = (b) => {
      const mode = h("select", {}, opt("missing", "Add missing only (safe — nothing is deleted)"),
        state.gmeta ? h("option", { value: "full" }, "Full restore (deletes every channel and role first)") : null);
      const confirmBox = h("input", { placeholder: "Type RESTORE to confirm a full restore" });
      modal(`Restore “${b.name}”`, h("div", {}, h("label", {}, "Mode"), mode,
        h("label", {}, "Confirmation — full restore only, server owner only"), confirmBox,
        h("p", { class: "help" }, "Members do not get their roles back, and messages in deleted channels are gone for good.")), [
        { label: "Cancel", onClick: (x) => x() },
        { label: "Restore", cls: "danger", onClick: async (x) => {
          x(); toast("Restoring… this can take a few minutes", "warn");
          try {
            const r = await g(`/backups/${encodeURIComponent(b.name)}/restore`, { method: "POST", body: { mode: mode.value, confirm: confirmBox.value } });
            const s = r.result || {};
            toast(r.notice || `Done: ${s.roles_created ?? 0} roles and ${s.channels_created ?? 0} channels created`, r.queued ? "warn" : "");
          } catch (e) { toast(e.errors?.confirm || e.message, "err"); }
        } },
      ]);
    };
    await draw();
    return root;
  }, "Server");
}

// ───────── data export ─────────
export function exportPage(c) {
  return wrap(c, "Data export", "Download this server's TeamCore data. Secrets such as webhook URLs are never included.", async () => {
    const { collections } = await g("/export/options");
    const boxes = collections.map((n) => ({ n, el: h("input", { type: "checkbox", checked: true }) }));
    const fmt = h("select", {}, opt("json", "JSON — one file"), opt("csv", "CSV — zip, one file per collection"));
    const go = h("button", { class: "btn primary", onclick: async () => {
      const chosen = boxes.filter((b) => b.el.checked).map((b) => b.n);
      if (!chosen.length) return toast("Pick at least one collection", "err");
      go.disabled = true;
      try {
        const res = await api(`/api/guilds/${state.gid}/export?${new URLSearchParams({ collections: chosen.join(","), fmt: fmt.value })}`, { raw: true });
        const url = URL.createObjectURL(await res.blob());
        const a = h("a", { href: url, download: `teamcore-${state.gid}.${fmt.value === "csv" ? "zip" : "json"}` });
        document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      } catch (e) { toast(e.message, "err"); }
      go.disabled = false;
    } }, "Download");
    return h("div", { class: "card" },
      h("h2", {}, "Collections"),
      h("div", { class: "split" }, boxes.map((b) => h("label", { class: "row", style: null }, b.el, b.n))),
      h("div", { class: "row end" }, fmt, go));
  }, "Server");
}

// ───────── preferences ─────────
export function preferencesPage(c) {
  const accents = ["#FF7A1A", "#E4572E", "#D9A404", "#4C9F70", "#3E7CB1", "#8A6FE8", "#D7488F"];
  const color = h("input", { type: "color", value: state.prefs.accent });
  const tz = h("input", { list: "dl_tz2", value: state.prefs.timezone });
  const save = async (patch) => {
    try { state.prefs = await api("/api/me/preferences", { method: "PUT", body: patch }); setAccent(state.prefs.accent); toast("Preferences saved"); }
    catch (e) { toast(e.errors ? Object.values(e.errors)[0] : e.message, "err"); }
  };
  clear(c).append(pageHead({ kicker: "You", title: "Preferences", sub: "Personal to your account — these never change the server." }),
    h("div", { class: "card" }, h("h2", {}, "Accent"),
      h("div", { class: "row" },
        accents.map((a) => {
          const b = h("button", { class: `btn small${a === state.prefs.accent ? " primary" : ""}`, title: a, onclick: () => { color.value = a; save({ accent: a }); } }, " ");
          b.style.background = a; b.style.borderColor = a; b.style.width = "30px"; return b;
        }),
        color, h("button", { class: "btn primary", onclick: () => save({ accent: color.value }) }, "Apply"))),
    h("div", { class: "card" }, h("h2", {}, "Timezone"),
      h("p", { class: "page-sub" }, "Every date in the dashboard is shown in this timezone."),
      h("div", { class: "row" }, tz, h("button", { class: "btn primary", onclick: () => save({ timezone: tz.value }) }, "Save")),
      h("datalist", { id: "dl_tz2" }, (state.gmeta?.timezones || []).map((z) => h("option", { value: z })))));
}
