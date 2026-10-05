// 共通の部品(要素の作成・リンク・表・試合のカード・並べ替えの向き・項目と値の表)(web/app.js から分けた。保守②。D-291)

import { $ } from "./core.js";
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
    // ▲▼ は別の要素にして、列の右の余白に置く(文字の右端を数値の右端にそろえる。D-267)
    const arrow = sort === c.key ? el("span", { className: "arrow" }, order === "desc" ? " ▼" : " ▲") : null;
    const text = arrow ? [c.label, arrow] : [c.label];
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

// 試合の結果のカード(押すと試合のページへ)
export function gameCard(g) {
  const tags = g.tags.length ? el("span", { className: "tags" }, g.tags.join("・")) : null;
  return el(
    "button",
    { className: "game-card" + (g.is_mine ? " mine" : ""), type: "button", onclick: () => openPage("game", { game_no: g.game_no }), dataset: { gameNo: g.game_no } },
    el("span", {}, g.away.name),
    el("span", { className: "score" }, String(g.away.runs)),
    el("span", {}, g.home.name),
    el("span", { className: "score" }, String(g.home.runs)),
    tags,
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
