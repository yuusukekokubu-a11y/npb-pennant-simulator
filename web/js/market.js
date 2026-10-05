// 市場の画面(判断の画面。①b。D-296〜D-300):FA と同じ提示の方式で 1 ラウンド。手放された選手は成績も、指名されなかった候補は評価も見られる。
// 名前を押すと提示のパネル。「市場を締める」は状態バー(procedure.js)

import { state } from "./core.js";
import { answer } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el } from "./parts.js";
import { runProc } from "./procedure.js";
import { dealPanel, decisionFilters, decisionTable, fetchDecision, resultsTable } from "./decision.js";

export async function marketSection(v, token) {
  const ps = state.proc;
  const m = v.market;
  if (!m) return [el("p", { className: "muted" }, "市場の情報がありません。")];
  const got = await fetchDecision("market", token);
  if (!got) return null;
  let prefs = null;
  if (state.answerLevel > 0) {
    prefs = await answer("fa_answers", {});
    if (token !== state.token) return null;
  }
  const { data, truth } = got;
  const canOffer = (p) => v.my_team && !m.done && p.open;
  const onName = (p) => { ps.open = ps.open === p.player_id ? null : p.player_id; renderCurrent(); };
  const detail = (p) => {
    if (ps.open !== p.player_id) return null;
    const items = el("p", { className: "small" }, `項目別の推定値 ± ふれ幅:${p.scouting.items.map((i) => `${i.label} ${i.text}`).join(" / ")}`);
    if (!canOffer(p)) return el("div", { className: "offer-panel", id: "market-panel", dataset: { playerId: p.player_id } }, items);
    const panel = dealPanel(p, m, prefs, { prefix: "market", onSend: (args) => runProc("market_offer", args), onCancel: (pid) => runProc("market_cancel", { player_id: pid }) });
    panel.append(items);
    return panel;
  };
  const out = [
    decisionFilters("market", data),
    decisionTable("market", data, truth, { onName, detail, rowClass: (p) => (p.offer ? "selected" : !p.open ? "muted" : "") }),
  ];
  if (m.results.length) out.push(resultsTable("market-results", `市場の結果(契約 ${m.results.length} 人)`, m.results, false));
  return out;
}

export async function marketClose() {
  state.proc.open = null;
  await runProc("market_close", {}, (view) => { state.proc.lastMarket = view.last_market || null; });
}
