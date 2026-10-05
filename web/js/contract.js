// 契約の画面(表・提示のパネル・自由契約・自動案)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { answer, query } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, link, sortToggle, table } from "./parts.js";
import { runProc } from "./procedure.js";

// ---- 契約更改(F3-2b。D-243〜D-252):自動案のまとめて提示、選手を押して出る提示のパネル、断られた理由 ----

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
  let prefs = null;
  if (state.answerLevel > 0) {
    prefs = await answer("negotiation_answers", {});
    if (token !== state.token) return null;
  }
  const pick = (id, label, items, value, onPick) => el("select", { id, "aria-label": label, onchange: (e) => onPick(e.target.value) }, ...items.map((x) => el("option", { value: x.key, selected: x.key === value }, x.label)));
  const filters = el("div", { className: "compact-filters" },
    pick("contract-group", "ポジションの絞り込み", data.groups, cs.group, (k) => { cs.group = k; cs.sortBy = { key: null, order: null }; renderCurrent(); }),
    pick("contract-status", "状態の絞り込み", data.statuses, cs.status, (k) => { cs.status = k; renderCurrent(); }));
  if (data.kinds.length) filters.append(pick("contract-kind", "成績の種類", data.kinds, cs.kind, (k) => { cs.kind = k; renderCurrent(); }));
  if (data.seasons.length > 1) filters.append(pick("contract-season", "シーズンの選択", data.seasons.map((x) => ({ key: x.key, label: x.key === "career" ? "通算" : "今季" })), cs.season, (k) => { cs.season = k; renderCurrent(); }));
  const first = (p) => [link(p.name, () => { ps.renewalOpen = ps.renewalOpen === p.player_id ? null : p.player_id; renderCurrent(); }), el("span", { className: "sub" }, p.hand || "")];
  const detail = (p) => (ps.renewalOpen === p.player_id ? contractPanel(p, rn, prefs) : null);
  const onSort = (key) => { sortToggle(cs.sortBy, cs.shown, key); renderCurrent(); };
  const rowClass = (p) => (p.status === "refused" ? "refused" : p.status === "released" || p.status === "declared" ? "muted" : "");
  return [
    filters,
    data.rows.length
      ? table({ firstLabel: "選手", columns: data.columns, rows: data.rows, first, sort: data.sort.key, order: data.order, onSort, rowClass, fluid: true, detail })
      : el("p", { className: "muted", id: "contract-empty" }, "この絞り込みに合う選手はいません。"),
  ];
}

// 年俸の入力欄と「+5%」「+10%」「−5%」のボタン(更改と FA の提示のパネルで共通。D-257)。基準は今の入力値、丸めは step 万円
export function salaryInput(value, min, step, max = null) {
  const input = el("input", { id: "offer-salary", type: "number", inputMode: "numeric", min: String(min), step: String(step), value: String(value), "aria-label": "年俸(万円)" });
  if (max) input.max = String(max); // 「なし」は算定の 1.0〜1.3 倍(D-273)
  const bump = (rate) => {
    const now = Number(input.value) || value;
    const next = Math.max(min, Math.round((now * (1 + rate)) / step) * step);
    input.value = String(max ? Math.min(max, next) : next);
  };
  const btn = (label, rate, id) => el("button", { type: "button", className: "secondary pct", id, onclick: () => bump(rate) }, label);
  const wrap = el("span", { className: "salary-input" }, el("label", {}, "年俸 ", input, " 万円"), btn("+5%", 0.05, "offer-plus5"), btn("+10%", 0.1, "offer-plus10"), btn("−5%", -0.05, "offer-minus5"));
  return { wrap, input };
}

// 選手をタップして出るパネル(D-272):交渉中なら 年数・年俸(±%)・提示する・自由契約にする・残りの回数・断られた理由。
// 契約が残る選手(複数年の途中・更改済)は「自由契約にする」だけ(残りの契約は消える)
function contractPanel(p, rn, prefs) {
  const r = p.renewal;
  const box = el("div", { className: "offer-panel", id: "offer-panel", dataset: { playerId: p.player_id } });
  if (r && r.offers.length) {
    box.append(el("ul", { className: "offer-history" }, ...r.offers.map((o, i) => el("li", {}, `${i + 1} 回目:${o.years} 年・${o.salary_text} → ${o.accepted ? "受けた" : `断られた(${o.reason})`}`))));
  }
  const releaseBtn = el("button", { id: "offer-release", type: "button", className: "danger", disabled: state.running || !p.can_release, onclick: () => contractRelease(p) }, "自由契約にする");
  if (r && r.status !== "accepted" && p.status !== "released" && p.status !== "declared" && p.can_offer) {
    const years = el("select", { id: "offer-years", "aria-label": "契約年数" }, ...Array.from({ length: rn.max_years - rn.min_years + 1 }, (_, i) => rn.min_years + i).map((y) => el("option", { value: String(y) }, `${y} 年`)));
    const min = rn.none_max_ratio ? r.auto_salary : rn.minimum_salary;
    const si = salaryInput(r.auto_salary, min, rn.rounding, rn.none_max_ratio ? Math.floor(r.auto_salary * rn.none_max_ratio) : null);
    box.append(el("div", { className: "offer-fields" }, el("label", {}, "年数 ", years), si.wrap));
    if (rn.hard && rn.cap) box.append(el("p", { className: "muted small" }, `見込みの総年俸 ${rn.projected_text} / 上限 ${rn.cap_text}`));
    if (prefs && prefs.players && prefs.players[p.player_id]) {
      const w = prefs.players[p.player_id];
      box.append(el("p", { className: "small answer" }, `志望(答え合わせ):${w.map((x) => `${x.label} ${x.text}${x.active ? "" : "(効かない)"}`).join("・")}`));
    }
    const offerBtn = el("button", { id: "offer-send", type: "button", disabled: state.running, onclick: () => sendOffer(p, years, si.input) }, `提示する(残り ${r.offers_left} 回)`);
    box.append(el("div", { className: "row" }, offerBtn, releaseBtn));
  } else if (p.status === "released" || p.status === "declared") {
    box.append(el("p", { className: "small" }, p.status === "released" ? "自由契約にしました(市場へ)。" : "FA を宣言しました(FA の段階で提示できます)。"));
  } else {
    const c = p.contract;
    if (c) box.append(el("p", { className: "small" }, `契約:${c.salary_text}・残り ${c.remaining} 年`));
    box.append(el("div", { className: "row" }, releaseBtn));
  }
  return box;
}

async function contractRelease(p) {
  if (!confirm(`${p.name} を自由契約にします(残りの契約は消えます。戻せません)。よろしいですか?`)) return;
  state.proc.lastOffer = null;
  state.proc.renewalOpen = null;
  await runProc("contract_release", { player_id: p.player_id });
}

export async function renewAuto() {
  state.proc.lastOffer = null;
  await runProc("renew_auto");
}

async function sendOffer(p, yearsEl, salaryEl) {
  const args = { player_id: p.player_id, years: Number(yearsEl.value) };
  if (salaryEl) {
    const v = salaryEl.value.trim();
    if (!/^\d+$/.test(v)) {
      $("proc-message").className = "message ng";
      $("proc-message").textContent = "年俸は整数(万円)で入れてください。";
      return;
    }
    args.salary = Number(v);
  }
  state.proc.lastOffer = null;
  await runProc("offer", args, (view) => { state.proc.lastOffer = view.last_offer || null; });
}
