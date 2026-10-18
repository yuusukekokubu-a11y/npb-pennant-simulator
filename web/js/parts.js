// 共通の部品(要素の作成・リンク・表・試合のカード・並べ替えの向き・項目と値の表)(web/app.js から分けた。保守②。D-291)

import { $, teamColor } from "./core.js";
import { openPage } from "./screens.js";
import { titleFor } from "./glossary.js";

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

export function link(text, onclick, className = "link") {
  return el("button", { className, type: "button", onclick }, text);
}

export const playerLink = (name, id) => link(name, () => openPage("player", { id }));
export const teamLink = (name, id) => link(name, () => openPage("team", { id }));

export function setPressed(groupId, value) {
  for (const b of $(groupId).children) b.setAttribute("aria-pressed", String(b.dataset.value === value));
}

// 表を作る。columns は { key, label }、rows の値は row.values[key]。見出しの title は用語集から引く(D-311)。
// first は左端(固定)の列の中身を作る関数。onSort があれば、見出しを押して並べ替えられる。
// extra は、名前の隣に固定して出す列(並び順の指標が表にないとき。D-131)
// fluid は、広い画面で横スクロールなしに全列を出す表(新しく作る表だけ。D-223)。detail(row) が要素を返せば、その行の下に 1 行足す
export function table({ firstLabel, columns, rows, first, sort, order, onSort, rowClass, limit, more, extra, fluid, detail }) {
  const headCell = (c, className) => {
    // ▲▼ は別の要素にして、見出しの文字の左に置く(列の右の余白はほかの列と同じ。数値が左に寄らない。D-403)
    const arrow = sort === c.key ? el("span", { className: "arrow" }, order === "desc" ? "▼" : "▲") : null;
    const text = arrow ? [arrow, c.label] : [c.label];
    const label = onSort ? el("button", { className: "sort", type: "button", onclick: () => onSort(c.key) }, ...text) : el("span", {}, ...text);
    return el("th", { scope: "col", className: `${className} ${sort === c.key ? "sorted" : ""}`.trim(), title: titleFor(c.label) }, label);
  };
  const nameClass = extra ? "sticky name-fixed" : "sticky";
  const head = el("tr", {}, el("th", { className: nameClass, scope: "col" }, firstLabel));
  if (extra) head.append(headCell(extra, "sticky2"));
  for (const c of columns) head.append(headCell(c, ""));
  const body = el("tbody");
  const shown = limit ? rows.slice(0, limit) : rows;
  for (const r of shown) {
    const tr = el("tr", { className: rowClass ? rowClass(r) : "" }, el("td", { className: `${nameClass} name-cell` }, ...first(r)));
    if (extra) tr.append(el("td", { className: sort === extra.key ? "sticky2 sorted" : "sticky2" }, r.values[extra.key] ?? ""));
    for (const c of columns) tr.append(el("td", sort === c.key ? { className: "sorted" } : {}, r.values[c.key] ?? ""));
    body.append(tr);
    const d = detail ? detail(r) : null;
    if (d) body.append(el("tr", { className: "detail" }, el("td", { colSpan: columns.length + 1 + (extra ? 1 : 0) }, d)));
  }
  const wrap = el("div", {}, el("div", { className: fluid ? "table-wrap fluid" : "table-wrap" }, el("table", {}, el("thead", {}, head), body)));
  if (limit && rows.length > limit && more) {
    wrap.append(el("button", { className: "secondary more", type: "button", onclick: more }, `もっと見る(あと ${rows.length - limit} 件)`));
  }
  return wrap;
}

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
    el("p", { className: "small muted" }, `球場:${a.park}の本拠地・${a.winner ? `${a.winner}の勝ち` : "引き分け"}`),
    el("p", { className: "small" }, `MVP:${mvp || "なし"}`),
    el("details", {}, el("summary", {}, "出た選手"), ...members),
  );
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

// ---- 選手のページ ----

export function kvTable(rows) {
  // rows: [{ label, values: [..] }]。見出しを押すと、用語集の解説を出す(D-311)
  const body = el("tbody");
  for (const r of rows) {
    const text = titleFor(r.label);
    const desc = el("span", { className: "desc", hidden: true }, text);
    const head = text ? el("th", { scope: "row" }, link(r.label, () => (desc.hidden = !desc.hidden)), desc) : el("th", { scope: "row" }, r.label);
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
  return [el("p", { className: "small muted" }, `スカウトの見立て(総合 ${sc.overall_text})。入団すると本当の数字が分かります。`), abilityPanel(sc.abilities, sc.skills)];
}

// 能力の一覧と、右側にまとめた特殊能力(D-374、D-400)。選手のページと、候補の見立て(D-418)で使う。skills が null なら「記録なし」
export function abilityPanel(items, skills, { skillsId = null } = {}) {
  const skillList = skills === null ? [el("span", { className: "muted small" }, "記録なし")] : skills.length ? skills.map(skillBadge) : [el("span", { className: "muted small" }, "なし")];
  return el("div", { className: "ability-panel" },
    el("div", { className: "ability-grid" }, ...items.map((a) => el("div", { className: `ability rank-${a.rank || "none"}`, "data-key": a.key }, el("span", { className: "ability-label" }, a.label), abilityValue(a)))),
    el("div", { className: "skills", ...(skillsId ? { id: skillsId } : {}) }, el("span", { className: "ability-label" }, "特殊能力"), ...skillList));
}
