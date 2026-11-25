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
export const spinner = () => h("div", { class: "spinner", role: "status" });

export function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 4000);
}

export function modal(title, body, buttons = [], { sub } = {}) {
  const root = document.getElementById("modal-root");
  const close = () => back.remove();
  const back = h("div", { class: "backdrop", onmousedown: (e) => e.target === back && close() },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true" },
      h("h2", {}, title), sub ? h("p", { class: "sub" }, sub) : null, body,
      h("div", { class: "row end", }, buttons.map((b) => h("button", { class: `btn ${b.cls || ""}`, onclick: () => b.onClick(close, btnEls) }, b.label)))));
  const btnEls = [...back.querySelectorAll(".row.end button")];
  root.append(back);
  return { close, el: back };
}

export function confirmDialog(title, message, { danger = true, confirmLabel = "Confirm" } = {}) {
  return new Promise((resolve) => {
    const m = modal(title, h("p", { class: "sub" }, message), [
      { label: "Cancel", onClick: (c) => { c(); resolve(false); } },
      { label: confirmLabel, cls: danger ? "danger" : "primary", onClick: (c) => { c(); resolve(true); } },
    ]);
    m.el.addEventListener("mousedown", (e) => e.target === m.el && resolve(false));
  });
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

export const nameOfChannel = (id) => { const c = state.gmeta?.channels.find((x) => x.id === String(id)); return c ? `#${c.name}` : id ? `#${id}` : "—"; };
export const nameOfRole = (id) => { const r = state.gmeta?.roles.find((x) => x.id === String(id)); return r ? `@${r.name}` : id ? `@${id}` : "—"; };

export function setAccent(hex) {
  document.documentElement.style.setProperty("--accent", hex);
  document.documentElement.style.setProperty("--accent2", `color-mix(in srgb, ${hex} 70%, white)`);
}

export function errorBox(e) {
  return h("div", { class: "card" }, h("b", {}, "Something went wrong"), h("p", { class: "sub" }, e.message || String(e)));
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
