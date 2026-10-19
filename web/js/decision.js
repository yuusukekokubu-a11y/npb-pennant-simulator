// 判断の画面の共通の部品(FA・ドラフト・市場。①b。D-296〜D-299):表の受け取り・操作の帯・表(名前と並べ替えた列を固定)・
// 自球団の状況のパネル・提示の操作。説明ブロックは置かない(列の説明は見出しの title に入る。D-270)。
// 行を押すと選手の札(能力が上、その画面の操作が下。D-484〜D-486)

import { $, state } from "./core.js";
import { query } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { band, bandSelect, el, nameCell, pinSorted, sortControls, sortLabel, sortToggle, table } from "./parts.js";
import { salaryInput } from "./contract.js";

export const DECISION_COL_W = "4.6rem"; // オフの手続きの表の列の幅(年俸・状態などが入る。D-483)

// 段階ごとの表の状態(絞り込み・種類・並び順・表示する選手・シーズン)。手続きの画面を行き来しても保つ
function decisionState(stage) {
  const ps = state.proc;
  if (!ps.decision) ps.decision = {};
  if (!ps.decision[stage]) ps.decision[stage] = { group: "all", kind: "war", show: "open", season: "current", sortBy: { key: null, order: null }, shown: null };
  return ps.decision[stage];
}

// 表を計算本体から受け取る(答え合わせモードはなくした。作り直し①)
export async function fetchDecision(stage, token) {
  const ds = decisionState(stage);
  const data = await query("decision_table", { stage, group: ds.group, kind: ds.kind, sort: ds.sortBy.key, order: ds.sortBy.key ? ds.sortBy.order : null, season: ds.season, show: ds.show });
  if (token !== state.token) return null;
  ds.shown = { key: data.sort.key, order: data.order };
  return { data, truth: null };
}

// 表の上の操作の帯(D-482):種類(基本・セイバー・WAR)→ 右寄せで「絞り込み」(ポジション・表示する選手・シーズン)→「並び」
export function decisionBand(stage, data, { filters = [], filterText = [] } = {}) {
  const ds = decisionState(stage);
  const seasons = data.seasons.map((x) => ({ key: x.key, label: x.key === "career" ? "通算" : "今季" }));
  const items = [
    bandSelect(`${stage}-group`, "ポジション", data.groups, ds.group, (k) => { ds.group = k; ds.sortBy = { key: null, order: null }; renderCurrent(); }),
    bandSelect(`${stage}-show`, "表示する選手", data.shows, ds.show, (k) => { ds.show = k; renderCurrent(); }),
  ];
  if (data.seasons.length > 1) items.push(bandSelect(`${stage}-season`, "シーズン", seasons, ds.season, (k) => { ds.season = k; renderCurrent(); }));
  const label = (list, key) => (list.find((x) => x.key === key) || {}).label || "";
  const text = [label(data.groups, ds.group), label(data.shows, ds.show), data.seasons.length > 1 ? label(seasons, ds.season) : "", ...filterText].filter(Boolean).join("・");
  return sortBand(stage, data, ds, {
    views: data.kinds, view: ds.kind, onView: (k) => { ds.kind = k; renderCurrent(); },
    filters: [...items, ...filters], filterText: text,
  });
}

// 帯の「並び」(表の列から選ぶ)を付けた帯。契約の画面でも使う
export function sortBand(id, data, ds, { views = [], view = null, onView = null, filters = [], filterText = "", note = "" }) {
  const options = data.columns.some((c) => c.key === data.sort.key) ? data.columns : [data.sort, ...data.columns];
  const onKey = (key) => { ds.sortBy = { key, order: null }; renderCurrent(); };
  const onOrder = (o) => { ds.sortBy = { key: data.sort.key, order: o }; renderCurrent(); };
  return band({
    id, views, viewsId: `${id}-kind`, view, onView, filters, filterText, note,
    sortText: sortLabel(data.sort.label, data.order),
    sortControls: sortControls(id, { options, key: data.sort.key, order: data.order, counts: data.sort.type === "count", onKey, onOrder }),
  });
}

// 表:名前と並べ替えた列を固定する(並べ替えた列は名前のすぐ右に動く)。見出しのタップで並べ替え、もう一度で逆順。
// 行を押すと選手の札(actions はその行の操作)。列の数と幅は種類を変えても同じ(D-483)
export function decisionTable(stage, data, truth, { actions, rowClass }) {
  const ds = decisionState(stage);
  const { pinned, rest } = pinSorted(data.columns, data.sort);
  const rows = data.rows.map((r) => {
    const values = { ...r.values };
    if (r.thin && values.pos) values.pos = el("span", { className: "thin" }, values.pos);
    if (r.in_slots && values.depth) values.depth = el("span", { className: "depth-in" }, values.depth);
    return { ...r, values };
  });
  const onSort = (key) => { sortToggle(ds.sortBy, ds.shown, key); renderCurrent(); };
  if (!rows.length) return el("p", { className: "muted", id: `${stage}-empty` }, "この絞り込みに合う選手はいません。");
  return table({ firstLabel: "選手", columns: rest, rows, first: (p) => nameCell(p.name, p.hand || ""), sort: pinned.key, order: data.order, onSort, rowClass, extra: pinned, pick: "proc", actions, slots: data.slots, colW: DECISION_COL_W, keep: `proc-${stage}` });
}

