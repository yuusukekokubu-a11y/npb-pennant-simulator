// オフの手続きの枠(段階・状態バー・おまかせ・次の手続きへ・成績つきの選手の表の切り替え)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { answer, call, query } from "./backend.js";
import { back, current, openPage, renderCurrent, showScreen } from "./screens.js";
import { el, playerLink, sortToggle, sortWord, table } from "./parts.js";
import { renderProgress, setDirty } from "./progress.js";
import { buildStatsFilters } from "./stats.js";
import { contractSection, renewAuto } from "./contract.js";
import { faSection } from "./fa.js";
import { historyTable, marketSection, poolSection } from "./market.js";

// ---- オフの手続き(F3-1。D-201〜D-207) ----

async function procCall(name, args = {}) {
  const r = await call("offseason", { name, args });
  if (!r.ok) throw new Error(r.message);
  return r.value;
}

function scoutCell(sc) {
  return `${sc.overall_text}`;
}

export async function renderProcedure(token) {
  const r = await call("query", { name: "offseason_view", args: {} });
  if (!r.ok) throw new Error(r.message);
  if (token !== state.token) return;
  const v = r.value;
  const ps = state.proc;
  ps.phase = v.phase;
  ps.stage = v.stage;
  // 段階のタブは 5 つ(契約・FA・ドラフト・市場・完了)。今の段階の名前を大きく出す(D-271)
  $("proc-title").textContent = v.phase_label;
  const at = v.phases.findIndex((x) => x.key === v.stage);
  $("proc-steps").replaceChildren(...v.phases.map((p, i) => el("li", { className: i === at ? "current" : i < at ? "done" : "" }, p.label)));
  renderProcedureBar(v);
  renderAutoLog();
  if (ps.lastOffer) {
    const o = ps.lastOffer;
    $("proc-message").className = o.accepted ? "message ok" : "message ng";
    $("proc-message").textContent = o.accepted ? `${o.name} は受けました(${o.years} 年・${o.salary.toLocaleString()} 万円)。` : `${o.name} に断られました:${o.reason}。${o.released ? "提示の回数を使い切ったので、自由契約になりました。" : ""}`;
  }
  // 答え合わせモードがオンなら、真の総合値も出す(D-206)
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("procedure_answers", {});
    if (token !== state.token) return;
    if (state.answerLevel === 0) truth = null;
  }
  const body = $("proc-body");
  let parts = [];
  if (v.stage === "contract") parts = await contractSection(v, token);
  else if (v.phase === "fa") parts = await faSection(v, token);
  else if (v.phase === "draft") parts = poolSection(v, truth);
  else if (v.phase === "market") parts = await marketSection(v, truth, token);
  if (parts === null || token !== state.token) return;
  body.replaceChildren(...parts);
  renderProcedureContracts(v);
  $("proc-history").replaceChildren(historyTable(v.picks, v.released));
  $("proc-history-box").hidden = v.stage === "contract";
  $("proc-history-box").open = false;
}

