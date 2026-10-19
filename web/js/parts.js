// 共通の部品(要素の作成・名札・表・操作の帯・試合のカード・並べ替えの向き・項目と値の表)(web/app.js から分けた。保守②。D-291)
// 表の仕組みは 1 つ(デザインの見直し。D-481〜D-488):選手の行は押すと選手の札(card.js)、表示を切り替えても形が動かない表、表の上の操作の帯

import { $, state, teamColor } from "./core.js";
import { openPage } from "./screens.js";
import { titleFor } from "./glossary.js";
import { isPicked, registerRow, scheduleCard, selectRow } from "./card.js";

// ---- 部品 ----

export function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k in e) e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

// 名札(下線のない、押せる名前。D-484):表の外の選手・チーム・球場の名前に使う。表の選手の行は、行全体を押す(nameCell)
export function link(text, onclick, className = "link") {
  return el("button", { className, type: "button", onclick }, text);
}

export const playerChip = (name, id) => link(name, () => openPage("player", { id }));
export const teamLink = (name, id) => link(name, () => openPage("team", { id }));

// 表の名前の列(D-484、D-486):1 行目に名前(リンクの見た目にしない)、2 行目に情報。どちらも途中で折り返さない
export function nameCell(name, sub = "", ...marks) {
  return [el("span", { className: "pname" }, name, ...marks.filter(Boolean)), sub ? el("span", { className: "sub" }, sub) : null];
}

export function setPressed(groupId, value) {
  for (const b of $(groupId).children) b.setAttribute("aria-pressed", String(b.dataset.value === value));
}

// 表の中の能力の値(「C 63」の形)は、ランクの色札と数字にする(D-488)。合わせた文字は計算本体の文字と同じ
const RANKED = /^[SABCDEFG] \d{1,3}$/;
function cellContent(v) {
  if (typeof v === "string" && RANKED.test(v)) return [el("span", { className: `rank-badge small rank-${v[0]}` }, v[0]), " ", el("span", { className: "rank-num" }, v.slice(2))];
  return [v ?? ""];
}

// 横のスクロールの位置(描き直しても同じ位置に戻す。D-483)
const scrollMemo = new Map();
const FOCUSABLE = "button, select, input, label, a, textarea, summary";

