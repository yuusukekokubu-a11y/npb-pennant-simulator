// 市場の画面(判断の画面。①b。作り直し③。D-296〜D-299、D-370):年俸は自動。欲しい選手を「獲得の希望」に入れ(入れた順が優先の順)、
// 「市場を締める」(状態バー。procedure.js)で、前年の順位が低い球団から 1 人ずつ獲得する。
// 手放された選手・FA で決まらなかった選手(能力が見える。指名されなかった候補は市場に出ない。D-398)

import { state } from "./core.js";
import { renderCurrent } from "./screens.js";
import { el, playerLink, table } from "./parts.js";
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

// 育成選手を支配下に上げる(D-375):70 人に空きがあれば、市場を締める前に選べる(年俸は最低年俸・1 年)
export function developmentBox(d) {
  const columns = [{ key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "overall", label: "総合" }, { key: "year", label: "育成" }, { key: "action", label: "" }];
  const rows = d.players.map((p) => ({ ...p, values: {
    position: p.position_label, age: `${p.age}歳`, overall: p.overall, year: `${p.dev_seasons + 1} 年目(あと ${p.left} 年)`,
    action: d.can_promote ? el("button", { type: "button", className: "secondary", disabled: state.running, onclick: () => runProc("promote", { player_id: p.player_id }) }, "支配下に上げる") : "",
  } }));
  const title = `育成選手(${d.count}/${d.max} 人・支配下の空き ${d.space} 人)${d.promoted.length ? `・上げた選手 ${d.promoted.map((x) => x.name).join("、")}` : ""}`;
  return el("details", { id: "development-box", open: d.can_promote && d.players.length > 0 }, el("summary", {}, title),
    d.players.length ? table({ firstLabel: "選手", columns, rows, first: (p) => [playerLink(p.name, p.player_id)], fluid: true }) : el("p", { className: "muted small" }, "育成選手はいません。"));
}
