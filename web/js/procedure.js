// オフの手続きの枠(段階・状態バー・自球団の状況のパネル・おまかせ・次の手続きへ・指名と入退団の記録)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { call } from "./backend.js";
import { back, current, openPage, renderCurrent, showScreen } from "./screens.js";
import { el, playerLink, table } from "./parts.js";
import { renderProgress, setDirty } from "./progress.js";
import { buildStatsFilters } from "./stats.js";
import { contractSection } from "./contract.js";
import { faClose, faSection } from "./fa.js";
import { draftSection } from "./draft.js";
import { developmentBox, marketClose, marketSection } from "./market.js";
import { foreignClose, foreignSection } from "./foreign.js";
import { outlookPanel } from "./decision.js";
import { activeDraftSection } from "./active_draft.js";

// ---- オフの手続き(F3-1。D-201〜D-207) ----

async function procCall(name, args = {}) {
  const r = await call("offseason", { name, args });
  if (!r.ok) throw new Error(r.message);
  return r.value;
}

const DECISION_STAGES = ["fa", "foreign", "draft", "market"]; // 判断の画面(①b):状態バーを押すと自球団の状況のパネルが開く(D-298)

export async function renderProcedure(token) {
  const r = await call("query", { name: "offseason_view", args: {} });
  if (!r.ok) throw new Error(r.message);
  if (token !== state.token) return;
  const v = r.value;
  const ps = state.proc;
  ps.phase = v.phase;
  ps.stage = v.stage;
  // 段階のタブは 8 つ(契約・FA・外国人・ドラフト・市場・トレード・現役ドラフト・完了。D-376、D-378、D-380)。今の段階の名前を大きく出す(D-271)
  $("proc-title").textContent = v.phase_label;
  const at = v.phases.findIndex((x) => x.key === v.stage);
  $("proc-steps").replaceChildren(...v.phases.map((p, i) => el("li", { className: i === at ? "current" : i < at ? "done" : "", title: p.label }, p.short || p.label)));
  renderProcedureBar(v);
  renderAutoLog();
  if (ps.lastRound && v.stage === "fa") {
    const lr = ps.lastRound;
    $("proc-message").className = "message ok";
    $("proc-message").textContent = lr.signed.length ? `ラウンド ${lr.round} の結果:${lr.signed.map((x) => `${x.name} → ${x.team_name}(${x.years} 年・${x.salary_text})`).join("、")}` : `ラウンド ${lr.round} の結果:成立した契約はありません。`;
  } else if (ps.lastBid && v.stage === "draft") {
    const lb = ps.lastBid;
    const mine = lb.results.filter((x) => x.is_mine);
    $("proc-message").className = lb.won ? "message ok" : "message";
    $("proc-message").textContent = `1 巡目の入札の結果:${mine.map((x) => (x.teams.length > 1 ? `${x.name} は ${x.teams.length} 球団が入札し、抽選で ${x.winner_name} が交渉権` : `${x.name} は単独で ${x.winner_name}`)).join("、")}。${lb.won ? "" : "外れたので、残りの候補からもう一度入札してください。"}`;
  } else if (ps.lastForeign && v.stage === "foreign") {
    const mine = ps.lastForeign.signed.filter((x) => x.is_mine);
    $("proc-message").className = "message ok";
    $("proc-message").textContent = `外国人市場の結果:契約 ${ps.lastForeign.signed.length} 人。自球団は ${mine.length ? mine.map((x) => `${x.name}(${x.salary_text})`).join("、") : "獲得なし"}。`;
  } else if (ps.lastMarket && v.stage === "market") {
    const mine = ps.lastMarket.signed.filter((x) => x.is_mine);
    $("proc-message").className = "message ok";
    $("proc-message").textContent = `市場の結果:契約 ${ps.lastMarket.signed.length} 人。自球団は ${mine.length ? mine.map((x) => `${x.name}(${x.years} 年・${x.salary_text})`).join("、") : "獲得なし"}。`;
  }
  const outlookBox = $("proc-outlook");
  outlookBox.replaceChildren();
  if (ps.outlookOpen && v.my_team && DECISION_STAGES.includes(v.stage)) {
    const panel = await outlookPanel(token);
    if (panel === null || token !== state.token) return;
    outlookBox.replaceChildren(panel);
  }
  const body = $("proc-body");
  let parts = [];
  if (v.stage === "contract") parts = await contractSection(v, token);
  else if (v.phase === "fa") parts = await faSection(v, token);
  else if (v.phase === "foreign") parts = await foreignSection(v, token);
  else if (v.phase === "draft" || v.phase === "dev_draft") parts = await draftSection(v, token);
  else if (v.phase === "market") parts = await marketSection(v, token);
  else if (v.phase === "trade") parts = tradeStageSection(v);
  else if (v.phase === "active_draft") parts = activeDraftSection(v);
  if (parts === null || token !== state.token) return;
  body.replaceChildren(...parts);
  $("proc-development").replaceChildren(v.stage === "market" && v.development ? developmentBox(v.development) : ""); // 育成選手を支配下に上げる(D-375)
  renderProcedureContracts(v);
  $("proc-history").replaceChildren(historyTable(v.picks, v.released));
  $("proc-history-box").hidden = v.stage === "contract";
  $("proc-history-box").open = false;
}

