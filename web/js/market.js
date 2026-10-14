// 市場の画面(判断の画面。①b。作り直し③。D-296〜D-299、D-370):年俸は自動。欲しい選手を「獲得の希望」に入れ(入れた順が優先の順)、
// 「市場を締める」(状態バー。procedure.js)で、前年の順位が低い球団から 1 人ずつ獲得する。
// 手放された選手・FA で決まらなかった選手は能力が見え、指名されなかった候補は評価と一言だけ

import { state } from "./core.js";
import { renderCurrent } from "./screens.js";
import { el } from "./parts.js";
import { runProc } from "./procedure.js";
import { decisionFilters, decisionTable, fetchDecision, resultsTable } from "./decision.js";

export async function marketSection(v, token) {
  const ps = state.proc;
  const m = v.market;
  if (!m) return [el("p", { className: "muted" }, "市場の情報がありません。")];
  const got = await fetchDecision("market", token);
  if (!got) return null;

  const { data, truth } = got;
  const canWish = (p) => v.my_team && !m.done && p.open;
  const onName = (p) => { ps.open = ps.open === p.player_id ? null : p.player_id; renderCurrent(); };
  const detail = (p) => {
    if (ps.open !== p.player_id) return null;
    const d = p.deal || {};
    const box = el("div", { className: "offer-panel", id: "market-panel", dataset: { playerId: p.player_id } });
    const about = p.amateur && p.scouting ? `指名されなかった候補。スカウトの評価 ${p.scouting.grade}:「${p.scouting.comment}」。` : `${d.former_team_name ? `前の所属 ${d.former_team_name}。` : ""}見込みの WAR ${Number(d.expected).toFixed(2)}。`;
    box.append(el("p", {}, `${about}年俸 ${d.calc_text}(自動・1 年)。`));
    if (!canWish(p)) return box;
    const rank = p.offer ? m.offers.findIndex((o) => o.player_id === p.player_id) + 1 : 0;
    if (p.offer) box.append(el("p", { className: "small" }, `獲得の希望 ${rank} 番目(希望は入れた順に優先します)`));
    const row = el("div", { className: "row" });
    if (p.offer) row.append(el("button", { id: "market-cancel", type: "button", className: "secondary", disabled: state.running, onclick: () => runProc("market_cancel", { player_id: p.player_id }) }, "希望を取り消す"));
    else row.append(el("button", { id: "market-send", type: "button", disabled: state.running, onclick: () => runProc("market_offer", { player_id: p.player_id }) }, "獲得を希望する"));
    box.append(row);
    return box;
  };
  const out = [
    decisionFilters("market", data),
    decisionTable("market", data, truth, { onName, detail, rowClass: (p) => (p.offer ? "selected" : !p.open ? "muted" : "") }),
  ];
  if (m.results.length) out.push(resultsTable("market-results", `市場の結果(契約 ${m.results.length} 人)`, m.results, true, "希望した球団"));
  return out;
}

export async function marketClose() {
  state.proc.open = null;
  await runProc("market_close", {}, (view) => { state.proc.lastMarket = view.last_market || null; });
}
