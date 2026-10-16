// 一軍登録の画面(D-425):一軍(先発・救援・野手)と二軍の選手を、調子・総合・成績・疲労と並べる。
// 一軍の選手の「外す」を押してから、二軍の同じ役割(投手・野手)の選手の「上げる」を押すと入れ替わる。
// 「AI に任せる」なら、7 日ごとの見直しで AI が選び直す。外した選手は 10 日戻せない

import { $, state } from "./core.js";
import { call } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { conditionMark, el, playerLink, table } from "./parts.js";
import { setDirty } from "./progress.js";

async function rosterCall(name, args = {}) {
  const r = await call("roster", { name, args });
  if (!r.ok) throw new Error(r.message);
  return r.value;
}

const COLUMNS = [{ key: "cond", label: "調子" }, { key: "overall", label: "総合" }, { key: "pos", label: "ポジション" }, { key: "stats", label: "成績" }, { key: "fatigue", label: "疲労" }, { key: "action", label: "入れ替え" }];
const ROLE_LABELS = { closer: "抑え", setup: "勝ち継投", middle: "中継ぎ", mopup: "敗戦処理" };

export async function renderRoster(token) {
  const v = await rosterCall("view");
  if (token !== state.token) return;
  const rs = state.roster || (state.roster = { out: null, message: null });
  const out = rs.out ? [...v.starters, ...v.relievers, ...v.batters].find((r) => r.player_id === rs.out) : null;
  if (!out) rs.out = null;
  $("roster-auto").checked = v.auto;
  $("roster-auto").disabled = state.running;
  $("roster-auto").onchange = (e) => run("auto", { auto: e.target.checked }, e.target.checked ? "AI に任せます(7 日ごとに見直します)。" : "AI に任せるのをやめました。一軍は手で入れ替えるまで変わりません。");
  const c = v.counts;
  $("roster-info").textContent = `一軍 ${c.active} 人・外国人 ${c.foreign}/${c.foreign_max ?? "-"}・捕手 ${c.catchers}(${c.catchers_min} 人以上)・次の見直し ${v.next_review}日目${v.auto ? "" : "(AI に任せていないので変わりません)"}`;
  $("roster-recent").textContent = v.recent ? `${v.recent.day}日目の入れ替え:下げた ${v.recent.out.join("・") || "なし"} / 上げた ${v.recent.in.join("・") || "なし"}` : "";
  const msg = $("roster-message");
  msg.className = rs.message ? `message ${rs.message.ok ? "ok" : "ng"}` : "message";
  msg.textContent = rs.message ? rs.message.text : out ? `${out.name} と入れ替える二軍の選手(${out.role === "pitcher" ? "投手" : "野手"})の「上げる」を押してください。` : "";

  const values = (r, action) => ({
    cond: conditionMark(r.condition),
    overall: r.overall_text,
    pos: r.role_label ? `${r.position_label}(${ROLE_LABELS[r.role_label] || ""})` : r.position_label + (r.foreign ? "・外" : ""),
    stats: r.stats,
    fatigue: r.fatigue === null ? "—" : String(r.fatigue),
    action,
  });
  const outButton = (r) => el("button", { type: "button", className: rs.out === r.player_id ? "" : "secondary", disabled: state.running, dataset: { playerId: r.player_id }, onclick: () => { rs.out = rs.out === r.player_id ? null : r.player_id; rs.message = null; renderCurrent(); } }, rs.out === r.player_id ? "選択中" : "外す");
  const inButton = (r) => {
    if (r.lock_days > 0) return el("span", { className: "muted small" }, `あと ${r.lock_days} 日`);
    const ok = out && out.role === r.role;
    return el("button", { type: "button", className: "secondary", disabled: state.running || !ok, dataset: { playerId: r.player_id }, onclick: () => run("swap", { out_id: out.player_id, in_id: r.player_id }, `${out.name} を二軍に下げ、${r.name} を一軍に上げました。`) }, "上げる");
  };
  const block = (id, title, rows, action) => el("section", { id },
    el("h2", {}, `${title}(${rows.length} 人)`),
    rows.length ? table({ firstLabel: "選手", columns: COLUMNS, rows: rows.map((r) => ({ ...r, values: values(r, action(r)) })), first: (r) => [playerLink(r.name, r.player_id), el("span", { className: "sub" }, `${r.age}歳${r.hand ? ` ${r.hand}` : ""}`)], rowClass: (r) => (rs.out === r.player_id ? "selected" : r.lock_days > 0 ? "muted" : ""), fluid: true })
      : el("p", { className: "muted small" }, "いません。"));
  $("roster-body").replaceChildren(
    block("roster-starters", "一軍:先発", v.starters, outButton),
    block("roster-relievers", "一軍:救援", v.relievers, outButton),
    block("roster-batters", "一軍:野手", v.batters, outButton),
    block("roster-farm", "二軍", v.farm, inButton),
  );
}

async function run(name, args, okText) {
  if (state.running) return;
  state.running = true;
  const rs = state.roster || (state.roster = {});
  try {
    await rosterCall(name, args);
    rs.message = { ok: true, text: okText };
    rs.out = null;
    setDirty(true);
  } catch (err) {
    rs.message = { ok: false, text: err.message };
  } finally {
    state.running = false;
  }
  renderCurrent();
}