// 状態バー(D-271):段階ごとの要約・予算・主ボタン。説明の文は置かない(D-270)
function renderProcedureBar(v) {
  const mine = v.my_team;
  const c = v.contracts;
  let info = "";
  let canNext = !state.running;
  if (v.stage === "contract" && v.renewal) {
    const rn = v.renewal;
    const n = rn.roster_counts;
    info = `${rn.players} 人:` + ["unoffered", "refused", "accepted", "declared", "released", "contracted"].filter((k) => n[k]).map((k) => `${rn.status_labels[k]} ${n[k]}`).join("・");
    canNext = canNext && rn.can_next;
  } else if (v.stage === "fa" && v.fa) {
    const f = v.fa;
    info = f.done ? `FA 終了:宣言 ${f.counts.declared}・契約 ${f.counts.signed}` : `ラウンド ${f.round}/${f.rounds}:宣言 ${f.counts.declared}・契約 ${f.counts.signed}・空き枠 ${f.space ?? "-"}`;
  } else if (v.stage === "draft" || v.stage === "market") {
    const turn = v.phase_finished ? "終了" : v.is_my_turn ? "あなたの番" : `${(v.order.find((o) => o.team_id === v.current_team) || {}).team_name || ""} の番`;
    info = `${Math.min(v.round, v.total_rounds)} / ${v.total_rounds} 巡・${turn}・${v.stage === "draft" ? "候補" : "市場"} ${v.stage === "draft" ? v.counts.candidates : v.counts.market} 人${mine ? `・${mine.players}/${mine.max} 人` : ""}`;
  }
  $("proc-info").textContent = info;
  const b = c && c.mine;
  const budget = $("proc-budget");
  if (b) {
    const over = b.hard && b.over_now > 0;
    budget.className = over ? "warn" : "";
    budget.textContent = b.cap ? `総年俸 ${b.total_text}(${b.hard ? "上限" : "目安"}の ${b.usage}%)${over ? `・上限を ${b.over_now.toLocaleString()} 万円超過` : ""}` : `総年俸 ${b.total_text}`;
  } else {
    budget.textContent = "";
  }
  $("proc-next").disabled = !canNext;
  $("proc-stage-auto").disabled = state.running;
  // 契約の段階は「自動案でまとめて更改」もバーに置く(D-272)
  const acts = $("proc-actions");
  const old = $("renew-auto");
  if (old) old.remove();
  if (v.stage === "contract" && v.renewal) {
    const rn = v.renewal;
    acts.prepend(el("button", { id: "renew-auto", type: "button", className: "secondary", title: "自動案でまとめて更改(未提示の全員に、1 年・算定した年俸で提示)", disabled: rn.unoffered === 0 || state.running, onclick: () => renewAuto() }, rn.unoffered ? `自動案で更改(${rn.unoffered})` : "自動案は提示済み"));
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
  if (c.renewals.length || c.budget_releases.length || c.negotiation_releases.length) {
    const mineFirst = (rows) => [...rows].sort((a, b) => (b.is_mine ? 1 : 0) - (a.is_mine ? 1 : 0));
    const renewCols = [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "old", label: "前の年俸" }, { key: "salary", label: "新しい年俸" }, { key: "change", label: "増減" }, { key: "years", label: "年数" }, { key: "offers", label: "提示", description: "受けるまでに提示した回数" }];
    const renewRows = mineFirst(c.renewals).map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, old: r.old_text || "-", salary: r.salary_text, change: r.change === null ? "-" : `${r.change >= 0 ? "+" : ""}${r.change.toLocaleString()}`, years: `${r.years} 年`, offers: r.offers ? `${r.offers} 回` : "-" } }));
    const det = el("details", { id: "renewal-results" }, el("summary", {}, `全球団の更改の結果(更改 ${c.renewals.length} 人${c.negotiation_releases.length ? `・交渉決裂 ${c.negotiation_releases.length} 人` : ""}${c.budget_releases.length ? `・予算超過 ${c.budget_releases.length} 人` : ""}・単価 ${c.rate_text})`));
    if (c.renewals.length) det.append(table({ firstLabel: "選手", columns: renewCols, rows: renewRows, first: (r) => [playerLink(r.name, r.player_id)], rowClass: (r) => (r.is_mine ? "mine" : ""), fluid: true, limit: state.proc.renewalsShown || 20, more: () => { state.proc.renewalsShown = (state.proc.renewalsShown || 20) + 50; renderCurrent(); } }));
    if (c.negotiation_releases.length) det.append(el("h3", {}, "交渉が決裂して自由契約"), table({ firstLabel: "選手", columns: [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "reason", label: "最後の理由" }, { key: "offers", label: "提示" }], rows: mineFirst(c.negotiation_releases).map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, reason: r.reason || "(提示せず)", offers: `${r.offers} 回` } })), first: (r) => [el("span", {}, r.name)], rowClass: (r) => (r.is_mine ? "mine" : ""), fluid: true, limit: 20 }));
    if (c.budget_releases.length) det.append(el("h3", {}, "予算超過で自由契約(AI 球団)"), table({ firstLabel: "選手", columns: [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "salary", label: "年俸" }], rows: c.budget_releases.map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, salary: r.salary_text } })), first: (r) => [el("span", {}, r.name)], fluid: true }));
    parts.push(det);
  }
  box.replaceChildren(...parts);
}

export function ceilingText(g) {
  return { S: "S(上位 5%)", A: "A", B: "B", C: "C", D: "D" }[g] || g;
}

// ---- 自由契約・市場の、成績つきの選手の一覧(D-222)。表の部品(table・sortToggle)と個人成績と同じ並び順の仕組みを使う ----

const POSITION_GROUPS = {
  batter: [["", "すべての野手"], ["C", "捕手"], ["IF", "内野手"], ["OF", "外野手"]],
  pitcher: [["", "すべての投手"], ["SP", "先発"], ["RP", "救援"]],
};
export const SCOUT_SORT_KEYS = ["overall", "ceiling"]; // 評価の列は計算本体の表にないので、画面側で並べる

