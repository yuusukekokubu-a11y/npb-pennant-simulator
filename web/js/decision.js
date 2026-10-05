// 判断の画面の共通の部品(FA・ドラフト・市場。①b。D-296〜D-299):表の受け取り・絞り込みの 1 行・表(名前と並べ替えた列を固定)・
// 自球団の状況のパネル・提示のパネル。説明ブロックは置かない(列の説明は見出しの title に入る。D-270)

import { $, state } from "./core.js";
import { answer, query } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, link, playerLink, sortToggle, table } from "./parts.js";
import { salaryInput } from "./contract.js";

// 段階ごとの表の状態(絞り込み・種類・並び順・表示する選手・シーズン)。手続きの画面を行き来しても保つ
function decisionState(stage) {
  const ps = state.proc;
  if (!ps.decision) ps.decision = {};
  if (!ps.decision[stage]) ps.decision[stage] = { group: "all", kind: "war", show: "open", season: "current", sortBy: { key: null, order: null }, shown: null };
  return ps.decision[stage];
}

// 表を計算本体から受け取る。答え合わせモードがオンなら、真の総合も受け取る
export async function fetchDecision(stage, token) {
  const ds = decisionState(stage);
  const data = await query("decision_table", { stage, group: ds.group, kind: ds.kind, sort: ds.sortBy.key, order: ds.sortBy.key ? ds.sortBy.order : null, season: ds.season, show: ds.show });
  if (token !== state.token) return null;
  ds.shown = { key: data.sort.key, order: data.order };
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("procedure_answers", {});
    if (token !== state.token) return null;
    if (state.answerLevel === 0) truth = null;
  }
  return { data, truth };
}

// 絞り込みの 1 行(ポジション・種類・表示する選手・シーズン)
export function decisionFilters(stage, data) {
  const ds = decisionState(stage);
  const pick = (id, label, items, value, onPick) => el("select", { id, "aria-label": label, onchange: (e) => onPick(e.target.value) }, ...items.map((x) => el("option", { value: x.key, selected: x.key === value }, x.label)));
  const box = el("div", { className: "compact-filters", id: `${stage}-filters` },
    pick(`${stage}-group`, "ポジションの絞り込み", data.groups, ds.group, (k) => { ds.group = k; ds.sortBy = { key: null, order: null }; renderCurrent(); }));
  if (data.kinds.length) box.append(pick(`${stage}-kind`, "成績の種類", data.kinds, ds.kind, (k) => { ds.kind = k; renderCurrent(); }));
  box.append(pick(`${stage}-show`, "表示する選手", data.shows, ds.show, (k) => { ds.show = k; renderCurrent(); }));
  if (data.seasons.length > 1) box.append(pick(`${stage}-season`, "シーズンの選択", data.seasons.map((x) => ({ key: x.key, label: x.key === "career" ? "通算" : "今季" })), ds.season, (k) => { ds.season = k; renderCurrent(); }));
  return box;
}

// 表:名前と並べ替えた列を固定する(並べ替えた列は名前のすぐ右に動く)。見出しのタップで並べ替え、もう一度で逆順
export function decisionTable(stage, data, truth, { onName, detail, rowClass }) {
  const ds = decisionState(stage);
  const sortCol = data.columns.find((c) => c.key === data.sort.key) || data.sort;
  const columns = data.columns.filter((c) => c.key !== sortCol.key);
  if (truth) columns.push({ key: "truth", label: "真の総合", description: "答え合わせ:真の今の総合値" + (truth.level === 2 ? " / 潜在能力" : "") });
  const rows = data.rows.map((r) => {
    const t = truth ? truth.players[r.player_id] : null;
    const values = { ...r.values, truth: t ? `${t.overall}${t.potential ? ` / ${t.potential}` : ""}` : "" };
    if (r.thin && values.pos) values.pos = el("span", { className: "thin" }, values.pos);
    if (r.in_slots && values.depth) values.depth = el("span", { className: "depth-in" }, values.depth);
    return { ...r, values };
  });
  const first = (p) => [onName ? link(p.name, () => onName(p)) : playerLink(p.name, p.player_id), el("span", { className: "sub" }, p.hand || "")];
  const onSort = (key) => { sortToggle(ds.sortBy, ds.shown, key); renderCurrent(); };
  if (!rows.length) return el("p", { className: "muted", id: `${stage}-empty` }, "この絞り込みに合う選手はいません。");
  return table({ firstLabel: "選手", columns, rows, first, sort: sortCol.key, order: data.order, onSort, rowClass, extra: sortCol, fluid: true, detail });
}

