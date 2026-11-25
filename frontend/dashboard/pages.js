// Hand-built pages (everything else is generated from specs by resource.js).
import { state, can } from "./state.js";
import { api, g } from "./api.js";
import { h, clear, spinner, toast, modal, confirmDialog, fmtDate, nameOfChannel, barChart, meter, errorBox, setAccent } from "./ui.js";
import { renderResource } from "./resource.js";

const wrap = async (c, title, sub, fn) => {
  clear(c).append(h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : null, spinner());
  try { const body = await fn(); c.lastChild.remove(); c.append(body); } catch (e) { c.lastChild.remove(); c.append(errorBox(e)); }
};
const opt = (v, l, sel) => h("option", { value: v, selected: sel || null }, l);

// ───────── overview ─────────
export function overviewPage(c) {
  return wrap(c, `${state.gmeta.guild.name}`, "Server overview", async () => {
    const o = await g("/overview");
    const stat = (n, label) => h("div", { class: "stat" }, h("b", {}, n ?? "—"), h("span", {}, label));
    return h("div", {},
      h("div", { class: "card row" },
        h("span", { class: "badge " + (o.bot_online ? "open" : "failed") }, o.bot_online ? "● Bot online" : "● Bot offline"),
        h("span", { class: "sub", }, `Your access: ${o.level}`), h("span", { class: "spacer" }),
        h("span", { class: "sub" }, o.server.synced_at ? `Server info synced ${fmtDate(o.server.synced_at)}` : ""),
        can("staff") ? h("button", { class: "btn small", onclick: async () => { const r = await g("/refresh", { method: "POST" }).catch((e) => ({ error: e.message })); toast(r.error || "Refresh requested", r.error ? "err" : ""); } }, "↻ Sync with Discord") : null),
      h("div", { class: "grid" },
        stat(o.server.member_count?.toLocaleString(), "Members"), stat(o.server.boosts, `Boosts (tier ${o.server.tier ?? 0})`),
        stat(o.counts.teams, "Teams"), stat(o.counts.matches, "Matches logged"), stat(o.counts.open_polls, "Open polls"),
        stat(o.counts.open_giveaways, "Active giveaways"), stat(o.counts.pending_suggestions, "Pending suggestions"), stat(o.counts.pending_appeals, "Pending appeals"), stat(o.counts.players, "Player profiles")),
      h("div", { class: "card" }, h("h2", {}, "Recent dashboard activity"),
        o.recent.length ? h("div", {}, o.recent.map((r) => h("div", { class: "row" }, h("b", {}, r.user_name), h("span", {}, r.action), h("span", { class: "sub" }, r.target || ""), h("span", { class: "spacer" }), h("span", { class: "sub" }, fmtDate(r.created_at)))))
          : h("p", { class: "sub" }, "Nothing yet.")));
  });
}

// ───────── settings + verification ─────────
const LABELS = { admin_role: "Admin role", franchise_owner_role: "Franchise Owner role", general_manager_role: "General Manager role", head_coach_role: "Head Coach role",
  assistant_coach_role: "Assistant Coach role", verify_role: "Verification role", log_channel: "Log channel", announce_channel: "Announcements channel", gametime_channel: "Gametime channel",
  matchmaking_channel: "Matchmaking channel", contract_channel: "Contract channel", awards_channel: "Awards channel", league_log_channel: "League log channel",
  suggestion_channel: "Suggestions channel", memberstats_channel: "Member report channel", verify_channel: "Verification channel", activity_enabled: "Track message activity",
  activity_channels: "Only track these channels (empty = all)", timezone: "Server timezone" };

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
    return h("div", {}, h("label", {}, LABELS[key] || key), el);
  };
  const types = Object.fromEntries(state.settingsSpec.map((s) => [s.key, s.type]));
  const form = h("div", {}, keys.map((k) => row(k, types[k])), h("datalist", { id: "dl_tz" }, m.timezones.map((z) => h("option", { value: z }))));
  const read = () => Object.fromEntries(Object.entries(inputs).map(([k, { el, type }]) => [k, type === "bool" ? el.checked : type === "channels" ? [...el.selectedOptions].map((o) => o.value) : el.value]));
  return { form, read };
}

