// 選手の札(二画面の C 案。D-485、D-486):表の行を押すと、その画面の札の置き場(index.html の #pcard-<置き場>)に出す。
// 頭は 2 行(名前と調子 / 球団・守備位置・年齢・投打)、左に能力・右に特殊能力、その下にその画面の操作と「選手のページを開く」。
// プロの選手は本当の能力(計算本体の player_card)、ドラフト・外国人の候補はスカウトの見立て(行が持つ scouting)。

import { $, state } from "./core.js";
import { query } from "./backend.js";
import { openPage } from "./screens.js";
import { abilityPanel, conditionMark, el, estimateBlock } from "./parts.js";

const hosts = new Map(); // 置き場 → { token, rows: Map(選手 → { row, actions }) }。行は描き直しのたびに覚え直す
const pending = new Set();

function host(name) {
  if (!hosts.has(name)) hosts.set(name, { token: null, rows: new Map() });
  const h = hosts.get(name);
  if (h.token !== state.token) { // 画面を描き直したら、前の行は捨てる
    h.token = state.token;
    h.rows = new Map();
  }
  return h;
}

const picks = () => state.pick || (state.pick = {});
export const isPicked = (name, id) => picks()[name] === id;

// 表を作るときに、行とその行の操作を覚える(parts.js の table)
// 同じ選手が操作のない表(履歴など)にもいるときは、操作のある行を残す
export function registerRow(name, row, actions) {
  const rows = host(name).rows;
  const got = rows.get(row.player_id);
  if (got && got.actions && !actions) return;
  rows.set(row.player_id, { row, actions });
}

// 札を描き直す(同じ描き直しの中で何度呼ばれても 1 回)
export function scheduleCard(name) {
  if (pending.has(name)) return;
  pending.add(name);
  queueMicrotask(() => { pending.delete(name); renderCard(name); });
}

export function selectRow(name, id) {
  picks()[name] = id;
  markRows(name);
  renderCard(name);
}

export function clearPick(name) {
  picks()[name] = null;
  markRows(name);
  renderCard(name);
}

function markRows(name) {
  const id = picks()[name];
  for (const tr of document.querySelectorAll(`[data-pick="${name}"] tr.pickable`)) tr.classList.toggle("sel", tr.dataset.pid === id);
}

// 札の頭の 2 行目(D-486):球団 / 守備位置 / 年齢 / 投打 …。候補は出身
function infoLine(d) {
  return [d.team_name || d.origin || "候補", d.position_label, d.age !== undefined && d.age !== null ? `${d.age}歳` : "", d.hand || "", d.foreign ? "外国人" : "", d.development || ""].filter(Boolean).join(" / ");
}

export async function renderCard(name) {
  const box = $(`pcard-${name}`);
  if (!box) return;
  const id = picks()[name];
  if (!id) {
    box.classList.add("empty");
    box.replaceChildren(el("p", { className: "pcard-empty" }, "行を押すと、ここに選手の札が出ます"));
    return;
  }
  const got = host(name).rows.get(id);
  const row = got ? got.row : null;
  let data = null;
  if (!(row && row.scouting && !row.shown)) { // 候補(見立て)でなければ、計算本体から本当の能力を受け取る
    try {
      data = await query("player_card", { player_id: id });
    } catch {
      data = null;
    }
  }
  if (picks()[name] !== id) return; // 待っている間に別の選手を選んだ
  if (!data && !row) {
    box.classList.add("empty");
    box.replaceChildren(el("p", { className: "pcard-empty" }, "行を押すと、ここに選手の札が出ます"));
    return;
  }
  const d = data || { ...row, origin: row.values && row.values.origin, team_name: null };
  const head = el("div", { className: "pcard-head" },
    el("h3", { className: "pcard-name" }, el("span", { className: "pcard-name-text" }, d.name), data && data.condition ? conditionMark(data.condition) : ""),
    el("p", { className: "pcard-info" }, infoLine(d)));
  const body = data
    ? (data.abilities ? [abilityPanel(data.abilities, data.skills)] : [el("p", { className: "muted small" }, "引退した選手です。")])
    : estimateBlock(row.scouting);
  const act = got && got.actions ? got.actions(row) : null;
  const open = data ? el("button", { type: "button", className: "secondary pcard-open", onclick: () => openPage("player", { id }) }, "選手のページを開く") : null;
  box.classList.remove("empty");
  box.replaceChildren(el("div", { className: "pcard", dataset: { playerId: id } },
    el("button", { type: "button", className: "pcard-close", "aria-label": "選ぶのをやめる", title: "選ぶのをやめる", onclick: () => clearPick(name) }, "×"),
    head, ...body, act ? el("div", { className: "pcard-act" }, act) : null, open));
}
