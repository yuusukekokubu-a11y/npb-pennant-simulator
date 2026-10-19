// 外国人市場の画面(判断の画面。D-376):候補の能力は自球団のスカウトの見立て(D-418)。年俸は内部の評価の段階の表(1 年)。
// 欲しい選手を「獲得の希望」に入れ(入れた順が優先の順)、「外国人市場を締める」(状態バー。procedure.js)で、前年の順位が低い球団から 1 人ずつ獲得する

import { state } from "./core.js";
import { el } from "./parts.js";
import { runProc } from "./procedure.js";
import { decisionBand, decisionTable, fetchDecision, resultsTable } from "./decision.js";

export async function foreignSection(v, token) {
  const f = v.foreign;
  if (!f) return [el("p", { className: "muted" }, "外国人市場の情報がありません。")];
  const got = await fetchDecision("foreign", token);
  if (!got) return null;
  const { data, truth } = got;
  const canWish = (p) => v.my_team && !f.done && p.open;
  // 札の下の操作(D-486):年俸と「獲得を希望する」「希望を取り消す」。能力の見立ては札の上
  const actions = (p) => {
    const d = p.deal || {};
    const box = el("div", { className: "offer-panel", id: "foreign-panel", dataset: { playerId: p.player_id } }, el("p", {}, `年俸 ${d.calc_text}(1 年)。`));
    if (!canWish(p)) return box;
    const rank = p.offer ? f.offers.findIndex((o) => o.player_id === p.player_id) + 1 : 0;
    if (p.offer) box.append(el("p", { className: "small" }, `獲得の希望 ${rank} 番目(希望は入れた順に優先します)`));
    const row = el("div", { className: "row" });
    if (p.offer) row.append(el("button", { id: "foreign-cancel", type: "button", className: "secondary", disabled: state.running, onclick: () => runProc("foreign_cancel", { player_id: p.player_id }) }, "希望を取り消す"));
    else row.append(el("button", { id: "foreign-send", type: "button", disabled: state.running, onclick: () => runProc("foreign_offer", { player_id: p.player_id }) }, "獲得を希望する"));
    box.append(row);
    return box;
  };
  const out = [
    decisionBand("foreign", data),
    decisionTable("foreign", data, truth, { actions, rowClass: (p) => (p.offer ? "selected" : !p.open ? "muted" : "") }),
  ];
  if (f.results.length) out.push(resultsTable("foreign-results", `外国人市場の結果(契約 ${f.results.length} 人)`, f.results.map((x) => ({ ...x, former_team_name: "海外" })), true, "希望した球団"));
  return out;
}

export async function foreignClose() {
  await runProc("foreign_close", {}, (view) => { state.proc.lastForeign = view.last_foreign || null; });
}