export function settingsPage(c) {
  return wrap(c, "⚙️ Server Settings", "These are the same settings as /setup in Discord — changing them here changes the bot.", async () => {
    const { settings } = await g("/settings");
    const keys = ["admin_role", "franchise_owner_role", "general_manager_role", "head_coach_role", "assistant_coach_role", "log_channel", "announce_channel", "gametime_channel",
      "matchmaking_channel", "contract_channel", "awards_channel", "league_log_channel", "suggestion_channel", "memberstats_channel", "activity_enabled", "activity_channels", "timezone"];
    const { form, read } = settingsForm(keys, settings);
    const save = h("button", { class: "btn primary", disabled: !can("admin") || null, onclick: async (e) => {
      e.target.disabled = true;
      try { await g("/settings", { method: "PUT", body: read() }); toast("Settings saved"); } catch (err) { toast(err.errors ? Object.entries(err.errors).map(([k, v]) => `${LABELS[k] || k}: ${v}`).join(" • ") : err.message, "err"); }
      e.target.disabled = !can("admin");
    } }, "Save settings");
    return h("div", { class: "card" }, form, h("div", { class: "row end" }, can("admin") ? null : h("span", { class: "sub" }, "Only admins can change settings."), save));
  });
}

export function verificationPage(c) {
  return wrap(c, "✅ Verification", "Members press a button to receive a role. The role can't have moderator/admin permissions.", async () => {
    const { settings } = await g("/settings");
    const { form, read } = settingsForm(["verify_role", "verify_channel"], settings);
    return h("div", { class: "card" }, form, h("div", { class: "row end" },
      h("button", { class: "btn", disabled: !can("admin") || null, onclick: async () => { try { await g("/settings", { method: "PUT", body: read() }); toast("Saved"); } catch (e) { toast(Object.values(e.errors || {})[0] || e.message, "err"); } } }, "Save"),
      h("button", { class: "btn primary", disabled: !can("manager") || null, onclick: async () => { try { const r = await g("/verification/publish", { method: "POST" }); toast(r.notice || "Verification message posted", r.queued ? "warn" : ""); } catch (e) { toast(e.message, "err"); } } }, "Post verification message")));
  });
}

// ───────── team records ─────────
export function recordsPage(c) {
  return wrap(c, "🧮 Team Records", "Every change is logged in the team's history and posted to your Log channel.", async () => {
    const { items } = await g("/r/teams?per_page=100");
    if (!items.length) return h("div", { class: "card empty" }, "No teams yet — create one on the Teams page.");
    const grid = h("div", { class: "grid" });
    const draw = (t) => {
      const card = h("div", { class: "card" });
      const act = async (body, label) => {
        try { const r = await g(`/teams/${t.team_id}/record`, { method: "POST", body }); toast(`${t.name}: ${label}`); const fresh = draw(r.team); card.replaceWith(fresh); } catch (e) { toast(e.message, "err"); }
      };
      const btn = (label, onclick, cls = "") => h("button", { class: `btn small ${cls}`, disabled: !can("staff") || null, onclick }, label);
      card.append(h("h2", {}, t.name), h("div", { class: "sub" }, `${t.competition || "General"}${t.division ? " · " + t.division : ""}`),
        h("div", { class: "stat" }, h("b", {}, `${t.wins}-${t.losses}-${t.ties}`), h("span", {}, `Win % ${t.win_pct || ""}`)),
        h("div", { class: "row" }, btn("+ Win", () => act({ action: "win" }, "+1 win")), btn("+ Loss", () => act({ action: "loss" }, "+1 loss")), btn("+ Tie", () => act({ action: "tie" }, "+1 tie"))),
        h("div", { class: "row" },
          btn("Set…", () => {
            const mk = (k) => h("input", { type: "number", min: 0, max: 9999, value: t[k] });
            const w = mk("wins"), l = mk("losses"), ti = mk("ties");
            modal(`Set ${t.name}'s record`, h("div", {}, h("label", {}, "Wins"), w, h("label", {}, "Losses"), l, h("label", {}, "Ties"), ti), [
              { label: "Cancel", onClick: (x) => x() }, { label: "Save", cls: "primary", onClick: (x) => { x(); act({ action: "set", wins: +w.value, losses: +l.value, ties: +ti.value }, "record set"); } }]);
          }),
          btn("Reset", async () => { if (await confirmDialog("Reset record", `Reset ${t.name} to 0-0-0? History is kept.`, { confirmLabel: "Reset" })) act({ action: "reset" }, "reset"); }, "danger"),
          h("button", { class: "btn small", onclick: async () => {
            try {
              const { items: hist } = await g(`/teams/${t.team_id}/history`);
              modal(`${t.name} — history`, hist.length ? h("div", {}, hist.map((x) => h("div", { class: "row" }, h("b", {}, x.action), h("span", {}, x.after ? `${x.after.wins}-${x.after.losses}-${x.after.ties}` : ""), h("span", { class: "spacer" }), h("span", { class: "sub" }, fmtDate(x.timestamp))))) : h("p", { class: "sub" }, "No history yet."), [{ label: "Close", onClick: (x) => x() }]);
            } catch (e) { toast(e.message, "err"); }
          } }, "History")));
      return card;
    };
    items.forEach((t) => grid.append(draw(t)));
    return grid;
  });
}