// 状態バー(D-271、①b):段階ごとの要約・資金・主ボタンと、段階の操作(ラウンドを締める・次の自分の番まで・パス・市場を締める)。
// FA・ドラフト・市場では、要約を押すと自球団の状況のパネルが開く(D-298)。説明の文は置かない(D-270)
function renderProcedureBar(v) {
  const ps = state.proc;
  const mine = v.my_team;
  const c = v.contracts;
  let info = "";
  let canNext = !state.running;
  if (v.stage === "contract" && v.renewal) {
    const rn = v.renewal;
    const n = rn.roster_counts;
    info = `${rn.players} 人:` + ["accepted", "declared", "released", "contracted"].filter((k) => n[k]).map((k) => `${rn.status_labels[k]} ${n[k]}`).join("・");
    canNext = canNext && rn.can_next;
  } else if (v.stage === "fa" && v.fa) {
    const f = v.fa;
    info = f.done ? `FA 終了:宣言 ${f.counts.declared}・契約 ${f.counts.signed}・市場へ ${f.counts.unsigned}` : `ラウンド ${f.round}/${f.rounds}:宣言 ${f.counts.declared}・契約 ${f.counts.signed}・残り ${f.counts.open}${mine ? `・提示 ${f.offers.length}・空き枠 ${f.space ?? "-"}` : ""}`;
  } else if (v.stage === "draft") {
    const turn = v.phase_finished ? "終了" : v.bidding ? (v.is_my_turn ? `入札${v.bid_step > 1 ? `(${v.bid_step} 回目)` : ""}・あなたの番` : "入札") : v.is_my_turn ? "あなたの番" : `${(v.order.find((o) => o.team_id === v.current_team) || {}).team_name || ""} の番`;
    const dev = v.development_draft ? "育成 " : "";
    const count = mine ? (v.development_draft ? `・育成 ${mine.development}/${mine.development_max} 人` : `・${mine.players}/${mine.max} 人`) : `・候補 ${v.counts.candidates} 人`;
    info = `${dev}${Math.min(v.round, v.total_rounds)}/${v.total_rounds} 巡・${turn}${count}`;
  } else if (v.stage === "foreign" && v.foreign) {
    const f = v.foreign;
    info = f.done ? `外国人市場 終了:契約 ${f.counts.signed} 人(自球団 ${f.counts.mine})` : `外国人市場 ${f.counts.pool} 人・希望 ${f.counts.offers}・空き枠 ${f.space ?? "-"}${mine ? `・外国人 ${mine.foreign} 人(一軍 ${f.active_max ?? "-"} まで)` : ""}`;
  } else if (v.stage === "trade" && v.trade) {
    info = `トレード:提案 ${v.trade.offers} 件・成立 ${v.trade.trades.length} 件`;
  } else if (v.stage === "active_draft" && v.active_draft) {
    const a = v.active_draft;
    info = !a.offers_done ? "現役ドラフト:出す候補を選ぶ" : a.finished ? `現役ドラフト 終了:指名 ${a.picks.length} 人` : a.is_my_turn ? "現役ドラフト:あなたの番" : `現役ドラフト:${a.current_team_name} の番`;
  } else if (v.stage === "market" && v.market) {
    const m = v.market;
    info = m.done ? `市場 終了:契約 ${m.counts.signed} 人(自球団 ${m.counts.mine})` : `市場 ${m.counts.pool} 人・希望 ${m.counts.offers}・空き枠 ${m.space ?? "-"}`;
  }
  const canOutlook = Boolean(mine) && DECISION_STAGES.includes(v.stage);
  const text = $("proc-info").parentElement;
  text.classList.toggle("clickable", canOutlook);
  text.onclick = canOutlook ? () => { ps.outlookOpen = !ps.outlookOpen; renderCurrent(); } : null;
  text.setAttribute("role", canOutlook ? "button" : "status");
  text.title = canOutlook ? "押すと自球団の状況(ポジション別の人数・主な選手・空き枠・資金)が開きます" : "";
  if (canOutlook) text.setAttribute("aria-expanded", String(Boolean(ps.outlookOpen)));
  else text.removeAttribute("aria-expanded");
  $("proc-info").textContent = info + (canOutlook ? (ps.outlookOpen ? " ▴" : " ▾") : "");
  const b = c && c.mine;
  const budget = $("proc-budget");
  if (b) {
    const over = b.hard && b.over_now > 0;
    budget.className = over ? "warn" : "";
    budget.textContent = b.cap ? `総年俸 ${b.total_text}(資金の ${b.usage}%)${over ? `・資金を ${b.over_now.toLocaleString()} 万円超過` : ""}` : `総年俸 ${b.total_text}`;
  } else {
    budget.textContent = "";
  }
  $("proc-next").disabled = !canNext;
  $("proc-stage-auto").disabled = state.running;
  // 段階の操作もバーに置く(FA:ラウンドを締める。ドラフト:次の自分の番まで・パス。市場:市場を締める)
  const acts = $("proc-actions");
  for (const id of ["renew-auto", "fa-close", "foreign-close", "draft-advance", "draft-pass", "market-close"]) {
    const old = $(id);
    if (old) old.remove();
  }
  const btn = (id, label, title, onclick, disabled = false) => el("button", { id, type: "button", className: "secondary", title, disabled: disabled || state.running, onclick }, label);
  if (v.stage === "fa" && v.fa && mine && !v.fa.done) {
    acts.prepend(btn("fa-close", `ラウンド${v.fa.round}を締める`, "AI 球団の提示と合わせて、選手が選びます(結果が出ます)", () => faClose()));
  } else if (v.stage === "foreign" && v.foreign && mine && !v.foreign.done) {
    acts.prepend(btn("foreign-close", "外国人市場を締める", "AI 球団の希望と合わせて、前年の順位が低い球団から 1 人ずつ獲得します(結果が出ます)", () => foreignClose()));
  } else if (v.stage === "draft" && mine && !v.phase_finished) {
    const dev = v.phase === "dev_draft";  // ドラフトは一度パスしたらその年の指名は終わり(選択終了。D-397)。育成ドラフトは巡ごとのパス
    if (v.is_my_turn) acts.prepend(btn("draft-pass", dev ? "パス" : "指名を終える", dev ? "今の巡は指名しない" : "今年の指名を終えます(選択終了。このあと指名の番は来ません)", () => runProc("pass")));
    else acts.prepend(btn("draft-advance", "自分の番まで", "次の自分の番まで、AI 球団の指名を進めます", () => runProc("advance")));
  } else if (v.stage === "market" && v.market && mine && !v.market.done) {
    acts.prepend(btn("market-close", "市場を締める", "AI 球団の希望と合わせて、前年の順位が低い球団から 1 人ずつ獲得します(結果が出ます)", () => marketClose()));
  }
}

