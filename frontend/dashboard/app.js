// TeamCore dashboard — shell, routing, login, server picker.
import { state, can, LEVELS } from "./state.js";
import { api, g, API, getToken, setToken } from "./api.js";
import { h, clear, spinner, toast, setAccent, errorBox } from "./ui.js";
import { renderResource } from "./resource.js";
import * as P from "./pages.js";

const app = document.getElementById("app");

// Hand-built pages: key -> [icon, label, minimum level, renderer]
const CUSTOM = {
  overview: ["🏠", "Overview", "viewer", P.overviewPage], settings: ["⚙️", "Settings", "manager", P.settingsPage], verification: ["✅", "Verification", "manager", P.verificationPage],
  records: ["🧮", "Team Records", "viewer", P.recordsPage], leaderboard: ["🥇", "Leaderboard", "viewer", P.leaderboardPage], poll_archive: ["🗄️", "Poll Archive", "viewer", P.pollArchivePage],
  activity: ["💬", "Member Activity", "viewer", P.activityPage], memberstats: ["📈", "Member Stats", "viewer", P.memberStatsPage], moderation: ["🛡️", "Moderation", "staff", P.moderationPage],
  backups: ["💾", "Backups", "admin", P.backupsPage], export: ["📦", "Data Export", "manager", P.exportPage], preferences: ["🎨", "Preferences", "viewer", P.preferencesPage],
};
const NAV = [
  ["", ["overview"]],
  ["Server", ["settings", "verification", "backups", "export"]],
  ["Competition", ["teams", "records", "team_members", "competitions", "divisions", "matches", "leaderboard", "stats_channels"]],
  ["Community", ["polls", "poll_archive", "giveaways", "countdowns", "events", "suggestions", "announcements", "role_panels"]],
  ["Automation", ["scheduled_messages", "reminders", "workflows", "webhooks"]],
  ["Players", ["player_profiles", "tryouts", "awards", "scouting_reports", "hall_of_fame"]],
  ["Insights", ["activity", "memberstats"]],
  ["Staff", ["moderation", "appeals", "staff_shifts", "staff_reports", "dashboard_permissions", "audit_logs"]],
];
const visible = (key) => {
  if (CUSTOM[key]) return can(CUSTOM[key][2]);
  const s = state.specs[key];
  return !!s && can(s.read);
};
const labelOf = (key) => (CUSTOM[key] ? [CUSTOM[key][0], CUSTOM[key][1]] : [state.specs[key].icon, state.specs[key].label]);

// ───────── boot ─────────
function parseHash() {
  // After login the backend redirects to  /dashboard/#token=…  (fragment: never sent to a server or written to logs).
  const raw = location.hash.slice(1);
  if (!raw.startsWith("token=") && !raw.startsWith("error=")) return null;
  const p = new URLSearchParams(raw);
  if (p.get("token")) setToken(p.get("token"));
  history.replaceState(null, "", location.pathname + "#/"); // scrub the token from the address bar immediately
  return p.get("error");
}

async function boot() {
  const err = parseHash();
  if (!getToken()) return loginScreen(err);
  clear(app).append(h("div", { class: "center" }, spinner()));
  try {
    const [me, meta, guilds] = await Promise.all([api("/auth/me"), api("/api/meta"), api("/api/guilds")]);
    state.user = me; state.prefs = me.preferences || state.prefs; state.specs = meta.resources; state.settingsSpec = meta.settings; state.guilds = guilds.guilds;
    setAccent(state.prefs.accent || "#FF7A1A");
  } catch (e) {
    if (e.status === 401) return loginScreen();
    return clear(app).append(h("div", { class: "center" }, errorBox(e)));
  }
  window.addEventListener("hashchange", route);
  route();
}

function loginScreen(error) {
  const msgs = { login_cancelled: "Login was cancelled.", bad_state: "Login expired — please try again.", token_exchange_failed: "Discord rejected the login. Try again.", profile_failed: "Couldn't read your Discord profile." };
  clear(app).append(h("div", { class: "center" }, h("div", { class: "hero" },
    h("div", { class: "logo-lg" }, "🏆"), h("h1", {}, "TeamCore Dashboard"), h("p", { class: "sub" }, "Manage your league, teams and community from the browser. Sign in with Discord to see the servers you can manage."),
    error ? h("p", { class: "err" }, msgs[error] || "Login failed.") : null,
    h("a", { class: "btn primary", href: `${API}/auth/login` }, "Sign in with Discord"),
    h("p", { class: "help" }, "We only request your username and server list. We never see your password."))));
}

