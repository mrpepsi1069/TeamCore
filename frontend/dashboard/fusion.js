// Football Fusion game stats: the backend parses and stores the report; this renders it.
import { h, modal } from "./ui.js";

const statLine = (stats) => Object.entries(stats || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ") || "—";

export function showFusion(ff) {
  if (!ff) return;
  const score = ff.score || {};
  const players = ff.players || [];
  const linked = ff.linked ?? players.filter((p) => p.user_id).length;
  const total = ff.total ?? players.length;
  const summary = h("div", { class: "grid" }, [
    ["Game", ff.game || "—"],
    ["Score", `${score.home || "?"} ${score.home_score ?? "?"} – ${score.away_score ?? "?"} ${score.away || "?"}`],
    ["Reporter", ff.reporter ? `${ff.reporter.name}${ff.reporter.display ? ` (${ff.reporter.display})` : ""}` : "—"],
    ["Linked players", `${linked}/${total}`],
    ["Identifier", ff.identifier || "—"],
  ].map(([label, value]) => h("div", { class: "stat" }, h("b", {}, value), h("span", {}, label))));

  const table = h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Player"), h("th", {}, "Positions & stats"))),
    h("tbody", {}, players.map((p) => h("tr", {},
      h("td", {}, h("b", {}, p.name), h("div", { class: "sub" }, p.user_id ? "Linked" : "Not linked")),
      h("td", {}, Object.entries(p.positions || {}).map(([pos, stats]) =>
        h("div", {}, h("b", {}, pos), " ", statLine(stats)))))))));

  modal(`${ff.game || "Football Fusion"} — game stats`, h("div", {}, summary, table),
    [{ label: "Close", onClick: (c) => c() }], { wide: true });
}
