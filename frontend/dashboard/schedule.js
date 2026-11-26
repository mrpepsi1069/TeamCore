// TeamCore dashboard — gametimes and multi-time polls.
// Both are posted by the bot into the server's gametime channel, so the Discord message and buttons are the same
// objects /gametime and /times build. The dashboard only collects the details.
import { state, can } from "./state.js";
import { g } from "./api.js";
import { h, clear, toast, errorBox, fmtDate, tabPage } from "./ui.js";

const opt = (v, label, on) => h("option", { value: v, selected: on || null }, label);
const text = (v) => String(v ?? "").trim();

export function schedulePage(c, initial = "gametime") {
  return tabPage(c, {
    kicker: "Scheduling",
    title: "Gametimes & times",
    sub: "Posted by the bot to your gametime channel, with the same poll buttons as the Discord commands.",
    tabs: [
      { key: "gametime", label: "Gametime", render: (el) => gametimeTab(el) },
      { key: "times", label: "Time options", render: (el) => timesTab(el) },
    ],
    initial,
  });
}

function roleSelect(id) {
  const roles = state.gmeta.roles.filter((r) => r.id !== state.gid && !r.managed);
  // Deliberately starts empty: posting pings whatever is selected, so it has to be a real choice.
  return h("select", { id }, [opt("", "— choose a role —"), ...roles.map((r) => opt(r.id, `@${r.name}`))]);
}

function channelCard(view, need) {
  if (view.channel) {
    return h("div", { class: "row between card tight" },
      h("span", {}, "Posts to ", h("b", {}, `#${view.channel.name}`)),
      h("span", { class: "sub" }, need === "manager" ? "Managers and admins" : "Staff and above"));
  }
  return h("div", { class: "card" },
    h("h2", {}, "No gametime channel set"),
    h("p", { class: "page-sub" }, "The bot can't post a poll until a gametime channel is picked. Settings → Gametime channel — the slash commands use it too."),
    h("a", { class: "btn small", href: `#/g/${state.gid}/settings` }, "Open settings"));
}

function recent(view, kind) {
  const rows = view.items.filter((i) => i.kind === kind);
  if (!rows.length) {
    return h("div", { class: "card empty" }, kind === "gametime"
      ? "No gametimes posted yet — from here or with /gametime."
      : "No time polls posted yet — from here or with /times.");
  }
  const body = rows.map((i) => h("tr", {},
    h("td", {}, h("b", {}, i.league || "—")),
    h("td", {}, (i.times || []).join(" · ") || "—"),
    h("td", {}, i.role_name ? `@${i.role_name}` : "—"),
    h("td", {}, fmtDate(i.created_at)),
    h("td", { class: "num" }, i.url
      ? h("a", { class: "btn small ghost", href: i.url, target: "_blank", rel: "noopener" }, "Open")
      : "—")));
  const head = ["League", kind === "gametime" ? "Time" : "Options", "Role", "Posted", ""].map((x) => h("th", { class: x ? "" : "num" }, x));
  return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, head)), h("tbody", {}, body)));
}

function leagueInput(id) {
  return h("input", { list: id, placeholder: "e.g. NFA", maxLength: 80 });
}

function leagueList(id) {
  return h("datalist", { id }, state.gmeta.competitions.map((x) => h("option", { value: x })));
}