// 「この段階をおまかせ」の結果:AI が自球団の分として行ったことの一覧(D-271)
function renderAutoLog() {
  const box = $("proc-autolog");
  const log = state.proc.autoLog;
  if (!log) { box.replaceChildren(); return; }
  const items = log.items;
  box.replaceChildren(el("details", { id: "auto-log" }, el("summary", {}, `おまかせの結果(${log.stage_label}):${items.length ? `${items.length} 件` : "自球団の動きなし"}`),
    items.length ? el("ul", { className: "offer-history" }, ...items.map((x) => el("li", {}, x.text))) : el("p", { className: "muted small" }, "なし")));
}

// 契約更改の結果(全球団。契約の段階の表の下に、閉じた形で出す)
function renderProcedureContracts(v) {
  const c = v.contracts;
  const box = $("proc-contracts");
  if (!c || v.stage !== "contract") { box.replaceChildren(); return; }
  const parts = [];
  if (c.renewals.length || c.budget_releases.length) {
    const mineFirst = (rows) => [...rows].sort((a, b) => (b.is_mine ? 1 : 0) - (a.is_mine ? 1 : 0));
    const renewCols = [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "old", label: "前の年俸" }, { key: "salary", label: "新しい年俸" }, { key: "change", label: "増減" }, { key: "years", label: "年数" }];
    const renewRows = mineFirst(c.renewals).map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, old: r.old_text || "-", salary: r.salary_text, change: r.change === null ? "-" : `${r.change >= 0 ? "+" : ""}${r.change.toLocaleString()}`, years: `${r.years} 年` } }));
    const det = el("details", { id: "renewal-results" }, el("summary", {}, `全球団の更改の結果(更改 ${c.renewals.length} 人${c.budget_releases.length ? `・資金超過 ${c.budget_releases.length} 人` : ""}・単価 ${c.rate_text})`));
    if (c.renewals.length) det.append(table({ firstLabel: "選手", columns: renewCols, rows: renewRows, first: (r) => [playerLink(r.name, r.player_id)], rowClass: (r) => (r.is_mine ? "mine" : ""), fluid: true, limit: state.proc.renewalsShown || 20, more: () => { state.proc.renewalsShown = (state.proc.renewalsShown || 20) + 50; renderCurrent(); } }));
    if (c.budget_releases.length) det.append(el("h3", {}, "資金の超過で自由契約(AI 球団)"), table({ firstLabel: "選手", columns: [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "salary", label: "年俸" }], rows: c.budget_releases.map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, salary: r.salary_text } })), first: (r) => [el("span", {}, r.name)], fluid: true }));
    parts.push(det);
  }
  box.replaceChildren(...parts);
}

