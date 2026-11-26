// Tiny DOM toolkit. Everything is built with createElement/textContent — user data is never parsed as HTML (no XSS).
import { state } from "./state.js";

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") value = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, "");
    else if (k in el && k !== "list" && k !== "style") el[k] = v;
    else el.setAttribute(k, v);
  }
  const add = (c) => {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  };
  kids.forEach(add);
  if (value !== undefined) el.value = value;
  return el;
}

export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

const SVG_NS = "http://www.w3.org/2000/svg";
export function icon(d, size = 16) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", size); svg.setAttribute("height", size);
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor"); svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const seg of d.split(" M")) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", seg.startsWith("M") ? seg : "M" + seg);
    svg.append(path);
  }
  return svg;
}
export const ICONS = {
  bell: "M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9 M13.7 21a2 2 0 0 1-3.4 0",
  menu: "M4 7h16 M4 12h16 M4 17h16",
};
export const spinner = () => h("div", { class: "spinner", role: "status", "aria-label": "Loading" });

// Spec labels carry emoji for Discord; the dashboard reads better without them.
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2190}-\u{21FF}\u{2300}-\u{23FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]/gu;
export const plain = (s) => String(s ?? "").replace(EMOJI, "").replace(/\s{2,}/g, " ").trim();

export function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}`, role: "status" }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 4200);
}

// ── page chrome ──
export function pageHead({ kicker, title, sub, actions } = {}) {
  return h("div", { class: "page-head" },
    kicker ? h("div", { class: "kicker" }, kicker) : null,
    h("div", { class: "page-head-row" }, h("h1", {}, title), actions ? h("div", { class: "page-actions" }, actions) : null),
    sub ? h("p", { class: "page-sub" }, sub) : null);
}

export function tabPage(container, { kicker, title, sub, actions, tabs, initial }) {
  clear(container);
  const bar = h("div", { class: "tabs", role: "tablist" });
  const panel = h("div", { class: "tab-panel" });
  container.append(pageHead({ kicker, title, sub, actions }), bar, panel);
  let token = 0;
  const show = async (key) => {
    const tab = tabs.find((t) => t.key === key) || tabs[0];
    [...bar.children].forEach((b) => {
      const on = b.dataset.tab === tab.key;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    const mine = ++token;
    clear(panel).append(h("div", { class: "tab-body" }));
    const body = panel.firstChild;
    try {
      await tab.render(body);
    } catch (e) {
      if (mine === token) clear(body).append(errorBox(e));
    }
  };
  tabs.forEach((t) => bar.append(h("button", { class: "tab", "data-tab": t.key, role: "tab", type: "button", onclick: () => show(t.key) }, t.label)));
  show(initial || tabs[0].key);
  return show;
}

// ── menus (one document listener, so nothing fights over clicks) ──
const menus = [];

export function closeMenus() {
  menus.splice(0).forEach((m) => m.close());
}

export function menu(trigger, build, { className = "", label } = {}) {
  const panel = h("div", { class: `menu hidden ${className}`.trim() });
  const host = h("div", { class: "menu-host" }, trigger, panel);
  const close = () => {
    panel.classList.add("hidden");
    trigger.setAttribute("aria-expanded", "false");
  };
  trigger.setAttribute("aria-haspopup", "true");
  trigger.setAttribute("aria-expanded", "false");
  if (label) trigger.setAttribute("aria-label", label);
  trigger.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (panel.classList.contains("hidden")) {
      closeMenus();
      trigger.setAttribute("aria-expanded", "true");
      panel.classList.remove("hidden");
      build(panel, close);
    } else {
      close();
    }
  });
  panel.addEventListener("click", (e) => {
    const item = e.target.closest("a, button.item");
    if (item) setTimeout(close, 0);
  });
  menus.push({ host, close });
  return host;
}

// ── modal ──
const openModals = [];
export function closeModals() {
  openModals.splice(0).forEach((m) => m.close());
}

export function modal(title, body, buttons = [], { sub, wide = false, onClose } = {}) {
  const root = document.getElementById("modal-root");
  function close() {
    document.removeEventListener("keydown", onKey);
    back.remove();
    const i = openModals.indexOf(handle);
    if (i >= 0) openModals.splice(i, 1);
    onClose?.();
  }
  function onKey(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  }
  const btnEls = buttons.map((b) => h("button", { class: `btn ${b.cls || ""}`, type: "button", onclick: () => b.onClick(close, btnEls) }, b.label));
  const back = h("div", { class: "backdrop", onmousedown: (e) => e.target === back && close() },
    h("div", { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "modal-head" }, h("h2", {}, title), h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: close }, "✕")),
      sub ? h("p", { class: "page-sub" }, sub) : null,
      body,
      buttons.length ? h("div", { class: "modal-actions" }, btnEls) : null));
  document.addEventListener("keydown", onKey);
  root.append(back);
  const handle = { close, el: back };
  openModals.push(handle);
  return handle;
}

export function confirmDialog(title, message, { danger = true, confirmLabel = "Confirm" } = {}) {
  return new Promise((resolve) => {
    const m = modal(title, h("p", { class: "page-sub" }, message), [
      { label: "Cancel", onClick: (c) => { c(); resolve(false); } },
      { label: confirmLabel, cls: danger ? "danger" : "primary", onClick: (c) => { c(); resolve(true); } },
    ]);
    m.el.addEventListener("mousedown", (e) => e.target === m.el && resolve(false));
  });
}

// ── picked files ──
// Reads an image as a data URL (never a blob: URL — the dashboard's CSP allows only img-src 'self' https: data:) and
// shrinks it to at most 1600 px wide, so a screenshot stays a few hundred KB on the way to the API.
export function readImageFile(file) {
  const bad = () => new Error("That image couldn't be read — use a PNG, JPEG or WebP screenshot.");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(bad());
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, 1600 / (img.width || 1600));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.width * scale));
        canvas.height = Math.max(1, Math.round(img.height * scale));
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => reject(bad());
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}

// Reads a picked text export (.json / .txt) for a paste box, refusing what the box couldn't hold.
export async function readTextFile(file, maxChars = 20000) {
  if (file.size > 1024 * 1024) throw new Error("That file is over 1 MB — this export is much smaller.");
  let text;
  try { text = await file.text(); } catch { throw new Error("That file couldn't be read — pick the .json or .txt export."); }
  if (!text.trim()) throw new Error("That file is empty.");
  if (text.length > maxChars) {
    throw new Error(`That file is too big (${text.length.toLocaleString()} characters) — the box holds ${maxChars.toLocaleString()}.`);
  }
  return text;
}

// ── formatting ──
export function fmtDate(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone: state.prefs.timezone || "UTC", dateStyle: "medium", ...(withTime ? { timeStyle: "short" } : {}) }).format(d);
  } catch { return d.toLocaleString(); }
}

export const nameOfChannel = (id) => {
  const c = state.gmeta?.channels.find((x) => x.id === String(id));
  return c ? `#${c.name}` : id ? `#${id}` : "—";
};
export const nameOfRole = (id) => {
  const r = state.gmeta?.roles.find((x) => x.id === String(id));
  return r ? `@${r.name}` : id ? `@${id}` : "—";
};

export function setAccent(hex) {
  document.documentElement.style.setProperty("--accent", hex);
}

export function errorBox(e) {
  return h("div", { class: "card" }, h("h2", {}, "Something went wrong"), h("p", { class: "page-sub" }, e.message || String(e)));
}

export function barChart(points, tip = (p) => `${p.label}: ${p.value}`) {
  const max = Math.max(1, ...points.map((p) => p.value));
  return h("div", { class: "bars" }, points.map((p) => {
    const b = h("div", { class: "bar", "data-tip": tip(p) });
    b.style.height = `${Math.max(2, (p.value / max) * 100)}%`;
    return b;
  }));
}

export function meter(frac) {
  const i = h("i");
  i.style.width = `${Math.round(Math.max(0, Math.min(1, frac)) * 100)}%`;
  return h("div", { class: "meter" }, i);
}
