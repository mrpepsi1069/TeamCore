// Member directory: who is in the server, their dashboard access, their team and their Discord roles.
import { state, can } from "./state.js";
import { g } from "./api.js";
import { h, clear, spinner, toast, modal, errorBox, pageHead, fmtDate } from "./ui.js";

const LEVEL_LABEL = { viewer: "Viewer", staff: "Staff", manager: "Manager" };
const ROSTER_STATUS = ["active", "reserve", "injured", "inactive"];

export async function membersPage(c) {
  clear(c);
  const view = { items: [], roles: [], notice: null, syncedAt: null, q: "", team: "", level: "" };

  const syncBtn = h("button", { class: "btn small", disabled: !can("staff") || null, onclick: async () => {
    syncBtn.disabled = true;
    try {
      const r = await g("/members/sync", { method: "POST" });
      toast(r.notice || `Synced${r.synced ? ` ${r.synced} member(s)` : ""}`, r.queued ? "warn" : "");
      await load();
    } catch (e) { toast(e.message, "err"); }
    syncBtn.disabled = false;
  } }, "Sync members");

  const search = h("input", { type: "search", placeholder: "Search by name or ID", maxLength: 80, "aria-label": "Search members" });
  const teamFilter = h("select", { "aria-label": "Filter by team" });
  const levelFilter = h("select", { "aria-label": "Filter by access level" });
  const table = h("div", {});
  const note = h("div", {});

  c.append(pageHead({
    kicker: "Club",
    title: "Members",
    sub: "Everyone in this server. Give dashboard access, put people on a team, and manage Discord roles without leaving the console.",
    actions: [syncBtn],
  }), note);

  const bar = h("div", { class: "card tight filters" },
    h("div", { class: "search" }, search), teamFilter, levelFilter, h("span", { class: "spacer" }), h("span", { class: "sub nowrap" }, ""));
  c.append(bar, table);

  const countLabel = bar.lastChild;
  let query = "";

  const matches = () => {
    query = search.value.trim().toLowerCase();
    return view.items.filter((m) => {
      if (query && !(`${m.display_name} ${m.username} ${m.user_id}`).toLowerCase().includes(query)) return false;
      if (view.team && !m.teams.some((t) => String(t.team_id) === view.team)) return false;
      if (view.level && (m.level || "") !== view.level) return false;
      return true;
    });
  };

  const render = () => {
    const rows = matches();
    countLabel.textContent = `${rows.length} of ${view.items.length}`;
    clear(table);
    if (!view.items.length) {
      table.append(h("div", { class: "card empty" }, view.notice || "No members found yet — press Sync members."));
      return;
    }
    if (!rows.length) { table.append(h("div", { class: "card empty" }, "No members match those filters.")); return; }
    const cells = (m) => [
      h("td", {}, h("div", { class: "member-cell" },
        m.avatar ? h("img", { class: "avatar", src: m.avatar, alt: "", referrerpolicy: "no-referrer" }) : h("span", { class: "avatar initial" }, (m.display_name || "?").charAt(0)),
        h("div", { class: "who" }, h("b", {}, m.display_name),
          h("span", {}, m.username && m.username !== m.display_name ? m.username : m.user_id)))),
      h("td", {}, m.teams.length
        ? h("div", { class: "row" }, m.teams.map((t) => h("span", { class: "chip solid" }, t.team_name + (t.position ? ` · ${t.position}` : ""))))
        : h("span", { class: "sub" }, "—")),
      h("td", { class: "nowrap" }, h("b", {}, m.cash_display || "$0 Cash")),
      h("td", {}, m.owns_server ? h("span", { class: "badge plain" }, "Owner")
        : m.level ? h("span", { class: "badge approved plain" }, LEVEL_LABEL[m.level] || m.level)
          : h("span", { class: "sub" }, "—")),
      h("td", {}, h("span", { class: "sub" }, m.role_ids.length ? `${m.role_ids.length} role(s)` : "—")),
      h("td", { class: "nowrap" }, h("span", { class: "sub" }, fmtDate(m.joined_at, false))),
      h("td", {}, h("div", { class: "row end" }, h("button", {
        class: "btn small", disabled: !can("manager") || null, onclick: () => manage(m, load),
      }, "Manage"))),
    ];
    table.append(h("div", { class: "table-wrap" },
      h("table", {},
        h("thead", {}, h("tr", {}, ["Member", "Team", "Cash", "Dashboard access", "Discord roles", "Joined", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, rows.map((m) => h("tr", {}, cells(m)))))));
  };

  const load = async () => {
    clear(table).append(spinner());
    try {
      const data = await g("/members");
      view.items = data.items || [];
      view.roles = data.assignable_roles || [];
      view.notice = data.notice;
      view.syncedAt = data.synced_at;
      clear(note);
      if (data.notice) note.append(h("div", { class: "card tight" }, h("p", { class: "page-sub" }, data.notice)));
      if (view.syncedAt) note.append(h("p", { class: "help" }, `Member list synced ${fmtDate(view.syncedAt)}.`));
      // keep the team filter in sync with the data
      const teams = new Map();
      view.items.forEach((m) => m.teams.forEach((t) => teams.set(String(t.team_id), t.team_name)));
      clear(teamFilter).append(h("option", { value: "" }, "All teams"),
        [...teams].sort((a, b) => String(a[1]).localeCompare(String(b[1]))).map(([id, name]) => h("option", { value: id, selected: id === view.team }, name)));
      clear(levelFilter).append(h("option", { value: "" }, "All access"),
        ["viewer", "staff", "manager"].map((v) => h("option", { value: v, selected: v === view.level }, LEVEL_LABEL[v])));
      render();
    } catch (e) { clear(table).append(errorBox(e)); }
  };

  search.addEventListener("input", render);
  teamFilter.addEventListener("change", () => { view.team = teamFilter.value; render(); });
  levelFilter.addEventListener("change", () => { view.level = levelFilter.value; render(); });

  await load();
}

function manage(m, onSaved) {
  const current = m.teams[0] || null;
  const access = h("select", {}, ["", "viewer", "staff", "manager"].map((v) =>
    h("option", { value: v, selected: (m.level || "") === v }, v ? LEVEL_LABEL[v] : "No dashboard access")));
  const team = h("select", {}, h("option", { value: "" }, "No team"),
    state.gmeta.teams.map((t) => h("option", { value: String(t.id), selected: current && current.team_id === t.id }, t.name)));
  const position = h("input", { value: current?.position || "", maxLength: 40, placeholder: "Head coach, QB, …" });
  const jersey = h("input", { type: "number", min: 0, max: 999, value: current?.jersey ?? "", placeholder: "#" });
  const status = h("select", {}, ROSTER_STATUS.map((v) => h("option", { value: v, selected: (current?.status || "active") === v }, v)));
  const roles = h("select", { multiple: true, size: Math.min(6, Math.max(3, state.gmeta.roles.length)) },
    state.gmeta.roles.filter((r) => r.id !== state.gid && !r.managed)
      .map((r) => h("option", { value: r.id, selected: m.role_ids.includes(r.id) }, r.name)));

  const save = async (body, path = `/members/${m.user_id}`, method = "PATCH") => {
    try {
      const r = await g(path, { method, body });
      toast(r.notice || "Saved", r.queued ? "warn" : "");
      onSaved?.();
      return r;
    } catch (e) { toast(e.errors ? Object.values(e.errors)[0] : e.message, "err"); return null; }
  };

  const body = h("div", {},
    h("div", { class: "member-cell" },
      m.avatar ? h("img", { class: "avatar", src: m.avatar, alt: "", referrerpolicy: "no-referrer" }) : h("span", { class: "avatar initial" }, (m.display_name || "?").charAt(0)),
      h("div", { class: "who" }, h("b", {}, m.display_name), h("span", {}, `${m.username || ""} · ${m.user_id}`))),
    h("p", { class: "help" }, `Cash balance: ${m.cash_display || "$0 Cash"} — /balance, /pay and /casino in Discord move it.`),
    h("h3", { class: "section-label" }, "Dashboard access"),
    can("admin") ? h("div", { class: "row stretch" }, access,
      h("span", { class: "sub" }, "Viewer reads, staff actions, manager runs the server.")) : h("p", { class: "page-sub" }, "Only admins can change dashboard access."),
    h("h3", { class: "section-label" }, "Team"),
    h("div", { class: "row stretch" }, team, position, jersey, status),
    h("h3", { class: "section-label" }, "Discord roles"),
    can("admin") ? h("div", {}, roles, h("p", { class: "help" }, "Hold Ctrl/Cmd to pick several. Changes are applied by the bot; roles above its own highest role can't be changed."))
      : h("p", { class: "page-sub" }, "Only admins can change Discord roles."));

  const buttons = [
    { label: "Close", onClick: (close) => close() },
    { label: "Save access", cls: "primary", onClick: async () => {
      if (!can("admin")) return;
      await save({ level: access.value, username: m.username || "" });
    } },
    { label: "Save team", onClick: async () => {
      await save({ team_id: team.value === "" ? null : Number(team.value), position: position.value, jersey: jersey.value, status: status.value, username: m.username || "" });
    } },
    { label: "Save roles", onClick: async () => {
      if (!can("admin")) return;
      const chosen = [...roles.selectedOptions].map((o) => o.value);
      await save({ role_ids: chosen }, `/members/${m.user_id}/roles`, "PUT");
    } },
  ];
  modal(`Manage ${m.display_name}`, body, buttons, { wide: true });
}