// ───────── leaderboard ─────────
export function leaderboardPage(c) {
  const sort = h("select", {}, opt("wins", "Wins"), opt("percentage", "Winning %"), opt("losses", "Fewest losses"));
  const comp = h("select", {}, opt("", "All competitions"), state.gmeta.competitions.map((x) => opt(x, x)));
  const div = h("select", {}, opt("", "All divisions"), [...new Set(state.gmeta.divisions.map((d) => d.name))].map((x) => opt(x, x)));
  const out = h("div", {});
  const load = async () => {
    clear(out).append(spinner());
    try {
      const { items } = await g(`/leaderboard?${new URLSearchParams({ sort: sort.value, ...(comp.value ? { competition: comp.value } : {}), ...(div.value ? { division: div.value } : {}) })}`);
      clear(out).append(items.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["#", "Team", "Competition", "W", "L", "T", "Win %"].map((x) => h("th", {}, x)))),
        h("tbody", {}, items.map((t) => h("tr", {}, h("td", {}, t.rank <= 3 ? ["🥇", "🥈", "🥉"][t.rank - 1] : t.rank),
          h("td", {}, h("div", { class: "row" }, t.logo ? h("img", { class: "avatar", src: t.logo, alt: "", referrerpolicy: "no-referrer" }) : null, t.name)),
          h("td", {}, `${t.competition || ""}${t.division ? " · " + t.division : ""}`), h("td", {}, t.wins), h("td", {}, t.losses), h("td", {}, t.ties), h("td", {}, t.pct)))))) : h("div", { class: "empty card" }, "No teams match."));
    } catch (e) { clear(out).append(errorBox(e)); }
  };
  [sort, comp, div].forEach((s) => s.addEventListener("change", load));
  clear(c).append(h("h1", {}, "🥇 Leaderboard"), h("p", { class: "sub" }, "Live standings. Post a self-updating one in Discord with /leaderboard post."), h("div", { class: "row card" }, sort, comp, div), out);
  return load();
}

// ───────── poll archive / activity / member stats ─────────
export function pollArchivePage(c) {
  return wrap(c, "🗄️ Poll Archive", "Closed polls and their final results.", async () => {
    const { items } = await g("/poll-archive");
    if (!items.length) return h("div", { class: "card empty" }, "No closed polls yet.");
    return h("div", {}, items.map((p) => {
      const total = p.results.reduce((a, r) => a + r.votes, 0) || 1;
      return h("div", { class: "card" }, h("h2", {}, `#${p.poll_id} ${p.question}`), h("div", { class: "sub" }, `${p.voters} voter(s) · closed ${fmtDate(p.closed_at)}${p.anonymous ? " · anonymous" : ""}`),
        p.results.map((r) => h("div", {}, h("div", { class: "row" }, h("span", {}, r.option), h("span", { class: "spacer" }), h("b", {}, `${r.votes} (${Math.round((r.votes / total) * 100)}%)`)), meter(r.votes / total))));
    }));
  });
}

export function activityPage(c) {
  const period = h("select", {}, opt("week", "Last 7 days"), opt("day", "Today"), opt("all", "All time"));
  const out = h("div", {});
  const load = async () => {
    clear(out).append(spinner());
    try {
      const d = await g(`/activity?period=${period.value}`);
      clear(out).append(
        d.enabled ? null : h("div", { class: "card" }, h("b", {}, "Activity tracking is off."), h("p", { class: "sub" }, "Turn on “Track message activity” in Settings to start counting (bots are never counted).")),
        h("div", { class: "card" }, h("h2", {}, "Messages per day (14 days)"), d.daily.length ? barChart(d.daily.map((x) => ({ label: x.date, value: x.count }))) : h("p", { class: "sub" }, "No data yet.")),
        h("div", { class: "card" }, h("h2", {}, "Most active members"), d.top.length ? h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, d.top.map((r, i) => h("tr", {}, h("td", {}, i + 1), h("td", {}, r.name), h("td", {}, r.count.toLocaleString() + " msgs")))))) : h("p", { class: "sub" }, "Nobody yet.")));
    } catch (e) { clear(out).append(errorBox(e)); }
  };
  period.addEventListener("change", load);
  clear(c).append(h("h1", {}, "💬 Member Activity"), h("p", { class: "sub" }, "Message counts per member (UTC days)."), h("div", { class: "row card" }, period), out);
  return load();
}

