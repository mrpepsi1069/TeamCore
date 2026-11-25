// Generic, spec-driven table + form for every resource. The field list comes from the backend (/api/meta) so the
// browser and the server always validate the same shape.
import { state, can } from "./state.js";
import { g } from "./api.js";
import { h, clear, spinner, toast, modal, confirmDialog, fmtDate, nameOfChannel, nameOfRole, errorBox } from "./ui.js";

const isoToLocalInput = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const intToHex = (n) => "#" + Number(n ?? 0xff7a1a).toString(16).padStart(6, "0");

// ───────── form fields ─────────
function option(value, label, selected) { return h("option", { value, selected: selected || null }, label); }

function fieldInput(f, value) {
  const m = state.gmeta;
  const id = `f_${f.name}`;
  const req = !!f.required;
  let el, get;
  const pickList = (items, mapper, multi) => {
    const sel = h("select", { id, multiple: multi || null });
    if (!multi && !req) sel.append(option("", "— none —"));
    items.forEach((it) => { const [v, l] = mapper(it); sel.append(option(v, l, multi ? (value || []).includes(v) : String(value ?? "") === String(v))); });
    return sel;
  };

  if (f.picker === "channel") {
    el = pickList(m.channels.filter((c) => ["text", "news"].includes(c.type)), (c) => [c.id, `#${c.name}${c.category ? ` — ${c.category}` : ""}`]);
    get = () => el.value || undefined;
  } else if (f.picker === "role") {
    el = pickList(m.roles.filter((r) => r.id !== state.gid), (r) => [r.id, `@${r.name}`]);
    get = () => el.value || undefined;
  } else if (f.picker === "roles") {
    el = pickList(m.roles.filter((r) => r.id !== state.gid && !r.managed), (r) => [r.id, `@${r.name}`], true);
    get = () => [...el.selectedOptions].map((o) => o.value);
  } else if (f.picker === "team") {
    el = pickList(m.teams, (t) => [String(t.id), t.name]);
    get = () => (el.value === "" ? undefined : Number(el.value));
  } else if (["competition", "division", "timezone"].includes(f.picker)) {
    const listId = `dl_${f.picker}`;
    const items = f.picker === "competition" ? m.competitions : f.picker === "division" ? [...new Set(m.divisions.map((d) => d.name))] : m.timezones;
    el = h("input", { id, list: listId, value: value ?? f.default ?? (f.picker === "timezone" ? state.prefs.timezone : "") , maxLength: f.max || 100 });
    const dl = h("datalist", { id: listId }, items.map((x) => h("option", { value: x })));
    get = () => el.value.trim() || undefined;
    el = h("div", {}, el, dl); const inner = el.firstChild; get = () => inner.value.trim() || undefined;
  } else if (f.type === "bool") {
    el = h("input", { id, type: "checkbox", checked: value ?? f.default ?? false });
    get = () => el.checked;
  } else if (f.type === "select") {
    el = h("select", { id }, f.choices.map((c) => option(String(c), String(c).replace(/_/g, " "), String(value ?? f.default ?? f.choices[0]) === String(c))));
    get = () => el.value;
  } else if (f.type === "text" || f.type === "lines" || f.type === "embed_fields") {
    const v = Array.isArray(value) ? (f.type === "embed_fields" ? value.map((x) => `${x.name} | ${x.value} | ${x.inline ? "yes" : "no"}`) : value).join("\n") : value ?? "";
    el = h("textarea", { id, value: v, maxLength: f.type === "text" ? f.max || 4000 : null, placeholder: f.type === "lines" ? "One per line" : f.type === "embed_fields" ? "name | value | yes/no" : "" });
    get = () => el.value;
  } else if (f.type === "int") {
    el = h("input", { id, type: "number", value: value ?? f.default ?? "", min: f.min ?? null, max: f.max ?? null });
    get = () => (el.value === "" ? undefined : Number(el.value));
  } else if (f.type === "datetime") {
    el = h("input", { id, type: "datetime-local", value: isoToLocalInput(value) });
    get = () => (el.value ? new Date(el.value).toISOString() : undefined);
  } else if (f.type === "color") {
    el = h("input", { id, type: "color", value: intToHex(value ?? f.default) });
    get = () => el.value;
  } else if (f.type === "url") {
    el = h("input", { id, type: "url", value: value ?? "", placeholder: "https://…", autocomplete: "off" });
    get = () => el.value.trim() || undefined;
  } else {
    el = h("input", { id, type: "text", value: value ?? f.default ?? "", maxLength: f.max || 200, autocomplete: "off" });
    get = () => el.value.trim() || undefined;
  }
  const label = f.label + (f.type === "datetime" ? " (your local time)" : "");
  const wrap = h("div", { class: "field", dataset: { name: f.name } }, h("label", { for: id }, label + (req ? " *" : "")), el, f.help ? h("div", { class: "help" }, f.help) : null, h("div", { class: "err" }));
  return { wrap, get, f };
}