// 自球団の状況のパネル(状態バーを押すと開く。D-298)
export async function outlookPanel(token) {
  const o = await query("team_outlook", {});
  if (token !== state.token) return null;
  const b = o.budget;
  const head = el("p", { className: "small" }, `${o.team_name}:${o.players}/${o.max} 人・空き枠 ${o.space}${o.pending_offers ? `(提示中 ${o.pending_offers} 人を除く)` : ""}・総年俸 ${b.total_text}${b.cap ? `(${b.hard ? "上限" : "目安"} ${b.cap_text}・${b.usage}%)` : ""}`);
  const rows = o.positions.map((r) => ({
    ...r,
    values: {
      count: el("span", { className: r.thin ? "thin" : "", title: r.thin_reasons.join("・") }, `${r.count}/${r.target}${r.thin ? " ◆" : ""}`),
      top: r.top.map((x) => `${x.name}(${x.war_text}・${x.age})`).join("、") || "—",
      age: r.age_mean === null ? "—" : `${r.age_mean}`,
    },
  }));
  const columns = [
    { key: "count", label: "人数/目安", description: "今の人数と人数の目安。◆は手薄(目安より 2 人以上少ない、または一軍相当の見込みの WAR が下位 4 球団)" },
    { key: "top", label: `主な選手(${o.season_label}の WAR・年齢)` },
    { key: "age", label: "平均年齢" },
  ];
  return el("div", { className: "outlook-panel", id: "outlook-panel" }, head,
    table({ firstLabel: "ポジション", columns, rows, first: (r) => [el("span", {}, r.label)], fluid: true }));
}

// 提示のパネル(FA と市場で共通。年数・年俸と ±% のボタン・提示する・取り消す。D-257、D-300)
export function dealPanel(p, info, prefs, { prefix, onSend, onCancel }) {
  const d = p.deal;
  const box = el("div", { className: "offer-panel", id: `${prefix}-panel`, dataset: { playerId: p.player_id } });
  box.append(el("p", {}, `${d.former_team_name ? `前の所属 ${d.former_team_name}。` : ""}算定年俸 ${d.calc_text}。見込みの WAR ${Number(d.expected).toFixed(2)}。`));
  if (p.offer) box.append(el("p", { className: "small" }, `提示中:${p.offer.years} 年・${Number(p.offer.salary).toLocaleString()} 万円(出し直すと上書き)`));
  const years = el("select", { id: `${prefix}-years`, "aria-label": "契約年数" }, ...Array.from({ length: info.max_years - info.min_years + 1 }, (_, i) => info.min_years + i).map((y) => el("option", { value: String(y), selected: p.offer ? p.offer.years === y : y === 1 }, `${y} 年`)));
  const si = salaryInput(p.offer ? p.offer.salary : d.calc_salary, info.none_max_ratio ? d.calc_salary : info.minimum_salary, info.rounding, info.none_max_ratio ? Math.floor(d.calc_salary * info.none_max_ratio) : null);
  box.append(el("div", { className: "offer-fields" }, el("label", {}, "年数 ", years), si.wrap));
  if (prefs && prefs.players && prefs.players[p.player_id]) {
    const w = prefs.players[p.player_id];
    box.append(el("p", { className: "small answer" }, `志望(答え合わせ):${w.map((x) => `${x.label} ${x.text}${x.active ? "" : "(効かない)"}`).join("・")}`));
  }
  const send = el("button", { id: `${prefix}-send`, type: "button", disabled: state.running, onclick: () => {
    const val = si.input.value.trim();
    if (!/^\d+$/.test(val)) {
      $("proc-message").className = "message ng";
      $("proc-message").textContent = "年俸は整数(万円)で入れてください。";
      return;
    }
    onSend({ player_id: p.player_id, years: Number(years.value), salary: Number(val) });
  } }, p.offer ? "提示を出し直す" : "提示する");
  const row = el("div", { className: "row" }, send);
  if (p.offer) row.append(el("button", { id: `${prefix}-cancel`, type: "button", className: "secondary", disabled: state.running, onclick: () => onCancel(p.player_id) }, "提示を取り消す"));
  box.append(row);
  return box;
}

// 結果の一覧(FA・市場)。閉じた形で、表の下に置く
export function resultsTable(id, title, results, withRound) {
  const columns = [...(withRound ? [{ key: "round", label: "ラウンド" }] : []), { key: "team", label: "契約した球団" }, { key: "years", label: "年数" }, { key: "salary", label: "年俸" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "former", label: "前の所属" }, { key: "offers", label: "提示の数" }];
  const rows = results.map((x) => ({ ...x, values: { round: String(x.round ?? ""), team: x.stayed ? `${x.team_name}(残留)` : x.team_name, years: `${x.years} 年`, salary: x.salary_text, position: x.position_label, age: `${x.age}歳`, former: x.former_team_name, offers: `${x.offers} 球団` } }));
  return el("details", { id }, el("summary", {}, title), table({ firstLabel: "選手", columns, rows, first: (x) => [playerLink(x.name, x.player_id)], rowClass: (x) => (x.is_mine ? "mine" : ""), fluid: true }));
}