export function memberStatsPage(c) {
  return wrap(c, "📈 Member Statistics", "Daily snapshots of your server's size, plus joins and leaves.", async () => {
    const d = await g("/memberstats?days=30");
    const last = d.snapshots.at(-1), first = d.snapshots[0];
    const stat = (n, l) => h("div", { class: "stat" }, h("b", {}, n), h("span", {}, l));
    return h("div", {}, h("div", { class: "grid" }, stat(last ? last.total.toLocaleString() : "—", "Members now"), stat(`+${d.joined}`, "Joined (30d)"), stat(`-${d.left}`, "Left (30d)"),
      stat(first && last ? `${last.total - first.total >= 0 ? "+" : ""}${last.total - first.total}` : "—", "Net change (30d)")),
      h("div", { class: "card" }, h("h2", {}, "Total members"), d.snapshots.length ? barChart(d.snapshots.map((s) => ({ label: s.date, value: s.total })), (p) => `${p.label}: ${p.value}`) : h("p", { class: "sub" }, "Snapshots are taken hourly — check back soon.")),
      h("div", { class: "card" }, h("h2", {}, "Joins per day"), d.daily.length ? barChart(d.daily.map((s) => ({ label: s.date, value: s.joined })), (p) => `${p.label}: +${p.value}`) : h("p", { class: "sub" }, "No joins recorded yet.")));
  });
}

// ───────── moderation ─────────
export function moderationPage(c) {
  clear(c).append(h("h1", {}, "🛡️ Moderation"), h("p", { class: "sub" }, "Lock channels (the original @everyone permission is restored exactly on unlock) and review the log."));
  const ch = h("select", {}, state.gmeta.channels.filter((x) => x.type === "text").map((x) => opt(x.id, `#${x.name}`)));
  const reason = h("input", { placeholder: "Reason (optional)", maxLength: 200 });
  const locks = h("div", {});
  const act = async (locked) => { try { const r = await g(`/channels/${ch.value}/lock`, { method: "POST", body: { locked, reason: reason.value } }); toast(r.notice || (locked ? "Channel locked" : "Channel unlocked"), r.queued ? "warn" : ""); loadLocks(); } catch (e) { toast(e.message, "err"); } };
  const loadLocks = async () => {
    try { const { items } = await g("/locks"); clear(locks).append(items.length ? h("div", {}, items.map((l) => h("div", { class: "row" }, h("b", {}, nameOfChannel(l.channel_id)), h("span", { class: "sub" }, `locked ${fmtDate(l.locked_at)} — ${l.reason || ""}`)))) : h("p", { class: "sub" }, "No channels are locked by TeamCore.")); } catch (e) { clear(locks).append(errorBox(e)); }
  };
  const logs = h("div", {});
  c.append(h("div", { class: "card" }, h("h2", {}, "Channel lock"), h("div", { class: "row" }, ch, reason), h("div", { class: "row" },
    h("button", { class: "btn danger", disabled: !can("manager") || null, onclick: () => act(true) }, "🔒 Lock"), h("button", { class: "btn", disabled: !can("manager") || null, onclick: () => act(false) }, "🔓 Unlock")),
    h("h2", {}, "Currently locked"), locks), h("h2", {}, "Moderation log"), logs);
  loadLocks();
  return renderResource(logs, "moderation_logs", { embedded: true });
}