// ───────── routing ─────────
async function route() {
  const m = location.hash.match(/^#\/g\/(\d+)(?:\/([\w-]+))?/);
  if (!m) return serverPicker();
  const [, gid, page = "overview"] = m;
  if (state.gid !== gid || !state.gmeta) {
    clear(app).append(h("div", { class: "center" }, spinner()));
    try {
      state.gid = gid; state.gmeta = null;
      const meta = await api(`/api/guilds/${gid}/meta`);
      state.gmeta = meta; state.level = meta.level;
    } catch (e) {
      state.gid = null;
      clear(app).append(h("div", { class: "center" }, h("div", { class: "hero" }, errorBox(e), h("a", { class: "btn", href: "#/" }, "← Choose another server"))));
      return;
    }
    buildShell();
  }
  openPage(page);
}

async function serverPicker() {
  state.gid = null; state.gmeta = null;
  try { state.guilds = (await api("/api/guilds")).guilds; } catch { /* keep cached list */ }
  const card = (gd) => h("div", { class: "server", onclick: () => (gd.bot_present ? (location.hash = `#/g/${gd.id}`) : window.open(gd.invite_url, "_blank", "noopener")) },
    gd.icon ? h("img", { class: "gicon", src: gd.icon, alt: "" }) : h("div", { class: "gicon" }, gd.name[0]),
    h("div", {}, h("b", {}, gd.name), h("div", { class: "sub" }, gd.bot_present ? `${gd.level} access` : "Add TeamCore →")));
  clear(app).append(h("div", { class: "content" },
    h("div", { class: "row" }, h("h1", {}, "Choose a server"), h("span", { class: "spacer" }),
      h("button", { class: "btn small", onclick: async () => { state.guilds = (await api("/api/guilds?refresh=true")).guilds; serverPicker(); } }, "↻ Refresh"),
      h("button", { class: "btn small", onclick: signOut }, "Sign out")),
    h("p", { class: "sub" }, "Servers you own or manage — or where an admin gave you dashboard access."),
    state.guilds.length ? h("div", { class: "servers" }, state.guilds.map(card)) : h("div", { class: "card empty" }, "No servers found. You need Administrator / Manage Server, or an admin must grant you access.")));
}

async function signOut() {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* token may already be gone */ }
  setToken(null); location.hash = "#/"; location.reload();
}

// ───────── shell ─────────
let content, side, notifTimer;
function buildShell() {
  clearInterval(notifTimer);
  const guild = state.gmeta.guild;
  side = h("aside", { class: "side" });
  const serverBtn = h("button", { class: "btn server-sel", onclick: () => (location.hash = "#/") }, guild.icon ? h("img", { class: "avatar", src: guild.icon, alt: "" }) : "🏟️", " ", h("b", {}, guild.name), h("span", { class: "spacer" }), "⇄");
  side.append(h("div", { class: "brand" }, h("div", { class: "logo" }, "🏆"), "TeamCore"), serverBtn,
    h("nav", { class: "nav" }, NAV.map(([group, keys]) => {
      const items = keys.filter(visible);
      return items.length ? [group ? h("div", { class: "nav-group" }, group) : null, items.map((k) => { const [i, l] = labelOf(k); return h("a", { dataset: { key: k }, onclick: () => { location.hash = `#/g/${state.gid}/${k}`; side.classList.remove("open"); } }, i, " ", l); })] : null;
    })));

  const results = h("div", { class: "results hidden" });
  const q = h("input", { placeholder: "Search teams, players, suggestions…", oninput: () => { clearTimeout(q._t); q._t = setTimeout(async () => {
    if (q.value.trim().length < 2) return results.classList.add("hidden");
    try { const { results: r } = await g(`/search?q=${encodeURIComponent(q.value.trim())}`); clear(results).append(r.length ? r.map((x) => h("a", { onclick: () => { results.classList.add("hidden"); q.value = ""; location.hash = `#/g/${state.gid}/${x.page}`; } }, x.label, h("span", { class: "sub" }, x.page.replace(/_/g, " ")))) : h("a", {}, "No results")); results.classList.remove("hidden"); } catch { /* ignore */ }
  }, 300); } });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) results.classList.add("hidden"); });

  const bell = h("button", { class: "btn rel", "aria-label": "Notifications", onclick: (e) => { e.stopPropagation(); toggleMenu(bellMenu); } }, "🔔");
  const bellMenu = h("div", { class: "menu hidden" });
  const userMenu = h("div", { class: "menu hidden" },
    h("a", { onclick: () => { location.hash = `#/g/${state.gid}/preferences`; toggleMenu(userMenu); } }, "🎨 Preferences"),
    h("a", { onclick: () => (location.hash = "#/") }, "⇄ Switch server"), h("button", { class: "item", onclick: signOut }, "⎋ Sign out"));
  const userBtn = h("button", { class: "btn", onclick: (e) => { e.stopPropagation(); toggleMenu(userMenu); } }, h("img", { class: "avatar", src: state.user.avatar_url, alt: "" }), state.user.username);
  const toggleMenu = (m) => { [bellMenu, userMenu].forEach((x) => x !== m && x.classList.add("hidden")); m.classList.toggle("hidden"); };
  document.addEventListener("click", () => [bellMenu, userMenu].forEach((x) => x.classList.add("hidden")));

  async function loadNotifs() {
    try {
      const { items } = await g("/notifications");
      bell.querySelector(".dot")?.remove();
      if (items.length) bell.append(h("span", { class: "dot" }, items.length));
      clear(bellMenu).append(items.length ? items.map((n) => h("a", { onclick: () => { location.hash = `#/g/${state.gid}/${n.page}`; bellMenu.classList.add("hidden"); } }, n.text)) : h("div", { class: "empty" }, "You're all caught up ✨"));
    } catch { /* non-critical */ }
  }

  const top = h("header", { class: "topbar" }, h("button", { class: "btn menu-btn", "aria-label": "Menu", onclick: () => side.classList.toggle("open") }, "☰"),
    h("div", { class: "search" }, q, results), h("span", { class: "spacer" }), h("div", { class: "rel" }, bell, bellMenu), h("div", { class: "rel" }, userBtn, userMenu));
  content = h("main", { class: "content" });
  clear(app).append(h("div", { class: "shell" }, side, h("div", { class: "main" }, top, content)));
  loadNotifs();
  notifTimer = setInterval(loadNotifs, 120000); // one tiny request every 2 minutes
}

async function openPage(key) {
  if (!visible(key)) key = "overview";
  side.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.key === key));
  clear(content).append(spinner());
  try {
    if (CUSTOM[key]) await CUSTOM[key][3](content);
    else await renderResource(content, key);
  } catch (e) { clear(content).append(errorBox(e)); }
}

boot();