async function gametimeTab(el) {
  clear(el);
  let view;
  try { view = await g("/schedule"); }
  catch (e) { el.append(errorBox(e)); return; }

  const league = leagueInput("dl_sched_gm");
  const time = h("input", { placeholder: "e.g. Friday 8 PM EST", maxLength: 80 });
  const role = roleSelect("sched_gm_role");
  const dm = h("input", { type: "checkbox", checked: true });
  const allowed = can("staff");

  const sync = () => { submit.disabled = !allowed || !role.value; };
  const submit = h("button", { class: "btn primary", disabled: true, onclick: async () => {
    if (!role.value) return toast("Pick the role to ping first", "err");
    submit.disabled = true;
    try {
      const res = await g("/schedule/gametime", { method: "POST",
        body: { league: text(league.value), time: text(time.value), role_id: role.value, dm: dm.checked } });
      toast(res.notice || `Posted in #${res.channel || "the gametime channel"}`);
      gametimeTab(el);
    } catch (e) {
      toast(e.errors ? Object.values(e.errors).join(" · ") : e.message, "err");
      sync();
    }
  } }, "Post gametime");
  role.addEventListener("change", sync);
  sync();

  el.append(
    channelCard(view, "staff"),
    h("div", { class: "card" },
      h("h2", {}, "New gametime"),
      h("p", { class: "page-sub" }, "Same as /gametime — a “Can make / Can't make / Unsure” poll with buttons."),
      h("div", { class: "split" },
        h("div", { class: "field" }, h("label", {}, "League"), league),
        h("div", { class: "field" }, h("label", {}, "Game time"), time),
        h("div", { class: "field" }, h("label", {}, "Role to ping"), role),
        h("div", { class: "field" }, h("label", {}, "Direct messages"),
          h("label", { class: "inline" }, dm, " Also DM everyone with that role"))),
      h("div", { class: "row end" },
        allowed ? null : h("span", { class: "sub" }, "Staff can post gametimes."), submit)),
    h("h2", { class: "section-label" }, "Recent gametimes"),
    recent(view, "gametime"),
    leagueList("dl_sched_gm"));
}

async function timesTab(el) {
  clear(el);
  let view;
  try { view = await g("/schedule"); }
  catch (e) { el.append(errorBox(e)); return; }

  const league = leagueInput("dl_sched_tm");
  const role = roleSelect("sched_tm_role");
  const list = h("div", { class: "stack" });
  const allowed = can("manager");

  const addRow = (value = "") => {
    const input = h("input", { value, placeholder: "e.g. Saturday 7 PM EST", maxLength: 80 });
    const row = h("div", { class: "row" }, input,
      h("button", { class: "btn small ghost", type: "button", onclick: () => {
        if (list.children.length <= 2) return toast("Keep at least two options", "err");
        row.remove();
      } }, "Remove"));
    list.append(row);
    return input;
  };
  [0, 1, 2].forEach(() => addRow());

  const addBtn = h("button", { class: "btn small", type: "button", onclick: () => {
    if (list.children.length >= 6) return toast("Six options is the maximum", "err");
    addRow();
  } }, "Add another time");
  const read = () => [...list.querySelectorAll("input")].map((i) => text(i.value)).filter(Boolean);

  const sync = () => { submit.disabled = !allowed || !role.value; };
  const submit = h("button", { class: "btn primary", disabled: true, onclick: async () => {
    if (!role.value) return toast("Pick the role to ping first", "err");
    submit.disabled = true;
    try {
      const res = await g("/schedule/times", { method: "POST",
        body: { league: text(league.value), times: read(), role_id: role.value } });
      toast(res.notice || `Posted in #${res.channel || "the gametime channel"}`);
      timesTab(el);
    } catch (e) {
      toast(e.errors ? Object.values(e.errors).join(" · ") : e.message, "err");
      sync();
    }
  } }, "Post time poll");
  role.addEventListener("change", sync);
  sync();

  el.append(
    channelCard(view, "manager"),
    h("div", { class: "card" },
      h("h2", {}, "New time poll"),
      h("p", { class: "page-sub" }, "Same as /times — one button per option, so members can pick every time that works."),
      h("div", { class: "split" },
        h("div", { class: "field" }, h("label", {}, "League"), league),
        h("div", { class: "field" }, h("label", {}, "Role to ping"), role)),
      h("div", { class: "field" }, h("label", {}, "Time options (two to six)"), list),
      h("div", { class: "row" }, addBtn, h("span", { class: "spacer" })),
      h("div", { class: "row end" },
        allowed ? null : h("span", { class: "sub" }, "Only managers can post time polls."), submit)),
    h("h2", { class: "section-label" }, "Recent time polls"),
    recent(view, "times"),
    leagueList("dl_sched_tm"));
}
