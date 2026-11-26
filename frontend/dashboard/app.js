// TeamCore dashboard — shell, routing, login, server picker.
import { state, can } from "./state.js";
import { api, g, API, getToken, setToken } from "./api.js";
import { h, clear, spinner, toast, setAccent, errorBox, plain, menu, closeMenus, closeModals, icon, ICONS } from "./ui.js";
import { renderResource } from "./resource.js";
import { membersPage } from "./members.js";
import { schedulePage } from "./schedule.js";
import * as P from "./pages.js";

const app = document.getElementById("app");

// Hand-built pages: key -> [label, minimum level, renderer]
const CUSTOM = {
  overview: ["Overview", "viewer", P.overviewPage],
  members: ["Members", "staff", membersPage],
  schedule: ["Scheduling", "staff", (c) => schedulePage(c, "gametime")],
  settings: ["Settings", "manager", P.settingsPage],
  verification: ["Verification", "manager", P.verificationPage],
  activity: ["Member activity", "viewer", P.activityPage],
  memberstats: ["Member growth", "viewer", P.memberStatsPage],
  moderation: ["Moderation", "staff", P.moderationPage],
  backups: ["Backups", "admin", P.backupsPage],
  export: ["Data export", "manager", P.exportPage],
  preferences: ["Preferences", "viewer", P.preferencesPage],
};
// Teams, rosters, leagues, divisions, matches and standings live in one place now — old links still land on the right tab.
const COMPETITION_TABS = {
  competition: "leagues", leagues: "leagues", teams: "teams", records: "teams", team_members: "rosters",
  competitions: "seasons", divisions: "divisions", matches: "matches", leaderboard: "standings",
};
for (const [key, tab] of Object.entries(COMPETITION_TABS)) CUSTOM[key] = ["Competition", "viewer", (c) => P.competitionPage(c, tab)];
CUSTOM.polls = ["Polls", "viewer", (c) => P.pollsPage(c, "active")];
CUSTOM.poll_archive = ["Polls", "viewer", (c) => P.pollsPage(c, "archive")];

const NAV = [
  ["", ["overview"]],
  ["Club", ["members", "competition"]],
  ["Community", ["polls", "giveaways", "suggestions", "announcements", "role_panels"]],
  ["Scheduling", ["schedule", "events", "countdowns", "scheduled_messages", "reminders"]],
  ["Players", ["player_profiles", "tryouts", "awards", "scouting_reports", "hall_of_fame"]],
  ["Automation", ["workflows", "webhooks"]],
  ["Insights", ["activity", "memberstats"]],
  ["Server", ["settings", "verification", "stats_channels", "moderation", "appeals", "staff_shifts", "staff_reports", "audit_logs", "backups", "export"]],
  ["", ["preferences"]],
];

const visible = (key) => {
  if (CUSTOM[key]) return can(CUSTOM[key][1]);
  const s = state.specs[key];
  return !!s && can(s.read);
};
const labelOf = (key) => (CUSTOM[key] ? CUSTOM[key][0] : plain(state.specs[key]?.label || key));

// One global handler each: no per-render listeners piling up, and menus always close on an outside click.
document.addEventListener("click", () => closeMenus());
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });

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
    state.user = me; state.prefs = me.preferences || state.prefs; state.specs = meta.resources; state.settingsSpec = meta.settings;
    state.guilds = guilds.guilds; state.botInfo = guilds.bot;
    setAccent(state.prefs.accent || "#FF7A1A");
  } catch (e) {
    if (e.status === 401) return loginScreen();
    return clear(app).append(h("div", { class: "center" }, errorBox(e)));
  }
  window.addEventListener("hashchange", route);
  route();
}

function loginScreen(error) {
  const msgs = {
    login_cancelled: "Login was cancelled.", bad_state: "Login expired — please try again.",
    token_exchange_failed: "Discord rejected the login. Try again.", profile_failed: "Couldn't read your Discord profile.",
  };
  clear(app).append(h("div", { class: "center" }, h("div", { class: "hero" },
    h("div", { class: "logo-lg" }, "TC"), h("h1", {}, "TeamCore"), 
    h("p", { class: "lead" }, "Leagues, teams, rosters and community — managed from the browser."),
    error ? h("p", { class: "err" }, msgs[error] || "Login failed.") : null,
    h("a", { class: "btn primary", href: `${API}/auth/login` }, "Sign in with Discord"),
    h("p", { class: "help" }, "We only request your username and server list. We never see your password."))));
}

