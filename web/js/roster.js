// 一軍登録の画面(D-425):一軍(先発・救援・野手)と二軍の選手を、調子・総合・成績・疲労と並べる。
// 一軍の選手の「外す」を押してから、二軍の同じ役割(投手・野手)の選手の「上げる」を押すと入れ替わる。
// 「AI に任せる」なら、7 日ごとの見直しで AI が選び直す。外した選手は 10 日戻せない
// 二軍の成績(D-446)と、けが(D-445。けが人は一軍に上げられない。一軍の選手がけがをしたら、自動で外れて代わりが上がる)
// 投手の起用(D-449〜D-451):先発の表の「番手」(▲▼)と救援の表の「役割」を変え、「この起用にする」で決める。「AI に戻す」まで使う

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

const COLUMNS = [{ key: "cond", label: "調子" }, { key: "overall", label: "総合" }, { key: "pos", label: "ポジション" }, { key: "stats", label: "成績" }, { key: "farm", label: "二軍成績" }, { key: "fatigue", label: "疲労" }, { key: "action", label: "入れ替え" }];
const STARTER_COLUMNS = [{ key: "turn", label: "番手" }, ...COLUMNS];
const RELIEF_COLUMNS = [{ key: "role", label: "役割" }, ...COLUMNS];
const ROLE_ORDER = ["closer", "setup", "middle", "mopup"];