export function inGroup(position, group) {
  if (!group) return true;
  if (group === "IF") return ["1B", "2B", "3B", "SS"].includes(position);
  if (group === "OF") return ["LF", "CF", "RF"].includes(position);
  return position === group;
}

// 成績つきの表を計算本体から受け取る。能力(答え合わせモード)のときだけ answer を呼ぶ
export async function fetchRosterTable(phase) {
  const rt = state.rosterTable;
  rt.phase = phase;
  if (rt.kinds[phase] === "ability" && state.answerLevel === 0) rt.kinds[phase] = "basic";
  const kind = rt.kinds[phase];
  const sortBy = rt.sortBy[rt.role];
  const abilityKeys = state.answerLevel > 0 ? (await answer("ability_columns", { role: rt.role })).map((c) => c.key) : [];
  // 能力の項目で並べていたときは、成績の表ではその表の既定に戻す(個人成績と同じ)。評価の列は画面側で並べる
  const usable = sortBy.key && !SCOUT_SORT_KEYS.includes(sortBy.key) && (kind === "ability" || !abilityKeys.includes(sortBy.key));
  const args = { phase, role: rt.role, kind, sort: usable ? sortBy.key : null, order: usable ? sortBy.order : null, season: rt.season };
  const data = kind === "ability" ? await answer("offseason_ability_table", { ...args, level: state.answerLevel }) : await query("offseason_table", args);
  rt.shownSort[rt.role] = { key: data.sort.key, order: data.order };
  return data;
}

// 切り替え(野手・投手 / 基本・セイバー・WAR・能力 / シーズン / ポジション)
export function rosterControls(data) {
  const rt = state.rosterTable;
  const seg = (id, items, value, onPick) => {
    const box = el("div", { className: "seg", id, role: "group" });
    for (const [k, label] of items) box.append(el("button", { type: "button", "aria-pressed": String(k === value), dataset: { value: k }, onclick: () => onPick(k) }, label));
    return box;
  };
  const kinds = [["basic", "基本"], ["saber", "セイバー"], ["war", "WAR"]];
  if (state.answerLevel > 0) kinds.push(["ability", "能力"]);
  const out = [
    seg("roster-role", [["batter", "野手"], ["pitcher", "投手"]], rt.role, (k) => { rt.role = k; rt.group = ""; renderCurrent(); }),
    seg("roster-kind", kinds, rt.kinds[rt.phase], (k) => { rt.kinds[rt.phase] = k; renderCurrent(); }),
  ];
  const filters = el("div", { className: "filters" });
  if (data.seasons.length > 1) {
    filters.append(el("select", { id: "roster-season", "aria-label": "シーズンの選択", onchange: (e) => { rt.season = e.target.value; renderCurrent(); } }, ...data.seasons.map((x) => el("option", { value: x.key, selected: x.key === rt.season }, x.label))));
  }
  filters.append(el("select", { id: "roster-group", "aria-label": "ポジションの絞り込み", onchange: (e) => { rt.group = e.target.value; renderCurrent(); } }, ...POSITION_GROUPS[rt.role].map(([k, label]) => el("option", { value: k, selected: k === rt.group }, label))));
  out.push(filters);
  return out;
}

export function rosterSortLine(data) {
  const extraNote = data.extra_column ? `並び順に使っている「${data.extra_column.label}」はこの表にない列なので、名前の隣に固定して出しています。` : "";
  return el("p", { className: "muted small", id: "roster-sort-line" }, `並び順:${data.sort.label}(${sortWord(data.sort, data.order)})。見出しを押すと並べ替え、もう一度押すと逆の順になります。${extraNote}`);
}

export function rosterSort(key) {
  const rt = state.rosterTable;
  sortToggle(rt.sortBy[rt.role], rt.shownSort[rt.role], key);
  renderCurrent();
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
  state.proc.lastOffer = null;
  state.proc.renewalOpen = null;
  await runProc("stage_auto", {}, (r) => { state.proc.autoLog = r.auto_log || null; });
}

// 「次の手続きへ」(D-271):自分の操作を終えて進む(AI の代行はしない)
export async function procNext() {
  const stage = state.proc.stage;
  const ask = { fa: "FA の残りのラウンドでは、自球団は追加の提示をしません(今のラウンドの提示は有効)。次へ進みますか?", draft: "ドラフトの自球団の残りの番は、すべてパスします。次へ進みますか?", market: "市場の自球団の残りの番は、すべてパスして、手続きを完了します。よろしいですか?" }[stage];
  if (ask && !confirm(ask)) return;
  state.proc.lastOffer = null;
  state.proc.autoLog = null;
  await runProc("next");
}