export async function runProc(name, args = {}, onResult = null) {
  if (state.running) return;
  state.running = true;
  $("proc-message").className = "message";
  $("proc-message").textContent = "";
  if (name !== "stage_auto") state.proc.autoLog = null;
  try {
    const r = await procCall(name, args);
    if (onResult) onResult(r);
    state.proc.selected = new Set();
    state.cache.clear();
    setDirty(true);
    if (r.finished) {
      state.view = r.view_all;
      state.stats.season = "current";
      state.gamesDay = null;
      buildStatsFilters();
      const s = r.summary;
      state.pages = [];
      showScreen("progress");
      renderProgress();
      $("progress-message").className = "message ok";
      $("progress-message").textContent = `オフの手続きが終わり、${s.next_year}シーズン目が始まりました(引退 ${s.counts.retired}人・入団 ${s.counts.rookies}人)。`;
      openPage("offseason", { year: s.year });
      return;
    }
    renderCurrent();
  } catch (err) {
    $("proc-message").className = "message ng";
    $("proc-message").textContent = `操作できませんでした:${err.message}`;
  } finally {
    state.running = false;
  }
}

// 「全部おまかせ」(メニューの中。D-271):残りの手続きを最後まで AI の方針で。やり直せないので確認し、未保存なら保存を案内する
export async function procAuto() {
  const unsaved = state.dirty ? "\n\n未保存の変更があります。先に「保存」しておくと、あとでこの時点からやり直せます。" : "";
  if (!confirm(`残りのオフの手続きを全部、AI の方針で(自分の球団の判断も含めて)最後まで進め、次のシーズンを始めます。やり直せません。よろしいですか?${unsaved}`)) return;
  if (current().name === "settings") back();
  await runProc("auto");
}

