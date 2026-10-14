// 契約の画面(表・選手のパネル・自由契約)(web/app.js から分けた。保守②。D-291。作り直し②で更改は査定の自動に。D-358)

import { state } from "./core.js";
import { query } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, link, sortToggle, table } from "./parts.js";
import { runProc } from "./procedure.js";

// ---- 契約更改の結果(作り直し②。D-358):更改は段階の始めに査定で済んでいる。選手を押すと結果のパネル(自由契約にできる) ----

// 契約の画面(契約更改と自由契約をまとめたもの。D-272):自球団の全選手を 1 つの表に。絞り込みはポジションと状態の 2 つ
export async function contractSection(v, token) {
  const ps = state.proc;
  const cs = ps.contract;
  if (!v.my_team || !v.renewal) return [el("p", { className: "muted" }, "操作する球団がありません。")];
  const rn = v.renewal;
  const usable = cs.sortBy.key;
  const data = await query("contract_table", { group: cs.group, kind: cs.kind, sort: usable, order: usable ? cs.sortBy.order : null, season: cs.season, status: cs.status });
  if (token !== state.token) return null;
  cs.shown = { key: data.sort.key, order: data.order };
  const pick = (id, label, items, value, onPick) => el("select", { id, "aria-label": label, onchange: (e) => onPick(e.target.value) }, ...items.map((x) => el("option", { value: x.key, selected: x.key === value }, x.label)));
  const filters = el("div", { className: "compact-filters" },
    pick("contract-group", "ポジションの絞り込み", data.groups, cs.group, (k) => { cs.group = k; cs.sortBy = { key: null, order: null }; renderCurrent(); }),
    pick("contract-status", "状態の絞り込み", data.statuses, cs.status, (k) => { cs.status = k; renderCurrent(); }));
  if (data.kinds.length) filters.append(pick("contract-kind", "成績の種類", data.kinds, cs.kind, (k) => { cs.kind = k; renderCurrent(); }));
  if (data.seasons.length > 1) filters.append(pick("contract-season", "シーズンの選択", data.seasons.map((x) => ({ key: x.key, label: x.key === "career" ? "通算" : "今季" })), cs.season, (k) => { cs.season = k; renderCurrent(); }));
  const first = (p) => [link(p.name, () => { ps.renewalOpen = ps.renewalOpen === p.player_id ? null : p.player_id; renderCurrent(); }), el("span", { className: "sub" }, p.hand || "")];
  const detail = (p) => (ps.renewalOpen === p.player_id ? contractPanel(p, rn) : null);
  const onSort = (key) => { sortToggle(cs.sortBy, cs.shown, key); renderCurrent(); };
  const rowClass = (p) => (p.status === "released" || p.status === "declared" ? "muted" : "");
  return [
    filters,
    data.rows.length
      ? table({ firstLabel: "選手", columns: data.columns, rows: data.rows, first, sort: data.sort.key, order: data.order, onSort, rowClass, fluid: true, detail })
      : el("p", { className: "muted", id: "contract-empty" }, "この絞り込みに合う選手はいません。"),
  ];
}

// 年俸の入力欄と「+5%」「+10%」「−5%」のボタン(FA・市場の提示のパネル。D-257)。基準は今の入力値、丸めは step 万円
export function salaryInput(value, min, step) {
  const input = el("input", { id: "offer-salary", type: "number", inputMode: "numeric", min: String(min), step: String(step), value: String(value), "aria-label": "年俸(万円)" });
  const bump = (rate) => {
    const now = Number(input.value) || value;
    input.value = String(Math.max(min, Math.round((now * (1 + rate)) / step) * step));
  };
  const btn = (label, rate, id) => el("button", { type: "button", className: "secondary pct", id, onclick: () => bump(rate) }, label);
  const wrap = el("span", { className: "salary-input" }, el("label", {}, "年俸 ", input, " 万円"), btn("+5%", 0.05, "offer-plus5"), btn("+10%", 0.1, "offer-plus10"), btn("−5%", -0.05, "offer-minus5"));
  return { wrap, input };
}

// 選手をタップして出るパネル(D-272、D-358):更改の結果(前の年俸 → 新しい年俸・年数)か今の契約と、「自由契約にする」
// (残りの契約は消える。最低人数を割る選手は外せない。資金を超えている間は外せる)
function contractPanel(p, rn) {
  const r = p.renewal;
  const box = el("div", { className: "offer-panel", id: "offer-panel", dataset: { playerId: p.player_id } });
  const releaseBtn = el("button", { id: "offer-release", type: "button", className: "danger", disabled: state.running || !p.can_release, onclick: () => contractRelease(p) }, "自由契約にする");
  if (p.status === "released" || p.status === "declared") {
    box.append(el("p", { className: "small" }, p.status === "released" ? "自由契約にしました(市場へ)。" : "FA を宣言しました(FA の段階で提示できます)。"));
    return box;
  }
  if (r && r.status === "accepted") box.append(el("p", { className: "small", id: "renewal-result" }, `更改:${r.old_text} → ${r.new_text}(${r.change_text}・${r.years} 年)`));
  else if (p.contract) box.append(el("p", { className: "small" }, `契約:${p.contract.salary_text}・残り ${p.contract.remaining} 年`));
  if (rn.hard && rn.cap) box.append(el("p", { className: "muted small" }, `総年俸 ${rn.team_total_text} / 資金 ${rn.cap_text}`));
  box.append(el("div", { className: "row" }, releaseBtn));
  return box;
}

async function contractRelease(p) {
  if (!confirm(`${p.name} を自由契約にします(残りの契約は消えます。戻せません)。よろしいですか?`)) return;
  state.proc.renewalOpen = null;
  await runProc("contract_release", { player_id: p.player_id });
}
