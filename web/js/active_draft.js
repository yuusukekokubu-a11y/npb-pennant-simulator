// オフの手続きの「現役ドラフト」の段階(D-380):出す候補を選ぶ → 前年の順位が低い球団から 1 人ずつ指名(あなたの番で選ぶ)。
// 行を押すと選手の札、その下に「出す候補に入れる」「指名する」(D-486)

import { state } from "./core.js";
import { el, nameCell, table } from "./parts.js";
import { renderCurrent } from "./screens.js";
import { runProc } from "./procedure.js";

const COLUMNS = [{ key: "state", label: "状態" }, { key: "pos", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "overall", label: "総合" }, { key: "salary", label: "年俸" }];
const values = (p, more = {}) => ({ ...p, values: { pos: p.position_label, age: `${p.age}歳`, overall: p.overall, salary: p.salary_text, ...more } });

export function activeDraftSection(v) {
  const a = v.active_draft;
  if (!a) return [];
  const parts = [];
  const ps = state.proc;
  if (!a.offers_done) {
    if (!v.my_team) return [el("p", { className: "muted small" }, "候補を決めています。")];
    // 出す候補を選ぶ(AI のおすすめが最初から入っている)
    if (!ps.activeOffer || ps.activeOfferYear !== v.year) {
      ps.activeOffer = [...a.recommended];
      ps.activeOfferYear = v.year;
    }
    const chosen = ps.activeOffer;
    const toggle = (p) => { if (chosen.includes(p.player_id)) chosen.splice(chosen.indexOf(p.player_id), 1); else chosen.push(p.player_id); renderCurrent(); };
    const actions = (p) => {
      const on = chosen.includes(p.player_id);
      return el("div", { className: "row" }, el("button", { type: "button", className: on ? "secondary active-toggle" : "active-toggle", disabled: state.running || (!on && chosen.length >= a.max_offers), onclick: () => toggle(p) }, on ? "出す候補から外す" : "出す候補に入れる"));
    };
    const stateText = (p) => [chosen.includes(p.player_id) ? "出す" : "", a.recommended.includes(p.player_id) ? "おすすめ" : ""].filter(Boolean).join("・");
    parts.push(el("h2", {}, `出す候補(${a.max_offers} 人まで)`));
    parts.push(a.eligible.length
      ? el("div", { id: "active-eligible" }, table({ firstLabel: "選手", columns: COLUMNS, rows: a.eligible.map((p) => values(p, { state: stateText(p) })), first: (p) => nameCell(p.name), rowClass: (p) => (chosen.includes(p.player_id) ? "selected" : ""), fluid: true, pick: "proc", actions }))
      : el("p", { className: "muted small" }, "出せる選手がいません(一軍にいた選手・外国人・このオフに入った選手・年俸の高い選手は出せません)。"));
    parts.push(el("div", { className: "row" }, el("button", { id: "active-offer", type: "button", disabled: state.running || !chosen.length, onclick: () => runProc("active_offer", { player_ids: [...chosen] }) }, `この ${chosen.length} 人を出す`)));
    return parts;
  }
  if (a.is_my_turn) {
    parts.push(el("h2", {}, "指名する選手を選ぶ(あなたの番)"));
    const actions = (p) => el("div", { className: "row" }, el("button", { type: "button", className: "active-pick", disabled: state.running, onclick: () => runProc("active_pick", { player_id: p.player_id }) }, "この選手を指名する"));
    parts.push(el("div", { id: "active-choices" }, table({ firstLabel: "選手", columns: [{ key: "team", label: "球団" }, ...COLUMNS], rows: a.choices.map((p) => values(p, { team: p.team_name, state: p.player_id === a.recommended_pick ? "おすすめ" : "" })),
      first: (p) => nameCell(p.name), rowClass: (p) => (p.player_id === a.recommended_pick ? "selected" : ""), fluid: true, pick: "proc", actions })));
  } else if (!a.finished) {
    parts.push(el("p", { className: "small" }, `${a.current_team_name} の番です。`));
  }
  parts.push(el("h2", {}, "指名の結果"));
  parts.push(a.picks.length ? el("ul", { className: "offer-history", id: "active-picks" }, ...a.picks.map((x) => el("li", { className: x.is_mine ? "mine" : "" }, `${x.team_name}:${x.name}(${x.position_label}。${x.from_team_name} から)`))) : el("p", { className: "muted small" }, "まだありません。"));
  parts.push(el("details", { id: "active-offers" }, el("summary", {}, "各球団の候補"), el("ul", { className: "offer-history" }, ...a.offers.map((o) => el("li", { className: o.is_mine ? "mine" : "" }, `${o.team_name}:${o.players.map((p) => p.name).join("・") || "なし"}`)))));
  return parts;
}
