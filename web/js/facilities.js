// 設備(D-383):オフの契約の段階で、練習設備・トレーナーと医療・育成施設の段階(1〜5)を選ぶ。維持費の分だけ年俸に使える額が減る

import { state } from "./core.js";
import { query } from "./backend.js";
import { el } from "./parts.js";
import { runProc } from "./procedure.js";

export async function facilitiesBox(token) {
  const f = await query("facilities_view", {});
  if (token !== state.token || !f.mine) return null;
  const b = f.mine.budget;
  const rows = f.kinds.map((k) => el("div", { className: "row facility-row" },
    el("label", {}, `${k.label} `, el("select", { id: `facility-${k.kind}`, "aria-label": k.label, disabled: state.running || !f.editable, onchange: (e) => runProc("facility_set", { kind: k.kind, level: Number(e.target.value) }) },
      ...k.options.map((o) => el("option", { value: String(o.level), selected: o.level === f.mine.levels[k.kind] }, `段階 ${o.level}(${o.upkeep_text}・${o.effect})`))))));
  const others = el("details", { id: "facility-teams" }, el("summary", {}, "全球団の設備"),
    el("ul", { className: "offer-history" }, ...f.teams.map((t) => el("li", { className: t.is_mine ? "mine" : "" }, `${t.team_name}:${f.kinds.map((k) => `${k.label} ${t.levels[k.kind]}`).join("・")}(維持費 ${t.upkeep_text})`))));
  return el("details", { id: "facility-box", open: state.proc.facilityOpen || false, ontoggle: (e) => { state.proc.facilityOpen = e.target.open; } },
    el("summary", {}, `設備(維持費 ${b.upkeep_text}・年俸に使える額 ${b.cap_text})`),
    ...rows,
    el("p", { className: "muted small" }, f.editable ? "段階を変えると、年俸に使える額(資金 − 維持費)がすぐ変わります。効きは次のオフの能力の更新から。" : f.reason),
    others);
}