export function openForm(spec, row, onSaved) {
  const editing = !!row;
  const inputs = spec.fields.filter((f) => !f.readonly && !(editing && f.create_only)).map((f) => fieldInput(f, row?.[f.name] ?? (f.name === "role_ids" ? (row?.roles || []).map((r) => r.role_id) : undefined)));
  const body = h("div", {}, inputs.map((i) => i.wrap), h("div", { class: "err", id: "form-err" }));
  const showErrors = (errors) => {
    body.querySelectorAll(".err").forEach((e) => (e.textContent = ""));
    for (const [k, v] of Object.entries(errors || {})) {
      const holder = k === "_" ? body.querySelector("#form-err") : body.querySelector(`[data-name="${k}"] .err`) || body.querySelector("#form-err");
      holder.textContent = k === "_" || !body.querySelector(`[data-name="${k}"]`) ? `${k === "_" ? "" : k + ": "}${v}` : v;
    }
  };
  modal(`${editing ? "Edit" : "New"} ${spec.label.replace(/s$/, "")}`, body, [
    { label: "Cancel", onClick: (c) => c() },
    { label: editing ? "Save changes" : "Create", cls: "primary", onClick: async (close, btns) => {
      const data = {};
      inputs.forEach((i) => { const v = i.get(); if (v !== undefined) data[i.f.name] = v; else if (editing) data[i.f.name] = ""; });
      btns.forEach((b) => (b.disabled = true));
      try {
        const res = editing ? await g(`/r/${spec.key}/${row.id}`, { method: "PATCH", body: data }) : await g(`/r/${spec.key}`, { method: "POST", body: data });
        close();
        toast(res.notice || (editing ? "Saved" : "Created"), res.queued ? "warn" : "");
        onSaved?.(res);
      } catch (e) {
        showErrors(e.errors || { _: e.message });
        btns.forEach((b) => (b.disabled = false));
      }
    } },
  ]);
}

// ───────── cells ─────────
function cell(spec, col, row) {
  const v = row[col];
  if (v == null || v === "") return "—";
  if (col === "status" || col === "level") return h("span", { class: `badge ${v}` }, String(v).replace(/_/g, " "));
  if (col.endsWith("_at") || ["start", "end", "date", "played_at", "send_at", "remind_at", "starts_at"].includes(col)) return fmtDate(v);
  if (col === "channel_id") return nameOfChannel(v);
  if (col === "role_id" || col === "ping_role_id" || col === "required_role_id") return nameOfRole(v);
  if (typeof v === "boolean") return v ? "✓" : "✗";
  if (col === "user_id" || col === "author_id" || col.endsWith("_user_id")) return row[col.replace("_id", "_name")] || row.username || String(v);
  if (col === "logo" || col === "image") return h("a", { href: v, target: "_blank", rel: "noopener noreferrer" }, "view");
  if (Array.isArray(v)) return v.join(", ");
  const s = String(v);
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
}
const headLabel = (spec, col) => (spec.fields.find((f) => f.name === col)?.label) || col.replace(/_/g, " ").replace(/\bid\b/, "ID").replace(/^./, (c) => c.toUpperCase());

