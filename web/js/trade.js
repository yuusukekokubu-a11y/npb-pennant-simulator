// トレードの画面(D-378):AI からの提案(受ける・断る)・自分から持ちかける(相手の球団・出す選手・欲しい選手を 1〜3 人ずつ選ぶ)・トレードの記録。
// シーズン中は期限(全日程の 3 分の 2)まで、オフはトレードの段階でできる。そのオフ・シーズンに入った選手は出せない(印「新」)

import { $, state } from "./core.js";
import { call } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, playerLink, table } from "./parts.js";
import { setDirty } from "./progress.js";

async function tradeCall(name, args = {}) {
  const r = await call("trade", { name, args });
  if (!r.ok) throw new Error(r.message);
  return r.value;
}

function ts() {
  if (!state.trade) state.trade = { partner: null, gives: [], gets: [], message: null };
  return state.trade;
}

export async function renderTrade(token) {
  const t = ts();
  const v = await tradeCall("view", { team_id: t.partner });
  if (token !== state.token) return;
  if (t.partner !== v.partner) { t.partner = v.partner; t.gets = []; }
  const open = Boolean(v.window);
  $("trade-window").textContent = open ? (v.window === "season" ? `シーズン中のトレードは ${v.deadline_day} 日目まで(今 ${v.day} 日目)。出す選手・欲しい選手は 1〜${v.max_players} 人ずつ。` : `オフのトレードの段階です。出す選手・欲しい選手は 1〜${v.max_players} 人ずつ。`) : v.reason;
  const msg = $("trade-message");
  msg.className = t.message ? `message ${t.message.ok ? "ok" : "ng"}` : "message";
  msg.textContent = t.message ? t.message.text : "";

  // AI からの提案
  $("trade-offers").replaceChildren(...(v.offers.length ? v.offers.map((o) => el("div", { className: "offer-panel", dataset: { offerId: o.id } },
    el("p", {}, o.text),
    el("div", { className: "row" },
      el("button", { type: "button", className: "trade-accept", disabled: state.running || !open, onclick: () => answer(o.id, true) }, "受ける"),
      el("button", { type: "button", className: "secondary trade-decline", disabled: state.running || !open, onclick: () => answer(o.id, false) }, "断る")))) : [el("p", { className: "muted small" }, "今、AI 球団からの提案はありません。")]));

  // 自分から持ちかける
  $("trade-partner").replaceChildren(...v.teams.map((x) => el("option", { value: x.team_id, selected: x.team_id === v.partner }, x.team_name)));
  $("trade-partner").disabled = state.running;
  $("trade-partner").onchange = (e) => { t.partner = e.target.value; t.gets = []; t.message = null; renderCurrent(); };
  const pick = (list, ids, max) => (p) => el("input", { type: "checkbox", "aria-label": `${p.name}を選ぶ`, checked: ids.includes(p.player_id), disabled: state.running || !open || p.locked || (!ids.includes(p.player_id) && ids.length >= max), onchange: (e) => { if (e.target.checked) ids.push(p.player_id); else ids.splice(ids.indexOf(p.player_id), 1); t.message = null; renderCurrent(); } });
  const columns = [{ key: "pos", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "overall", label: "総合" }, { key: "salary", label: "年俸" }];
  const rows = (list, ids) => list.map((p) => ({ ...p, values: { pos: p.position_label, age: `${p.age}歳`, overall: p.overall, salary: p.salary_text } }));
  const first = (ids, max) => (p) => [pick(null, ids, max)(p), playerLink(p.name, p.player_id), p.locked ? el("span", { className: "sub", title: "このオフ(シーズン)に入った選手は出せません" }, "新") : "", p.foreign ? el("span", { className: "badge-foreign" }, "外") : ""];
  t.gives = t.gives.filter((id) => v.mine.some((p) => p.player_id === id && !p.locked));
  t.gets = t.gets.filter((id) => v.theirs.some((p) => p.player_id === id && !p.locked));
  $("trade-mine").replaceChildren(table({ firstLabel: "出す選手", columns, rows: rows(v.mine, t.gives), first: first(t.gives, v.max_players), rowClass: (p) => (t.gives.includes(p.player_id) ? "selected" : p.locked ? "muted" : ""), fluid: true }));
  $("trade-theirs").replaceChildren(table({ firstLabel: "欲しい選手", columns, rows: rows(v.theirs, t.gets), first: first(t.gets, v.max_players), rowClass: (p) => (t.gets.includes(p.player_id) ? "selected" : p.locked ? "muted" : ""), fluid: true }));
  const name = (list, id) => (list.find((p) => p.player_id === id) || {}).name || "";
  $("trade-summary").textContent = `出す:${t.gives.map((id) => name(v.mine, id)).join("・") || "なし"} ⇔ 欲しい:${t.gets.map((id) => name(v.theirs, id)).join("・") || "なし"}`;
  $("trade-propose").disabled = state.running || !open || !t.gives.length || !t.gets.length;
  $("trade-propose").onclick = () => propose();

  // このシーズン(オフ)のトレード
  const recCols = [{ key: "text", label: "交換" }];
  $("trade-history").replaceChildren(v.trades.length ? table({ firstLabel: "日", columns: recCols, rows: v.trades.map((x) => ({ ...x, values: { text: x.text } })), first: (x) => [el("span", {}, x.day === null || x.day === undefined ? "オフ" : `${x.day}日目`)], rowClass: (x) => (x.is_mine ? "mine" : ""), fluid: true }) : el("p", { className: "muted small" }, "まだありません。"));
}

async function propose() {
  const t = ts();
  await run(() => tradeCall("propose", { team_id: t.partner, gives: t.gives, gets: t.gets }));
}

async function answer(offerId, accept) {
  await run(() => tradeCall("answer", { offer_id: offerId, accept }));
}

async function run(fn) {
  if (state.running) return;
  state.running = true;
  const t = ts();
  try {
    const v = await fn();
    const r = v.result || {};
    t.message = { ok: Boolean(r.accepted), text: r.message || "" };
    if (r.accepted) { t.gives = []; t.gets = []; }
    setDirty(true);
    state.cache.clear();
  } catch (err) {
    t.message = { ok: false, text: `操作できませんでした:${err.message}` };
  } finally {
    state.running = false;
  }
  renderCurrent();
}
