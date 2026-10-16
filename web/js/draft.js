// ドラフトの画面(判断の画面。①b。作り直し③。D-296〜D-299、D-369、D-418):列は 名前・見立て(役割を選ぶと能力の各項目と特殊能力)・年齢・出身・ポジション・加入後の序列。
// 能力は自球団のスカウトの見立て(入団すると本当の数字になる)。並べ替えは列見出しのタップ。絞り込みの既定は「未指名だけ」。手薄なポジションに ◆。
// 1 巡目は全球団の入札と抽選:名前を押すと「入札する」のボタン。2 巡目からは順番に指名(「次の自分の番まで進める」「パス」は状態バー。procedure.js)

import { state } from "./core.js";
import { renderCurrent } from "./screens.js";
import { el, estimateBlock, playerLink, table } from "./parts.js";
import { runProc } from "./procedure.js";
import { decisionFilters, decisionTable, fetchDecision } from "./decision.js";

export async function draftSection(v, token) {
  const ps = state.proc;
  const got = await fetchDecision("draft", token);
  if (!got) return null;
  const { data, truth } = got;
  const onName = (p) => { ps.open = ps.open === p.player_id ? null : p.player_id; renderCurrent(); };
  const detail = (p) => {
    if (ps.open !== p.player_id) return null;
    const box = el("div", { className: "offer-panel", id: "draft-panel", dataset: { playerId: p.player_id } }, ...estimateBlock(p.scouting));
    if (p.open && v.is_my_turn) {
      const label = v.bidding ? "1 巡目に入札する" : v.development_draft ? `育成 ${v.round} 巡目で指名する` : `${v.round} 巡目で指名する`;
      box.append(el("div", { className: "row" }, el("button", { id: "draft-pick", type: "button", disabled: state.running, onclick: () => draftPick(p.player_id) }, label)));
    }
    return box;
  };
  const out = [
    decisionFilters("draft", data),
    decisionTable("draft", data, truth, { onName, detail, rowClass: (p) => (ps.open === p.player_id ? "selected" : !p.open ? "muted" : "") }),
  ];
  if (v.lottery && v.lottery.length) out.push(lotteryTable(v.lottery));
  return out;
}

async function draftPick(playerId) {
  await runProc("pick", { player_id: playerId }, (view) => { state.proc.lastBid = view.last_bid || null; });
}

// 1 巡目の入札の結果(閉じた形で、表の下に置く)
function lotteryTable(rows) {
  const columns = [{ key: "step", label: "回" }, { key: "teams", label: "入札した球団" }, { key: "winner", label: "交渉権" }, { key: "position", label: "ポジション" }];
  const list = rows.map((x) => ({ ...x, values: { step: `${x.step} 回目`, teams: x.team_names.join("、"), winner: x.teams.length > 1 ? `${x.winner_name}(抽選)` : x.winner_name, position: x.position_label } }));
  const contested = rows.filter((x) => x.teams.length > 1).length;
  return el("details", { id: "draft-lottery" }, el("summary", {}, `1 巡目の入札の結果(${rows.length} 人・抽選 ${contested} 回)`),
    table({ firstLabel: "選手", columns, rows: list, first: (x) => [playerLink(x.name, x.player_id)], rowClass: (x) => (x.won ? "mine" : x.is_mine ? "selected" : ""), fluid: true }));
}