// 表を作る(D-481)。columns は { key, label }、rows の値は row.values[key]。見出しの title は用語集から引く(D-311)。
// first は左端(固定)の列の中身を作る関数(選手は nameCell)。onSort があれば、見出しを押して並べ替えられる。
// extra は、名前のすぐ右に固定して出す列(並べた列。D-483)。fluid は、広い画面で横スクロールなしに全列を出す表(D-223)。
// pick は選手の札の置き場の名前(行を押すと札が開く。D-484)。actions(row) は札に出すその行の操作。onRow は選手でない行を押したとき。
// slots は列の枠の数:列の幅を決めた表にし、足りない分は空の列で埋める(表示を切り替えても形が動かない。D-483)。colW はその列の幅
export function table({ firstLabel, columns, rows, first, sort, order, onSort, rowClass, limit, more, extra, fluid, detail, pick, actions, onRow, slots, colW, keep }) {
  const fixed = Boolean(slots);
  let cols = columns;
  if (fixed) {
    const room = slots - (extra ? 1 : 0) - cols.length;
    if (room > 0) cols = [...cols, ...Array.from({ length: room }, (_, i) => ({ key: `__pad${i}`, label: "", pad: true }))];
  }
  const headCell = (c, className) => {
    if (c.pad) return el("th", { className: "pad", "aria-hidden": "true" });
    // ▲▼ は別の要素にして、見出しの文字の左に置く(列の右の余白はほかの列と同じ。数値が左に寄らない。D-403)
    const arrow = sort === c.key ? el("span", { className: "arrow" }, order === "desc" ? "▼" : "▲") : null;
    const text = arrow ? [arrow, c.label] : [c.label];
    const label = onSort ? el("button", { className: "sort", type: "button", onclick: () => onSort(c.key) }, ...text) : el("span", {}, ...text);
    return el("th", { scope: "col", className: `${className} ${sort === c.key ? "sorted" : ""}`.trim(), title: titleFor(c.label) }, label);
  };
  const nameClass = extra ? "sticky name-fixed" : "sticky";
  const head = el("tr", {}, el("th", { className: nameClass, scope: "col" }, firstLabel));
  if (extra) head.append(headCell(extra, "sticky2"));
  for (const c of cols) head.append(headCell(c, ""));
  const body = el("tbody");
  const shown = limit ? rows.slice(0, limit) : rows;
  const valueCell = (r, c, className = "") => {
    if (c.pad) return el("td", { className: "pad" });
    const v = r.values[c.key];
    const td = el("td", { className: `${className} ${sort === c.key ? "sorted" : ""}`.trim() }, ...cellContent(v));
    if (fixed && typeof v === "string" && v.length > 6) td.title = v; // 幅を決めた列で切れる文字は、指を重ねると全文
    return td;
  };
  for (const r of shown) {
    const tr = el("tr", { className: rowClass ? rowClass(r) : "" }, el("td", { className: `${nameClass} name-cell` }, ...first(r)));
    if (extra) tr.append(valueCell(r, extra, "sticky2"));
    for (const c of cols) tr.append(valueCell(r, c));
    if (pick && r.player_id) {
      registerRow(pick, r, actions);
      clickableRow(tr, () => selectRow(pick, r.player_id), isPicked(pick, r.player_id));
      tr.dataset.pid = r.player_id;
    } else if (onRow) {
      clickableRow(tr, () => onRow(r));
    }
    body.append(tr);
    const d = detail ? detail(r) : null;
    if (d) body.append(el("tr", { className: "detail" }, el("td", { colSpan: cols.length + 1 + (extra ? 1 : 0) }, d)));
  }
  const tbl = el("table", { className: fixed ? "fixed" : "" });
  if (fixed) {
    const w = colW || "var(--col-w)";
    const n = cols.length + (extra ? 1 : 0);
    tbl.prepend(el("colgroup", {}, el("col", { className: "c-name" }), ...Array.from({ length: n }, () => el("col", { style: `width: ${w}` }))));
    tbl.style.width = `calc(var(--table-name-w) + ${n} * ${w})`;
  }
  tbl.append(el("thead", {}, head), body);
  const scroller = el("div", { className: fluid && !fixed ? "table-wrap fluid" : "table-wrap", dataset: pick ? { pick } : {} }, tbl);
  if (keep) {
    scroller.addEventListener("scroll", () => scrollMemo.set(keep, scroller.scrollLeft), { passive: true });
    requestAnimationFrame(() => { if (scrollMemo.has(keep)) scroller.scrollLeft = scrollMemo.get(keep); });
  }
  const wrap = el("div", {}, scroller);
  if (limit && rows.length > limit && more) {
    wrap.append(el("button", { className: "secondary more", type: "button", onclick: more }, `もっと見る(あと ${rows.length - limit} 件)`));
  }
  if (pick) scheduleCard(pick); // 選んだ選手がいなくなったときも、札を描き直す
  return wrap;
}