// ───────── routing ─────────
async function route() {
  closeModals();
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
      clear(app).append(h("div", { class: "center" }, h("div", { class: "hero" }, errorBox(e),
        h("p", { class: "help" }, h("a", { href: "#/" }, "← Choose another server")))));
      return;
    }
    buildShell();
  }
  openPage(page);
}

async function serverPicker() {
  state.gid = null; state.gmeta = null;
  try { const r = await api("/api/guilds"); state.guilds = r.guilds; state.botInfo = r.bot; } catch { /* keep cached list */ }
  document.title = "TeamCore";
  const card = (gd) => h("button", { class: "server", onclick: () => (gd.bot_present ? (location.hash = `#/g/${gd.id}`) : window.open(gd.invite_url, "_blank", "noopener")) },
    gd.icon ? h("img", { class: "gicon", src: gd.icon, alt: "" }) : h("div", { class: "gicon" }, gd.name[0]),
    h("div", {}, h("b", {}, gd.name), h("div", { class: "sub" }, gd.bot_present ? `${gd.level} access` : "Add TeamCore to this server")));
  clear(app).append(h("div", { class: "content" },
    h("div", { class: "page-head" },
      h("div", { class: "kicker" }, "Signed in as " + (state.user?.username || "")),
      h("div", { class: "page-head-row" }, h("h1", {}, "Choose a server"),
        h("div", { class: "page-actions" },
          h("button", { class: "btn small", onclick: async () => { const r = await api("/api/guilds?refresh=true"); state.guilds = r.guilds; state.botInfo = r.bot; serverPicker(); } }, "Refresh list"),
          h("button", { class: "btn small ghost", onclick: signOut }, "Sign out"))),
      h("p", { class: "page-sub" }, "Servers you own or manage — or where an admin gave you dashboard access.")),
    botBanner(),
    state.guilds.length ? h("div", { class: "servers" }, state.guilds.map(card))
      : h("div", { class: "card empty" }, "No servers found. You need Administrator or Manage Server, or an admin must grant you access.")));
}

// Explains WHY servers show "add TeamCore" instead of leaving the user guessing.
function botBanner() {
  const b = state.botInfo, missing = state.guilds.filter((x) => !x.bot_present).length;
  if (!b || (!missing && b.online)) return null;
  const ago = b.last_seen ? Math.max(1, Math.round((Date.now() - new Date(b.last_seen)) / 60000)) : null;
  let title, text;
  if (!b.last_seen) {
    title = "The bot has never reported to this database";
    text = "Check that your bot host is running the current code and that its MONGODB_URI / DATABASE_URL points at the same cluster as this website. Then restart the bot and press Refresh list.";
  } else if (!b.online) {
    title = `The bot was last seen ${ago} minute(s) ago`;
    text = "It looks offline (free hosts can sleep). Wake or restart it, wait a minute, then press Refresh list.";
  } else if (!b.servers_synced) {
    title = "The bot is online but hasn't published any servers yet";
    text = "Restart the bot and watch its log for “Dashboard: published …”. If you don't see it, the bot is still on old code.";
  } else {
    title = `TeamCore is online and has synced ${b.servers_synced} server(s)`;
    text = "Servers without the bot — or where it joined in the last few minutes — will appear after a refresh.";
  }
  return h("div", { class: "card" }, h("h2", {}, title), h("p", { class: "page-sub" }, text));
}

async function signOut() {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* token may already be gone */ }
  setToken(null); location.hash = "#/"; location.reload();
}

// ───────── shell ─────────
let content, side, notifTimer;
let notifItems = [];