// ───────── page ─────────
export async function renderResource(container, key, { embedded = false } = {}) {
  const spec = state.specs[key];
  let page = 1, q = "", status = "";
  const statusField = spec.fields.find((f) => f.name === "status" && f.type === "select");

  const head = embedded ? null : h("div", {}, h("h1", {}, `${spec.icon} ${spec.label}`), spec.help ? h("p", { class: "sub" }, spec.help) : null);
  const table = h("div", {});
  const searchBox = spec.search?.length ? h("input", { placeholder: "Search…", style: null, oninput: (e) => { clearTimeout(searchBox._t); searchBox._t = setTimeout(() => { q = e.target.value; page = 1; load(); }, 300); } }) : null;
  const statusSel = statusField ? h("select", { onchange: (e) => { status = e.target.value; page = 1; load(); } }, option("", "All statuses"), statusField.choices.map((c) => option(c, c.replace(/_/g, " ")))) : null;
  const newBtn = spec.create && can(spec.write) ? h("button", { class: "btn primary", onclick: () => openForm(spec, null, load) }, "＋ New") : null;
  const bar = h("div", { class: "row card" }, searchBox && h("div", { class: "search" }, searchBox), statusSel, h("span", { class: "spacer" }), newBtn);
  clear(container).append(...[head, bar, table].filter(Boolean));

  async function runAction(row, a) {
    let body = {};
    if (a.params?.length) {
      const inputs = a.params.map((p) => fieldInput(p, undefined));
      const ok = await new Promise((resolve) => modal(a.label, h("div", {}, inputs.map((i) => i.wrap)), [
        { label: "Cancel", onClick: (c) => { c(); resolve(false); } },
        { label: a.label, cls: a.danger ? "danger" : "primary", onClick: (c) => { inputs.forEach((i) => { const v = i.get(); if (v !== undefined) body[i.f.name] = v; }); c(); resolve(true); } }]));
      if (!ok) return;
    } else if (a.confirm && !(await confirmDialog(a.label, "Are you sure?", { danger: !!a.danger, confirmLabel: a.label }))) return;
    try {
      const res = await g(`/r/${spec.key}/${row.id}/actions/${a.name}`, { method: "POST", body });
      toast(res.notice || `${a.label} ✓`, res.queued ? "warn" : "");
      load();
    } catch (e) { toast(e.message, "err"); }
  }

  async function load() {
    clear(table).append(spinner());
    try {
      const qs = new URLSearchParams({ page, per_page: 25, ...(q ? { q } : {}), ...(status ? { status } : {}) });
      const data = await g(`/r/${key}?${qs}`);
      clear(table);
      if (!data.items.length) { table.append(h("div", { class: "empty card" }, q || status ? "Nothing matches your filters." : "Nothing here yet.", can(spec.write) && spec.create ? " Use “New” to add the first one." : "")); return; }
      const showActions = (spec.update && can(spec.write)) || can(spec.delete) || spec.actions.length;
      table.append(
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, spec.columns.map((c) => h("th", {}, headLabel(spec, c))), showActions ? h("th", {}, "") : null)),
          h("tbody", {}, data.items.map((row) => h("tr", {},
            spec.columns.map((c) => h("td", {}, cell(spec, c, row))),
            showActions ? h("td", {}, h("div", { class: "row end" },
              spec.actions.filter((a) => can(a.level || spec.write) && Object.entries(a.when || {}).every(([k, v]) => row[k] === v))
                .map((a) => h("button", { class: `btn small ${a.danger ? "danger" : ""}`, onclick: () => runAction(row, a) }, a.label)),
              spec.update && can(spec.write) ? h("button", { class: "btn small", onclick: () => openForm(spec, row, load) }, "Edit") : null,
              can(spec.delete) ? h("button", { class: "btn small danger", onclick: async () => {
                if (!(await confirmDialog("Delete", `Delete this ${spec.label.toLowerCase().replace(/s$/, "")}? This can't be undone.`, { confirmLabel: "Delete" }))) return;
                try { const r = await g(`/r/${key}/${row.id}`, { method: "DELETE" }); toast(r.notice || "Deleted", r.notice ? "warn" : ""); load(); } catch (e) { toast(e.message, "err"); }
              } }, "Delete") : null)) : null)))),
        ),
        h("div", { class: "pager" }, h("span", {}, `${data.total} total`),
          h("div", { class: "row" },
            h("button", { class: "btn small", disabled: page <= 1 || null, onclick: () => { page--; load(); } }, "← Prev"),
            h("span", {}, `Page ${page} of ${Math.max(1, Math.ceil(data.total / data.per_page))}`),
            h("button", { class: "btn small", disabled: page * data.per_page >= data.total || null, onclick: () => { page++; load(); } }, "Next →"))));
    } catch (e) { clear(table).append(errorBox(e)); }
  }
  await load();
}