// 自球団の状況のパネル(状態バーを押すと開く。D-298)。見出しの解説は用語集から(D-311)
export async function outlookPanel(token) {
  const o = await query("team_outlook", {});
  if (token !== state.token) return null;
  const b = o.budget;
  const head = el("p", { className: "small" }, `${o.team_name}:${o.players}/${o.max} 人・空き枠 ${o.space}${o.pending_offers ? `(提示・希望中の ${o.pending_offers} 人を除く)` : ""}・総年俸 ${b.total_text}${b.cap ? `(${b.hard ? "上限" : "目安"} ${b.cap_text}・${b.usage}%)` : ""}`);
  const rows = o.positions.map((r) => ({
    ...r,
    values: {
      count: el("span", { className: r.thin ? "thin" : "", title: r.thin_reasons.join("・") }, `${r.count}/${r.target}${r.thin ? " ◆" : ""}`),
      top: r.top.map((x) => `${x.name}(${x.war_text}・${x.age})`).join("、") || "—",
      age: r.age_mean === null ? "—" : `${r.age_mean}`,
    },
  }));
  const columns = [
    { key: "count", label: "人数/目安" },
    { key: "top", label: `主な選手(${o.season_label}の WAR・年齢)` },
    { key: "age", label: "平均年齢" },
  ];
  return el("div", { className: "outlook-panel", id: "outlook-panel" }, head,
    table({ firstLabel: "ポジション", columns, rows, first: (r) => [el("span", {}, r.label)], fluid: true }));
}

// 提示の操作(FA。札の下。年数・年俸と ±% のボタン・提示する・取り消す。D-257、D-486、D-487)
export function dealPanel(p, info, { prefix, onSend, onCancel }) {
  const d = p.deal;
  const box = el("div", { className: "offer-panel", id: `${prefix}-panel`, dataset: { playerId: p.player_id } });
  box.append(el("p", {}, `${d.former_team_name ? `前の所属 ${d.former_team_name}。` : ""}算定年俸 ${d.calc_text}。見込みの WAR ${Number(d.expected).toFixed(2)}。`));
  if (p.offer) box.append(el("p", { className: "small" }, `提示中:${p.offer.years} 年・${Number(p.offer.salary).toLocaleString()} 万円(出し直すと上書き)`));
  const years = el("select", { id: `${prefix}-years`, "aria-label": "契約年数" }, ...Array.from({ length: info.max_years - info.min_years + 1 }, (_, i) => info.min_years + i).map((y) => el("option", { value: String(y), selected: p.offer ? p.offer.years === y : y === 1 }, `${y} 年`)));
  const si = salaryInput(p.offer ? p.offer.salary : d.calc_salary, info.minimum_salary, info.rounding);
  box.append(el("div", { className: "offer-fields" }, el("label", {}, "年数 ", years)), si.wrap);
  const send = el("button", { id: `${prefix}-send`, type: "button", disabled: state.running, onclick: () => {
    const val = si.input.value.trim();
    if (!/^\d+$/.test(val)) {
      $("proc-message").className = "message ng";
      $("proc-message").textContent = "年俸は整数(万円)で入れてください。";
      return;
    }
    onSend({ player_id: p.player_id, years: Number(years.value), salary: Number(val) });
  } }, p.offer ? "提示を出し直す" : "この年俸で提示する");
  const row = el("div", { className: "row" }, send);
  if (p.offer) row.append(el("button", { id: `${prefix}-cancel`, type: "button", className: "secondary", disabled: state.running, onclick: () => onCancel(p.player_id) }, "提示を取り消す"));
  box.append(row);
  return box;
}

// 結果の一覧(FA・市場)。閉じた形で、表の下に置く。行を押すと選手の札
export function resultsTable(id, title, results, withRound, offersLabel = "提示の数") {
  const columns = [...(withRound ? [{ key: "round", label: "ラウンド" }] : []), { key: "team", label: "契約した球団" }, { key: "years", label: "年数" }, { key: "salary", label: "年俸" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "former", label: "前の所属" }, { key: "offers", label: offersLabel }];
  const rows = results.map((x) => ({ ...x, values: { round: String(x.round ?? ""), team: x.stayed ? `${x.team_name}(残留)` : x.team_name, years: `${x.years} 年`, salary: x.salary_text, position: x.position_label, age: `${x.age}歳`, former: x.former_team_name, offers: `${x.offers} 球団` } }));
  return el("details", { id }, el("summary", {}, title), table({ firstLabel: "選手", columns, rows, first: (x) => nameCell(x.name), rowClass: (x) => (x.is_mine ? "mine" : ""), fluid: true, pick: "proc" }));
}