// 「この段階をおまかせ」(主ボタン。D-271):今の段階だけを AI の方針で進め、AI が自球団の分として行ったことの一覧を出して、次の段階の入口で止まる
export async function procStageAuto() {
  const ask = { draft: "ドラフト(育成ドラフトを含む)の残りの自球団の番を、AI の方針で指名します(やり直せません)。よろしいですか?", fa: "FA の残りのラウンドで、自球団も AI の方針で提示します(やり直せません)。よろしいですか?" }[state.proc.stage];
  if (ask && !confirm(ask)) return; // 確認はドラフトと FA の段階だけ(D-284)
  state.proc.renewalOpen = null;
  await runProc("stage_auto", {}, (r) => { state.proc.autoLog = r.auto_log || null; });
}

// 「次の手続きへ」(D-271):自分の操作を終えて進む(AI の代行はしない)
export async function procNext() {
  const stage = state.proc.stage;
  const ask = { fa: "FA の残りのラウンドでは、自球団は追加の提示をしません(今のラウンドの提示は有効)。次へ進みますか?", draft: state.proc.phase === "dev_draft" ? "育成ドラフトの自球団の残りの番は、すべてパスします。次へ進みますか?" : "ドラフトの自球団の残りの番(1 巡目の入札を含む)は、すべてパスして、育成ドラフトへ進みます。よろしいですか?", foreign: "外国人市場を今の希望で締めて(希望していなければ、自球団は獲得なし)、ドラフトへ進みます。よろしいですか?", market: "市場を今の希望で締めて(希望していなければ、自球団は獲得なし)、次の段階へ進みます。よろしいですか?", trade: "答えていないトレードの提案は断ったことにして、次の段階へ進みます。よろしいですか?", active_draft: "現役ドラフトの残り(まだ決めていない候補・指名)は AI の方針で決めて、手続きを完了します。よろしいですか?" }[stage];
  if (ask && !confirm(ask)) return;
  state.proc.lastRound = null;
  state.proc.lastMarket = null;
  state.proc.lastForeign = null;
  state.proc.lastBid = null;
  state.proc.autoLog = null;
  await runProc("next");
}

// 指名・自由契約・市場の履歴(閉じた形で、表の下に置く)
function historyTable(picks, released) {
  const rows = [];
  for (const x of released) rows.push({ k: "自由契約", team: x.team_name, name: x.name, pos: x.position_label, age: x.age, mine: x.is_mine });
  const kind = (x) => (x.phase === "draft" ? `ドラフト ${x.round} 巡${x.lottery ? "(抽選)" : ""}` : x.phase === "dev_draft" ? `育成ドラフト ${x.round} 巡` : x.phase === "foreign" ? "外国人市場" : "市場");
  for (const x of picks) rows.push({ k: kind(x), team: x.team_name, name: x.player_id ? x.name : `(${x.note === "full" ? "空き枠なし" : x.note === "budget" ? "資金不足" : x.phase === "draft" ? "指名を終える" : "見送り"})`, pos: x.position_label, age: x.age, mine: x.is_mine });
  if (!rows.length) return el("p", { className: "muted small" }, "まだありません。");
  const body = el("tbody", {}, ...rows.map((r) => el("tr", { className: r.mine ? "mine" : "" }, el("td", {}, r.k), el("td", {}, r.team), el("td", {}, r.name), el("td", {}, r.pos || ""), el("td", {}, r.age ? `${r.age}歳` : ""))));
  return el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "手続き"), el("th", {}, "球団"), el("th", {}, "選手"), el("th", {}, "ポジション"), el("th", {}, "年齢"))), body));
}

// オフのトレードの段階(D-378):このオフのトレードと、提案の件数。交換はトレードの画面で
function tradeStageSection(v) {
  const tr = v.trade || { offers: 0, trades: [] };
  const box = [el("div", { className: "row" }, el("button", { id: "proc-open-trade", type: "button", disabled: state.running || !v.my_team, onclick: () => { state.trade = null; openPage("trade"); } }, tr.offers ? `トレードの画面を開く(提案 ${tr.offers} 件)` : "トレードの画面を開く"))];
  box.push(tr.trades.length ? el("ul", { className: "offer-history", id: "proc-trades" }, ...tr.trades.map((x) => el("li", { className: x.is_mine ? "mine" : "" }, x.text))) : el("p", { className: "muted small" }, "このオフのトレードはまだありません。"));
  return box;
}
