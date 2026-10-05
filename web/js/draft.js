// ドラフトの画面(判断の画面。①b。D-296〜D-299):列は 名前・総合の推定値とふれ幅・天井・年齢・出身・ポジション・加入後の序列。
// 並べ替えは列見出しのタップ。絞り込みの既定は「未指名だけ」。手薄なポジションに ◆。「次の自分の番まで進める」「パス」は状態バー(procedure.js)。
// 名前を押すと項目別の推定値と「指名」のボタン(自分の番のとき)

import { state } from "./core.js";
import { renderCurrent } from "./screens.js";
import { el } from "./parts.js";
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
    const box = el("div", { className: "offer-panel", id: "draft-panel", dataset: { playerId: p.player_id } },
      el("p", { className: "small" }, `項目別の推定値 ± ふれ幅:${p.scouting.items.map((i) => `${i.label} ${i.text}`).join(" / ")}`));
    if (p.open && v.is_my_turn) box.append(el("div", { className: "row" }, el("button", { id: "draft-pick", type: "button", disabled: state.running, onclick: () => runProc("pick", { player_id: p.player_id }) }, `${v.round} 巡目で指名する`)));
    return box;
  };
  return [
    decisionFilters("draft", data),
    decisionTable("draft", data, truth, { onName, detail, rowClass: (p) => (ps.open === p.player_id ? "selected" : !p.open ? "muted" : "") }),
  ];
}