// 変えている途中の起用(「この起用にする」を押すまで画面だけ)。一軍の投手が変わったら捨てる
function pitchDraft(rs, v) {
  const ids = (xs) => xs.map((r) => r.player_id).sort().join();
  const d = rs.pitch;
  if (d && ids(v.starters) === [...d.starters].sort().join() && ids(v.relievers) === Object.keys(d.roles).sort().join()) return d;
  rs.pitch = null;
  return { starters: v.starters.map((r) => r.player_id), roles: Object.fromEntries(v.relievers.map((r) => [r.player_id, r.role_label])), changed: false };
}

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

  const pv = v.pitching;
  const draft = pitchDraft(rs, v);
  const edit = (fn) => { const d = rs.pitch || (rs.pitch = { starters: [...draft.starters], roles: { ...draft.roles } }); fn(d); d.changed = true; rs.message = null; renderCurrent(); };
  const move = (i, k) => edit((d) => { [d.starters[i], d.starters[i + k]] = [d.starters[i + k], d.starters[i]]; });
  const turnCell = (r) => {
    const i = draft.starters.indexOf(r.player_id);
    return el("span", { className: "turn" }, `${i + 1}${i === pv.next_index ? "(次)" : ""}`,
      el("button", { type: "button", className: "secondary small-button", disabled: state.running || i === 0, "aria-label": "番手を上げる", dataset: { up: r.player_id }, onclick: () => move(i, -1) }, "▲"),
      el("button", { type: "button", className: "secondary small-button", disabled: state.running || i === draft.starters.length - 1, "aria-label": "番手を下げる", dataset: { down: r.player_id }, onclick: () => move(i, 1) }, "▼"));
  };
  const roleCell = (r) => {
    const sel = el("select", { className: "pitch-role", disabled: state.running, "aria-label": `${r.name} の役割`, dataset: { playerId: r.player_id }, onchange: (e) => edit((d) => { d.roles[r.player_id] = e.target.value; }) },
      ...pv.roles.map((o) => el("option", { value: o.key }, o.label)));
    sel.value = draft.roles[r.player_id];
    return sel;
  };
  const values = (r, action) => ({
    turn: r.slot === "starter" ? turnCell(r) : "",
    role: r.slot === "reliever" ? roleCell(r) : "",
    cond: conditionMark(r.condition),
    overall: r.overall_text,
    pos: r.position_label + (r.foreign ? "・外" : ""),
    stats: r.stats,
    farm: r.farm_stats,
    fatigue: r.fatigue === null ? "—" : String(r.fatigue),
    action,
  });
  const outButton = (r) => el("button", { type: "button", className: rs.out === r.player_id ? "" : "secondary", disabled: state.running, dataset: { playerId: r.player_id }, onclick: () => { rs.out = rs.out === r.player_id ? null : r.player_id; rs.message = null; renderCurrent(); } }, rs.out === r.player_id ? "選択中" : "外す");
  const inButton = (r) => {
    if (r.injury) return el("span", { className: "injury small" }, r.injury.text); // けが人は上げられない(D-445)
    if (r.lock_days > 0) return el("span", { className: "muted small" }, `あと ${r.lock_days} 日`);
    const ok = out && out.role === r.role;
    return el("button", { type: "button", className: "secondary", disabled: state.running || !ok, dataset: { playerId: r.player_id }, onclick: () => run("swap", { out_id: out.player_id, in_id: r.player_id }, `${out.name} を二軍に下げ、${r.name} を一軍に上げました。`) }, "上げる");
  };
  const block = (id, title, rows, action, columns = COLUMNS) => el("section", { id },
    el("h2", {}, `${title}(${rows.length} 人)`),
    rows.length ? table({ firstLabel: "選手", columns, rows: rows.map((r) => ({ ...r, values: values(r, action(r)) })), first: (r) => [playerLink(r.name, r.player_id), el("span", { className: "sub" }, `${r.age}歳${r.hand ? ` ${r.hand}` : ""}`)], rowClass: (r) => (rs.out === r.player_id ? "selected" : r.lock_days > 0 || r.injury ? "muted" : ""), fluid: true })
      : el("p", { className: "muted small" }, "いません。"));
  const starters = draft.starters.map((id) => v.starters.find((r) => r.player_id === id));
  const rank = (r) => ROLE_ORDER.indexOf(r.role_label); // 救援は決まっている役割の順に並べる(選んでいる間は並べ替えない)
  const relievers = [...v.relievers].sort((a, b) => rank(a) - rank(b));
  const closers = Object.values(draft.roles).filter((x) => x === "closer").length;
  const pitching = el("section", { id: "roster-pitching" },
    el("h2", {}, "投手の起用"),
    el("p", { className: "small", id: "roster-pitching-source" }, `${pv.source_text}${draft.changed ? "(変えた内容は「この起用にする」を押すまで使いません)" : ""}`),
    pv.notice ? el("p", { className: "small notice", id: "roster-pitching-notice" }, pv.notice) : "",
    el("div", { className: "row" },
      el("button", { type: "button", id: "roster-pitching-save", disabled: state.running || !draft.changed, onclick: () => {
        if (closers !== 1) { rs.message = { ok: false, text: `抑えは 1 人にしてください(今は ${closers} 人)。` }; renderCurrent(); return; }
        run("pitching", { starters: draft.starters, roles: draft.roles }, "この起用で戦います(「AI に戻す」まで使います)。", true);
      } }, "この起用にする"),
      el("button", { type: "button", id: "roster-pitching-auto", className: "secondary", disabled: state.running || (pv.source === "ai" && !draft.changed), onclick: () => {
        if (pv.source === "ai") { rs.pitch = null; rs.message = { ok: true, text: "変えた内容を取り消しました(AI の起用のままです)。" }; renderCurrent(); return; }
        run("pitching_auto", {}, "投手の起用を AI に戻しました。", true);
      } }, "AI に戻す")));
  $("roster-body").replaceChildren(
    pitching,
    block("roster-starters", "一軍:先発", starters, outButton, STARTER_COLUMNS),
    block("roster-relievers", "一軍:救援", relievers, outButton, RELIEF_COLUMNS),
    block("roster-batters", "一軍:野手", v.batters, outButton),
    block("roster-farm", "二軍", v.farm, inButton),
  );
}

async function run(name, args, okText, pitching = false) {
  if (state.running) return;
  state.running = true;
  const rs = state.roster || (state.roster = {});
  try {
    await rosterCall(name, args);
    rs.message = { ok: true, text: okText };
    rs.out = null;
    if (pitching) rs.pitch = null;
    setDirty(true);
  } catch (err) {
    rs.message = { ok: false, text: err.message };
  } finally {
    state.running = false;
  }
  renderCurrent();
}