// ───────── backups ─────────
export function backupsPage(c) {
  return wrap(c, "💾 Backups", "Saves roles, channels, permission overwrites and basic settings. It can't save messages, members, emojis, webhooks or bans.", async () => {
    const root = h("div", {});
    const backupTable = (items) => h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Name", "Contents", "Created", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, items.map((b) => h("tr", {},
        h("td", {}, b.name),
        h("td", {}, `${b.stats.roles} roles · ${b.stats.categories} categories · ${b.stats.channels} channels`),
        h("td", {}, fmtDate(b.created_at)),
        h("td", {}, h("div", { class: "row end" },
          h("button", { class: "btn small", onclick: () => restore(b) }, "Restore"),
          h("button", { class: "btn small danger", onclick: () => removeBackup(b) }, "Delete"))))))));
    const removeBackup = async (b) => {
      if (!(await confirmDialog("Delete backup", `Delete “${b.name}”?`, { confirmLabel: "Delete" }))) return;
      try { await g(`/backups/${encodeURIComponent(b.name)}`, { method: "DELETE" }); } catch (e) { toast(e.message, "err"); }
      draw();
    };
    const draw = async () => {
      const { items } = await g("/backups");
      clear(root).append(
        h("div", { class: "card row" }, h("button", { class: "btn primary", disabled: !can("admin") || null, onclick: () => {
          const name = h("input", { placeholder: "Backup name", maxLength: 50 });
          modal("Create backup", h("div", {}, h("label", {}, "Name"), name), [{ label: "Cancel", onClick: (x) => x() }, { label: "Create", cls: "primary", onClick: async (x) => {
            x(); toast("Creating backup…");
            try { const r = await g("/backups", { method: "POST", body: { name: name.value } }); toast(r.notice || "Backup saved", r.queued ? "warn" : ""); draw(); } catch (e) { toast(e.errors?.name || e.message, "err"); }
          } }]);
        } }, "＋ New backup")),
        items.length ? backupTable(items) : h("div", { class: "card empty" }, "No backups yet."));
    };
    const restore = (b) => {
      const mode = h("select", {}, opt("missing", "Add missing only (safe — nothing is deleted)"), state.gmeta && h("option", { value: "full" }, "Full restore (DELETES every channel and role first)"));
      const confirmBox = h("input", { placeholder: "Type RESTORE to confirm a full restore" });
      modal(`Restore “${b.name}”`, h("div", {}, h("label", {}, "Mode"), mode, h("label", {}, "Confirmation (full restore only — server owner only)"), confirmBox,
        h("p", { class: "help" }, "Members will not get their roles back, and messages in deleted channels are lost for good.")), [
        { label: "Cancel", onClick: (x) => x() },
        { label: "Restore", cls: "danger", onClick: async (x) => { x(); toast("Restoring… this can take a few minutes", "warn");
          try { const r = await g(`/backups/${encodeURIComponent(b.name)}/restore`, { method: "POST", body: { mode: mode.value, confirm: confirmBox.value } }); const s = r.result || {}; toast(r.notice || `Done: ${s.roles_created ?? 0} roles, ${s.channels_created ?? 0} channels created`, r.queued ? "warn" : ""); }
          catch (e) { toast(e.errors?.confirm || e.message, "err"); } } }]);
    };
    await draw();
    return root;
  });
}

// ───────── data export ─────────
export function exportPage(c) {
  return wrap(c, "📦 Data Export", "Download your server's TeamCore data. Secrets (webhook URLs) are never included.", async () => {
    const { collections } = await g("/export/options");
    const boxes = collections.map((n) => ({ n, el: h("input", { type: "checkbox", checked: true }) }));
    const fmt = h("select", {}, opt("json", "JSON (one file)"), opt("csv", "CSV (zip, one file per collection)"));
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
    } }, "⬇ Download");
    return h("div", { class: "card" }, h("div", { class: "grid" }, boxes.map((b) => h("label", { class: "row" }, b.el, b.n))), h("div", { class: "row" }, fmt, go));
  });
}

// ───────── preferences (themes + personal timezone) ─────────
export function preferencesPage(c) {
  const accents = ["#FF7A1A", "#FF4D4D", "#F5B942", "#22C55E", "#3B82F6", "#A855F7", "#EC4899"];
  const color = h("input", { type: "color", value: state.prefs.accent });
  const tz = h("input", { list: "dl_tz2", value: state.prefs.timezone });
  const save = async (patch) => {
    try { state.prefs = await api("/api/me/preferences", { method: "PUT", body: patch }); setAccent(state.prefs.accent); toast("Preferences saved"); } catch (e) { toast(e.errors ? Object.values(e.errors)[0] : e.message, "err"); }
  };
  clear(c).append(h("h1", {}, "🎨 Preferences"), h("p", { class: "sub" }, "Personal to you — they don't change the server."),
    h("div", { class: "card" }, h("h2", {}, "Accent theme"), h("div", { class: "row" }, accents.map((a) => { const b = h("button", { class: "btn", title: a, onclick: () => { color.value = a; save({ accent: a }); } }, "  "); b.style.background = a; return b; }), color,
      h("button", { class: "btn primary", onclick: () => save({ accent: color.value }) }, "Apply")),
      h("h2", {}, "My timezone"), h("p", { class: "help" }, "All dates in the dashboard are shown in this timezone."),
      h("div", { class: "row" }, tz, h("button", { class: "btn primary", onclick: () => save({ timezone: tz.value }) }, "Save")),
      h("datalist", { id: "dl_tz2" }, (state.gmeta?.timezones || []).map((z) => h("option", { value: z })))));
}
