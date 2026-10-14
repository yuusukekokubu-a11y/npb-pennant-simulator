// スタメンと打順の画面(D-373):打順の型を選ぶと AI がその型で組む。9 つの打順ごとに守備位置と選手を選んで「このスタメンと打順にする」で、
// 次の見直し(7 日ごと)まで毎試合その打順で戦う。見直しの日と、一軍にいない選手が入ったときは AI が組み直す

import { $, state } from "./core.js";
import { call } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, playerLink, skillBadge, table } from "./parts.js";
import { setDirty } from "./progress.js";

async function lineupCall(name, args = {}) {
  const r = await call("lineup", { name, args });
  if (!r.ok) throw new Error(r.message);
  return r.value;
}

export async function renderLineup(token) {
  const v = await lineupCall("view");
  if (token !== state.token) return;
  const ls = state.lineup || (state.lineup = { draft: null, message: null });
  if (!ls.draft) ls.draft = v.slots.map((s) => ({ player_id: s.player_id, position: s.position }));
  const players = [...v.slots, ...v.bench];
  const byId = Object.fromEntries(players.map((p) => [p.player_id, p]));
  $("lineup-style").replaceChildren(...v.styles.map((s) => el("option", { value: s.key, selected: s.key === v.style }, s.label)));
  $("lineup-style").disabled = state.running || !v.editable;
  $("lineup-style").onchange = (e) => run("style", { style: e.target.value }, `打順の型を「${e.target.selectedOptions[0].textContent}」にしました。AI が組み直しました。`);
  $("lineup-auto").disabled = state.running || !v.editable;
  $("lineup-auto").onclick = () => run("auto", {}, "AI が今の型で組み直しました。");
  $("lineup-save").disabled = state.running || !v.editable;
  $("lineup-save").onclick = () => run("set", { slots: ls.draft }, "このスタメンと打順で戦います。");
  $("lineup-source").textContent = v.source_text;
  const msg = $("lineup-message");
  if (ls.message) {
    msg.className = `message ${ls.message.ok ? "ok" : "ng"}`;
    msg.textContent = ls.message.text;
  } else {
    msg.className = v.notice ? "message" : "message";
    msg.textContent = v.notice || "";
  }

  // 打順の表:打順・守備位置(選ぶ)・選手(選ぶ)・本来のポジションと主な能力
  const pick = (id, label, items, value, onPick) => el("select", { id, "aria-label": label, disabled: state.running || !v.editable, onchange: (e) => onPick(e.target.value) }, ...items.map((x) => el("option", { value: x.key, selected: x.key === value }, x.label)));
  const posItems = v.positions;
  const optionText = (p) => `${p.name}(${p.native_label || p.position_label}・${p.overall})`;
  const playerItems = players.map((p) => ({ key: p.player_id, label: optionText(p) }));
  const rows = ls.draft.map((d, i) => {
    const p = byId[d.player_id] || {};
    const native = p.native_position || p.position;
    const nativeLabel = p.native_label || p.position_label || "";
    const off = d.position !== "DH" && native && native !== d.position;
    return {
      ...d,
      order: i + 1,
      values: {
        pos: pick(`lineup-pos-${i + 1}`, `${i + 1} 番の守備位置`, posItems, d.position, (k) => { ls.draft[i].position = k; ls.message = null; renderCurrent(); }),
        player: pick(`lineup-player-${i + 1}`, `${i + 1} 番の選手`, playerItems, d.player_id, (k) => { ls.draft[i].player_id = k; ls.message = null; renderCurrent(); }),
        native: el("span", { className: off ? "thin" : "" }, off ? `本来は${nativeLabel}` : nativeLabel),
        meet: (p.abilities || {}).meet || "",
        power: (p.abilities || {}).power || "",
        speed: (p.abilities || {}).speed || "",
        defense: (p.abilities || {}).defense || "",
        skills: el("span", { className: "skills-inline" }, ...(p.skills || []).map(skillBadge)),
      },
    };
  });
  const columns = [{ key: "pos", label: "守備位置" }, { key: "player", label: "選手" }, { key: "native", label: "ポジション" }, { key: "meet", label: "ミート" }, { key: "power", label: "パワー" }, { key: "speed", label: "走力" }, { key: "defense", label: "守備力" }, { key: "skills", label: "特殊能力" }];
  $("lineup-table").replaceChildren(table({ firstLabel: "打順", columns, rows, first: (r) => [el("span", {}, `${r.order}`)], fluid: true }));
  const inDraft = new Set(ls.draft.map((d) => d.player_id));
  const bench = players.filter((p) => !inDraft.has(p.player_id));
  $("lineup-bench").replaceChildren(bench.length ? table({ firstLabel: "選手", columns: [{ key: "pos", label: "ポジション" }, { key: "overall", label: "総合" }, { key: "meet", label: "ミート" }, { key: "power", label: "パワー" }, { key: "speed", label: "走力" }, { key: "defense", label: "守備力" }, { key: "skills", label: "特殊能力" }],
    rows: bench.map((p) => ({ ...p, values: { pos: p.native_label || p.position_label, overall: p.overall, meet: p.abilities.meet, power: p.abilities.power, speed: p.abilities.speed, defense: p.abilities.defense, skills: el("span", { className: "skills-inline" }, ...(p.skills || []).map(skillBadge)) } })), first: (p) => [playerLink(p.name, p.player_id), el("span", { className: "sub" }, p.hand || "")], fluid: true })
    : el("p", { className: "muted small" }, "控えはいません。"));
}

async function run(name, args, okText) {
  if (state.running) return;
  state.running = true;
  const ls = state.lineup || (state.lineup = {});
  try {
    const v = await lineupCall(name, args);
    ls.draft = v.slots.map((s) => ({ player_id: s.player_id, position: s.position }));
    ls.message = { ok: true, text: okText };
    setDirty(true);
  } catch (err) {
    ls.message = { ok: false, text: err.message };
  } finally {
    state.running = false;
  }
  renderCurrent();
}
