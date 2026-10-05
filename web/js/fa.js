// FA の画面(判断の画面。①b。D-296〜D-298):絞り込みの 1 行と表。ラウンドの表示と「ラウンドを締める」は状態バー(procedure.js)。
// 名前を押すと提示のパネル。説明ブロックは置かない(D-270)

import { state } from "./core.js";
import { answer } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el } from "./parts.js";
import { runProc } from "./procedure.js";
import { dealPanel, decisionFilters, decisionTable, fetchDecision, resultsTable } from "./decision.js";

export async function faSection(v, token) {
  const ps = state.proc;
  const fa = v.fa;
  if (!fa) return [el("p", { className: "muted" }, "FA の情報がありません。")];
  const got = await fetchDecision("fa", token);
  if (!got) return null;
  let prefs = null;
  if (state.answerLevel > 0) {
    prefs = await answer("fa_answers", {});
    if (token !== state.token) return null;
  }
  const { data, truth } = got;
  const canOffer = (p) => v.my_team && !fa.done && p.open && p.deal && p.deal.status === "open";
  const onName = (p) => { ps.faOpen = ps.faOpen === p.player_id ? null : p.player_id; renderCurrent(); };
  const detail = (p) => (ps.faOpen === p.player_id && canOffer(p) ? dealPanel(p, fa, prefs, { prefix: "fa", onSend: faSend, onCancel: (pid) => runProc("fa_cancel", { player_id: pid }) }) : null);
  const out = [
    decisionFilters("fa", data),
    decisionTable("fa", data, truth, { onName, detail, rowClass: (p) => (p.offer ? "selected" : !p.open ? "muted" : "") }),
  ];
  if (fa.results.length) out.push(resultsTable("fa-results", `FA の結果(契約 ${fa.results.length} 人)`, fa.results, true));
  return out;
}

async function faSend(args) {
  state.proc.lastRound = null;
  await runProc("fa_offer", args);
}

export async function faClose() {
  state.proc.faOpen = null;
  await runProc("fa_close", {}, (view) => { state.proc.lastRound = view.last_round || null; });
}