// 行全体を押せる形にする(キーボードでも Enter・スペース。D-484)。行の中のボタン・選ぶ欄を押したときは何もしない
export function clickableRow(tr, onPick, selected = false) {
  tr.classList.add("pickable");
  if (selected) tr.classList.add("sel");
  tr.tabIndex = 0;
  tr.addEventListener("click", (e) => { if (!e.target.closest(FOCUSABLE)) onPick(); });
  tr.addEventListener("keydown", (e) => {
    if (e.target !== tr || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    onPick();
  });
}

// ---- 操作の帯(D-482):表示の切り替え → 右寄せで「絞り込み」→「並び」。注意書きは帯の中の小さな印 ----

// views は [{ key, label }]、viewsId はその切り替えの要素の id。filters・sortControls は小さなボタンを押すと開く中身(要素の並び)
export function band({ id, views = [], view = null, onView = null, viewsId = null, filters = [], filterText = "", sortText = "", sortControls = [], note = "" }) {
  const box = el("div", { className: "tband", id: `${id}-band` });
  if (views.length) {
    box.append(el("div", { className: "seg tseg", role: "group", "aria-label": "表示の切り替え", id: viewsId || `${id}-views` },
      ...views.map((v) => el("button", { type: "button", dataset: { value: v.key }, "aria-pressed": String(v.key === view), onclick: () => onView(v.key) }, v.label))));
  }
  // 右寄せのまとまり(狭い画面で 2 行目に回っても右寄せ)
  const right = el("div", { className: "tband-right" });
  if (note) right.append(el("span", { className: "tnote", id: `${id}-note`, title: note, "aria-label": note, tabIndex: 0, onclick: (e) => e.currentTarget.classList.toggle("open") }, noteMark(note), el("span", { className: "tnote-full" }, note)));
  if (filters.length) right.append(chip(`${id}-filter`, "絞り込み", filterText, filters));
  if (sortControls.length) right.append(chip(`${id}-sort-chip`, "並び", sortText, sortControls)); // 中の指標の選ぶ欄が `${id}-sort`
  box.append(right);
  return box;
}

// 注意書きの印の短い文字(全文は title と、押すと出る吹き出し)
function noteMark(text) {
  if (text.includes("途中")) return "途中の値";
  if (text.includes("参考値")) return "参考値";
  return "注意";
}

// 帯の小さなボタン(押すと下に開く)。開いているかは画面の状態に覚え、描き直しても閉じない
function chip(id, label, text, controls) {
  if (!state.bands) state.bands = {};
  const d = el("details", { className: "chip", id, open: Boolean(state.bands[id]), ontoggle: (e) => { state.bands[id] = e.target.open; } },
    el("summary", {}, el("span", { className: "chip-label" }, label), text ? el("span", { className: "chip-text" }, text) : null),
    el("div", { className: "chip-panel" }, ...controls));
  return d;
}

// 帯の外を押したら、開いている小さなボタンを閉じる
document.addEventListener("pointerdown", (e) => {
  for (const d of document.querySelectorAll("details.chip[open]")) {
    if (d.contains(e.target)) continue;
    d.open = false;
    if (state.bands) state.bands[d.id] = false; // 描き直しの前に閉じたことを覚える
  }
});

// 帯の中の選ぶ欄(ラベルつき)
export function bandSelect(id, label, items, value, onPick) {
  const s = el("select", { id, "aria-label": label, onchange: (e) => onPick(e.target.value) }, ...items.map((x) => el("option", { value: x.key, selected: x.key === value }, x.label)));
  return el("label", { className: "chip-field" }, el("span", {}, label), s);
}

// 帯の「並び」の中身:指標の選ぶ欄と、高い順・低い順。groups があれば区分つき
export function sortControls(id, { options, groups = null, key, order, counts = false, onKey, onOrder }) {
  const opt = (c) => el("option", { value: c.key, selected: c.key === key }, c.label);
  const sel = el("select", { id: `${id}-sort`, "aria-label": "並び順に使う指標", onchange: (e) => onKey(e.target.value) },
    ...(groups ? groups.filter((g) => g.items.length).map((g) => el("optgroup", { label: g.label }, ...g.items.map(opt))) : options.map(opt)));
  sel.value = key;
  const seg = el("div", { className: "seg small-seg", id: `${id}-order`, role: "group", "aria-label": "並び順の向き" },
    ...[["desc", counts ? "多い順" : "高い順"], ["asc", counts ? "少ない順" : "低い順"]].map(([v, label]) => el("button", { type: "button", dataset: { value: v }, "aria-pressed": String(v === order), onclick: () => onOrder(v) }, label)));
  return [el("label", { className: "chip-field" }, el("span", {}, "指標"), sel), seg];
}

// 並べた列の帯の文字(「打率▼」)
export const sortLabel = (label, order) => `${label}${order === "desc" ? "▼" : "▲"}`;

// 球団のマーク(D-400):丸の中に頭文字(文字は CSS で出すので、表や行の文字には入らない)。色は球団の番号ごと
export function emblem(teamId, name, size = "") {
  return el("span", { className: `emblem ${size}`.trim(), "data-initial": String(name || "").slice(0, 1), style: `--team: ${teamColor(teamId)}`, "aria-hidden": "true" });
}

// 順位の数字。1〜3 位はメダル(D-400)
export function rankBadge(rank) {
  return el("span", { className: rank >= 1 && rank <= 3 ? `rank medal medal-${rank}` : "rank" }, String(rank));
}

// 試合の結果のカード(小さな得点板。押すと試合のページへ)
export function gameCard(g) {
  const tags = g.tags.length ? el("span", { className: "tags" }, g.tags.join("・")) : null;
  const line = (t, lose) => [emblem(t.team_id, t.name, "small"), el("span", { className: "gc-name" + (lose ? " lose" : "") }, t.name), el("span", { className: "score" + (lose ? " lose" : "") }, String(t.runs))];
  return el(
    "button",
    { className: "game-card" + (g.is_mine ? " mine" : ""), type: "button", onclick: () => openPage("game", { game_no: g.game_no }), dataset: { gameNo: g.game_no } },
    ...line(g.away, g.away.runs < g.home.runs),
    ...line(g.home, g.home.runs < g.away.runs),
    tags,
  );
}

// オールスターの結果のカード(D-436):得点・球場・MVP。出た選手は開いて見る
export function allstarCard(a) {
  const score = `${a.away.league_name} ${a.away.runs} - ${a.home.runs} ${a.home.league_name}`;
  const mvp = a.mvp.map((m) => `${m.name}(${m.team}) ${m.H}安打 ${m.HR}本塁打 ${m.RBI}打点`).join("、");
  const members = Object.entries(a.players).map(([lg, xs]) =>
    el("div", {}, el("h4", {}, lg), el("p", { className: "small" }, xs.map((x) => `${x.name}(${x.team})`).join("、"))),
  );
  return el(
    "div",
    { className: "card allstar" },
    el("p", {}, el("b", {}, `オールスター 第${a.number}戦(${a.day}日目)`), ` ${score}`),
    el("p", { className: "small muted" }, `球場:${a.stadium_of}の本拠地・${a.winner ? `${a.winner}の勝ち` : "引き分け"}`),
    el("p", { className: "small" }, `MVP:${mvp || "なし"}`),
    el("details", {}, el("summary", {}, "出た選手"), ...members),
  );
}

// ポストシーズンのシリーズのカード(D-440):両球団の勝ち数(アドバンテージを含む)・勝ち抜け・MVP。試合のスコアは試合のページへ
export function seriesCard(s, withGames = true) {
  const team = (t) => el("span", { className: "series-team" + (t.winner ? " win" : "") + (t.is_mine ? " mine" : "") }, `${t.seed ? `${t.seed}位 ` : ""}${t.name} ${t.wins}`);
  const head = el("p", {}, el("b", {}, s.label), " ", team(s.teams[0]), " - ", team(s.teams[1]), s.ties ? `(${s.ties}分)` : "");
  const note = [s.advantage ? `${s.teams[0].name}に${s.advantage}勝のアドバンテージ` : "", s.winner ? `${s.winner}の勝ち抜け` : "", s.mvp ? `MVP:${s.mvp.name}(${s.mvp.team}${s.mvp.text ? ` ${s.mvp.text}` : ""})` : ""].filter(Boolean).join("・");
  const games = withGames && s.games && s.games.length
    ? el("p", { className: "small" }, ...s.games.flatMap((g, i) => [i ? " / " : "", link(`第${i + 1}戦 ${g.away} ${g.score} ${g.home}`, () => openPage("game", { game_no: g.game_no }))]))
    : null;
  return el("div", { className: "card series" }, head, note ? el("p", { className: "small muted" }, note) : null, games);
}

// 見出しを押したときの並び順の決め方(表の部品で共通。D-131):今の並びと同じ列なら逆順に、違う列ならその指標の「よい」向き(能力の項目は高い順)から
export function sortToggle(sortBy, shown, key) {
  if (shown && shown.key === key) {
    sortBy.key = key;
    sortBy.order = shown.order === "desc" ? "asc" : "desc";
  } else {
    sortBy.key = key;
    sortBy.order = null;
  }
}

// 並べた列を名前のすぐ右に固定する形に分ける(D-483):並べた列(表になければ計算本体の extra_column)と、残りの列
export function pinSorted(columns, sortCol, extra = null) {
  const pinned = extra || columns.find((c) => c.key === sortCol.key) || sortCol;
  return { pinned, rest: columns.filter((c) => c.key !== pinned.key) };
}

// ---- 選手のページ ----

export function kvTable(rows) {
  // rows: [{ label, values: [..] }]。見出しを押すと、用語集の解説を出す(D-311)
  const body = el("tbody");
  for (const r of rows) {
    const text = titleFor(r.label);
    const desc = el("span", { className: "desc", hidden: true }, text);
    const head = text ? el("th", { scope: "row" }, link(r.label, () => (desc.hidden = !desc.hidden), "term"), desc) : el("th", { scope: "row" }, r.label);
    body.append(el("tr", {}, head, ...r.values.map((v) => el("td", {}, v))));
  }
  return el("div", { className: "table-wrap" }, el("table", { className: "kv" }, body));
}

// 調子の印(D-424):↑ 絶好調・↗ 好調・→ 普通・↘ 不調・↓ 絶不調。title に名前
export function conditionMark(c) {
  if (!c) return "";
  return el("span", { className: `cond cond-${c.level}`, title: c.label, "data-level": String(c.level) }, `${c.mark} ${c.label}`);
}

// 特殊能力の札(D-374):良いものは緑、悪いものは灰色
export function skillBadge(s) {
  return el("span", { className: `skill ${s.good ? "good" : "bad"}`, "data-key": s.key }, s.label);
}

// 能力の札(作り直し①。D-347、D-400):ランクのある能力は色つきの札と数字。札と数字を合わせた文字は計算本体の text と同じ
function abilityValue(a) {
  if (a.rank && a.text.startsWith(a.rank)) return el("strong", { className: "ability-value" }, el("span", { className: `rank-badge rank-${a.rank}` }, a.rank), el("span", { className: "ability-num" }, a.text.slice(a.rank.length)));
  return el("strong", { className: "ability-value" }, el("span", { className: "ability-num" }, a.text));
}

// 候補の見立て(D-418):能力の一覧と特殊能力に、見立てであることの 1 行を添える
export function estimateBlock(sc) {
  if (!sc) return [];
  return [el("p", { className: "small muted estimate-note" }, `スカウトの見立て(総合 ${sc.overall_text})。入団すると本当の数字が分かります。`), abilityPanel(sc.abilities, sc.skills)];
}

// 能力と特殊能力の 2 列(D-374、D-400、D-486):左に能力を縦に、右に特殊能力を縦に。左右とも同じ高さの見出しで、1 行目の上端と行の高さをそろえる。
// 選手のページ・選手の札・候補の見立て(D-418)で使う。skills が null なら「記録なし」
export function abilityPanel(items, skills, { skillsId = null } = {}) {
  const skillList = skills === null ? [el("span", { className: "skill none" }, "記録なし")] : skills.length ? skills.map(skillBadge) : [el("span", { className: "skill none" }, "なし")];
  return el("div", { className: "ability-panel" },
    el("div", { className: "ability-grid" }, el("div", { className: "col-head" }, "能力"), ...items.map((a) => el("div", { className: `ability rank-${a.rank || "none"}`, "data-key": a.key }, el("span", { className: "ability-label" }, a.label), abilityValue(a)))),
    el("div", { className: "skills", ...(skillsId ? { id: skillsId } : {}) }, el("div", { className: "col-head" }, "特殊能力"), ...skillList));
}
