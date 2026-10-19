// FA の画面(判断の画面。①b。D-296〜D-298):操作の帯と表(D-482)。ラウンドの表示と「ラウンドを締める」は状態バー(procedure.js)。
// 行を押すと選手の札、その下に提示の操作(D-486)。説明ブロックは置かない(D-270)

import { state } from "./core.js";
import { el } from "./parts.js";
import { runProc } from "./procedure.js";
import { dealPanel, decisionBand, decisionTable, fetchDecision, resultsTable } from "./decision.js";

export async function faSection(v, token) {
  const fa = v.fa;
  if (!fa) return [el("p", { className: "muted" }, "FA の情報がありません。")];
  const got = await fetchDecision("fa", token);
  if (!got) return null;

  const { data, truth } = got;
  const canOffer = (p) => v.my_team && !fa.done && p.open && p.deal && p.deal.status === "open";
  const actions = (p) => (canOffer(p) ? dealPanel(p, fa, { prefix: "fa", onSend: faSend, onCancel: (pid) => runProc("fa_cancel", { player_id: pid }) }) : null);
  const out = [
    decisionBand("fa", data),
    decisionTable("fa", data, truth, { actions, rowClass: (p) => (p.offer ? "selected" : !p.open ? "muted" : "") }),
  ];
  if (fa.results.length) out.push(resultsTable("fa-results", `FA の結果(契約 ${fa.results.length} 人)`, fa.results, true));
  return out;
}

async function faSend(args) {
  state.proc.lastRound = null;
  await runProc("fa_offer", args);
}

export async function faClose() {
  await runProc("fa_close", {}, (view) => { state.proc.lastRound = view.last_round || null; });
}