function buildShell() {
  clearInterval(notifTimer);
  closeMenus();
  const guild = state.gmeta.guild;
  const closeSide = () => side.classList.remove("open");
  side = h("aside", { class: "side" });

  const serverBtn = h("button", { class: "btn server-sel", onclick: () => (location.hash = "#/") },
    guild.icon ? h("img", { class: "avatar sm", src: guild.icon, alt: "" }) : h("span", { class: "avatar sm" }),
    h("b", {}, guild.name), h("span", { class: "spacer" }), h("span", { class: "chev" }, "⇄"));

  side.append(
    h("div", { class: "brand" }, h("div", { class: "logo" }, "TC"), h("span", {}, "TeamCore"), h("em", {}, "Console")),
    serverBtn,
    h("nav", { class: "nav" }, NAV.map(([group, keys]) => {
      const items = keys.filter(visible);
      if (!items.length) return null;
      return [group ? h("div", { class: "nav-group" }, group) : null,
        items.map((k) => h("a", { dataset: { key: k }, onclick: () => { location.hash = `#/g/${state.gid}/${k}`; closeSide(); } }, labelOf(k)))];
    })),
    h("div", { class: "side-foot" },
      h("div", { class: "row" }, h("span", { class: "badge plain" }, state.level),
        h("span", { class: "spacer" }),
        h("button", { class: "btn small ghost", onclick: () => { location.hash = `#/g/${state.gid}/preferences`; } }, "Settings"))));

  // search
  const results = h("div", { class: "results hidden" });
  const q = h("input", { type: "search", placeholder: "Search teams, players, suggestions…", "aria-label": "Search this server",
    oninput: () => {
      clearTimeout(q._t);
      q._t = setTimeout(async () => {
        if (q.value.trim().length < 2) return results.classList.add("hidden");
        try {
          const { results: r } = await g(`/search?q=${encodeURIComponent(q.value.trim())}`);
          // NB: spread the list — passing an array to append() stringifies the anchors into URLs.
          clear(results).append(...(r.length
            ? r.map((x) => h("a", { href: `#/g/${state.gid}/${x.page}`, onclick: () => { results.classList.add("hidden"); q.value = ""; } },
              x.label, h("span", { class: "menu-item-meta" }, String(x.page).replace(/_/g, " "))))
            : [h("div", { class: "menu-empty" }, "No matches")]));
          results.classList.remove("hidden");
        } catch (e) { clear(results).append(h("div", { class: "menu-empty" }, e.message)); results.classList.remove("hidden"); }
      }, 260);
    } });

  // notifications
  const bell = h("button", { class: "btn bell" }, icon(ICONS.bell, 17), h("span", { class: "badge-count hidden" }));
  const notifMenu = menu(bell, (panel) => {
    clear(panel).append(h("div", { class: "menu-head" }, "Notifications",
      h("button", { class: "btn small ghost", onclick: () => loadNotifs(true) }, "Refresh")));
    if (!notifItems.length) return panel.append(h("div", { class: "menu-empty" }, "You're all caught up."));
    notifItems.forEach((n) => panel.append(h("a", { href: `#/g/${state.gid}/${n.page}` },
      h("span", { class: "menu-item-text" }, n.text), h("span", { class: "menu-item-meta" }, "Open"))));
  }, { label: "Notifications" });

  // user menu
  const avatar = state.user?.avatar_url ? h("img", { class: "avatar sm", src: state.user.avatar_url, alt: "" }) : h("span", { class: "avatar sm" });
  const userBtn = h("button", { class: "btn user-chip" }, avatar, h("b", {}, state.user?.username || "Account"));
  const userMenu = menu(userBtn, (panel) => {
    clear(panel).append(
      h("div", { class: "menu-head" }, state.user?.username || "Account"),
      h("a", { href: `#/g/${state.gid}/preferences` }, "Preferences"),
      h("a", { href: "#/" }, "Switch server"),
      h("button", { class: "item", onclick: signOut }, "Sign out"));
  }, { label: "Account menu" });

  const top = h("header", { class: "topbar" },
    h("button", { class: "btn menu-btn ghost", "aria-label": "Menu", onclick: () => side.classList.toggle("open") }, icon(ICONS.menu, 18)),
    h("div", { class: "search" }, q, results),
    h("span", { class: "spacer" }),
    notifMenu, userMenu);

  content = h("main", { class: "content" });
  clear(app).append(h("div", { class: "shell" }, side, h("div", { class: "main" }, top, content)));
  loadNotifs();
  notifTimer = setInterval(loadNotifs, 180000); // one small request every 3 minutes
}

async function loadNotifs(manual = false) {
  const badge = document.querySelector(".bell .badge-count");
  try {
    const { items } = await g("/notifications");
    notifItems = items || [];
    if (badge) {
      badge.textContent = String(notifItems.length);
      badge.classList.toggle("hidden", !notifItems.length);
    }
    if (manual) toast(notifItems.length ? `${notifItems.length} notification(s)` : "Nothing new");
  } catch (e) {
    notifItems = [];
    if (badge) badge.classList.add("hidden");
    if (manual) toast(e.message, "err");
  }
}

async function openPage(key) {
  closeMenus();
  if (!visible(key)) key = "overview";
  side.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.key === key));
  document.title = `${labelOf(key)} · ${state.gmeta.guild.name} · TeamCore`;
  clear(content).append(spinner());
  try {
    if (CUSTOM[key]) await CUSTOM[key][2](content);
    else await renderResource(content, key);
  } catch (e) { clear(content).append(errorBox(e)); }
}

boot();
