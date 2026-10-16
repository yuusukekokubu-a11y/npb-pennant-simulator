// 記録室(D-422、D-423):下のタブの「記録」。歴代の順位と優勝・年ごとのタイトル・通算記録・補強の記録(ドラフト・外国人・出入り)。
// 計算本体は読むだけ。旧「ドラフトの振り返り」(D-216)は、補強の記録のドラフト・外国人に置き換えた

import { $, state } from "./core.js";
import { query } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, playerLink, setPressed, table, teamLink } from "./parts.js";

const SECTIONS = [["standings", "順位と優勝"], ["titles", "タイトル"], ["leaders", "通算記録"], ["entries", "補強"]];
const ENTRY_KINDS = [["draft", "ドラフト"], ["foreign", "外国人"], ["moves", "出入り"]];

// 記録室を、補強の記録のドラフト(球団を選んだ状態)で開く(進行の画面の「ドラフト」・チームのページのボタン)
export function openEntries(teamId = null) {
  const r = state.records;
  r.section = "entries";
  r.kind = "draft";
  r.team = teamId;
  r.year = null;
}

function segButtons(id, items, value, onPick) {
  const box = $(id);
  box.replaceChildren(...items.map(([v, label]) => el("button", { type: "button", dataset: { value: v }, onclick: () => onPick(v) }, label)));
  setPressed(id, value);
}

function select(label, options, value, onChange) {
  const s = el("select", { "aria-label": label, onchange: (e) => onChange(e.target.value) }, ...options.map(([v, text]) => el("option", { value: String(v) }, text)));
  if (value !== null && value !== undefined) s.value = String(value);
  s.disabled = options.length === 0;
  return s;
}

export async function renderRecords(token) {
  const r = state.records;
  segButtons("records-section", SECTIONS, r.section, (v) => { r.section = v; r.year = null; renderCurrent(); });
  $("records-kind").hidden = r.section !== "entries";
  if (r.section === "entries") segButtons("records-kind", ENTRY_KINDS, r.kind, (v) => { r.kind = v; r.year = null; renderCurrent(); });
  const render = { standings: renderStandingsHistory, titles: renderTitles, leaders: renderLeaders, entries: r.kind === "moves" ? renderMoves : renderEntries }[r.section];
  await render(token);
}

function empty(text) {
  $("records-controls").replaceChildren();
  $("records-body").replaceChildren(el("p", { className: "info" }, text));
}

const NO_SEASON = "まだ終わったシーズンがありません。年度を確定すると、ここに記録が残ります。";

// ---- 歴代の順位と優勝 ----

async function renderStandingsHistory(token) {
  const r = state.records;
  const d = await query("record_standings", { year: r.year });
  if (token !== state.token) return;
  if (d.year === null) return empty(NO_SEASON);
  r.year = d.year;
  $("records-controls").replaceChildren(select("シーズンの選択", d.years.map((y) => [y, `${y}シーズン目`]).reverse(), d.year, (v) => { r.year = Number(v); renderCurrent(); }));
  const cols = [{ key: "wins", label: "勝" }, { key: "losses", label: "敗" }, { key: "ties", label: "分" }, { key: "pct", label: "勝率" }, { key: "gb", label: "差" }];
  const blocks = d.leagues.map((lg) => el("section", { className: "record-league" },
    el("h2", {}, lg.league_name),
    table({ firstLabel: "順位・チーム", columns: cols, rows: lg.rows.map((x) => ({ ...x, values: { wins: String(x.wins), losses: String(x.losses), ties: String(x.ties), pct: x.pct, gb: x.games_behind } })),
      first: (x) => [el("span", { className: "rank" + (x.champion ? " medal medal-1" : "") }, String(x.rank)), teamLink(x.team_name, x.team_id)], rowClass: (x) => (x.is_mine ? "mine" : ""), fluid: true })));
  const champs = table({ firstLabel: "チーム", columns: [{ key: "count", label: "優勝" }, { key: "years", label: "優勝した年" }],
    rows: d.champions.map((c) => ({ ...c, values: { count: `${c.count} 回`, years: c.years.length ? c.years.map((y) => `${y}`).join("・") : "—" } })),
    first: (c) => [teamLink(c.team_name, c.team_id), el("span", { className: "sub" }, c.league_name)], rowClass: (c) => (c.is_mine ? "mine" : ""), fluid: true });
  $("records-body").replaceChildren(...blocks, el("h2", {}, "球団ごとの優勝回数"), champs);
}

// ---- 年ごとのタイトル ----

async function renderTitles(token) {
  const r = state.records;
  const d = await query("record_titles", { year: r.year });
  if (token !== state.token) return;
  if (d.year === null) return empty(NO_SEASON);
  r.year = d.year;
  $("records-controls").replaceChildren(select("シーズンの選択", d.years.map((y) => [y, `${y}シーズン目`]).reverse(), d.year, (v) => { r.year = Number(v); renderCurrent(); }));
  const who = (t) => (t.players.length ? t.players.map((p) => [playerLink(p.name, p.player_id), el("span", { className: "sub" }, p.team_name)]).flat() : [el("span", { className: "muted" }, "—")]);
  const blocks = d.leagues.map((lg) => el("section", { className: "record-league" },
    el("h2", {}, lg.league_name),
    table({ firstLabel: "タイトル", columns: [{ key: "who", label: "選手" }, { key: "value", label: "記録" }],
      rows: lg.titles.map((t) => ({ ...t, values: { who: el("span", { className: "title-who" }, ...who(t)), value: t.value_text } })),
      first: (t) => [el("span", {}, t.label)], rowClass: (t) => (t.players.some((p) => p.is_mine) ? "mine" : ""), fluid: true })));
  $("records-body").replaceChildren(...blocks, el("p", { className: "muted small" }, "打率・出塁率・防御率は、規定打席・規定投球回に届いた選手だけ。"));
}

// ---- 通算記録 ----

async function renderLeaders(token) {
  const r = state.records;
  const d = await query("record_leaders", { role: r.role, key: r.leaderKey });
  if (token !== state.token) return;
  r.leaderKey = d.key;
  const roleSel = select("打者・投手の選択", [["batter", "打者"], ["pitcher", "投手"]], r.role, (v) => { r.role = v; r.leaderKey = null; renderCurrent(); });
  const keySel = select("記録の選択", d.keys.map((k) => [k.key, k.label]), d.key, (v) => { r.leaderKey = v; renderCurrent(); });
  $("records-controls").replaceChildren(roleSel, keySel);
  if (!d.rows.length) return $("records-body").replaceChildren(el("p", { className: "info" }, "まだ記録がありません。"));
  $("records-body").replaceChildren(
    table({ firstLabel: "順位・選手", columns: [{ key: "value", label: d.label }, { key: "seasons", label: "シーズン" }, { key: "team", label: "今の所属" }],
      rows: d.rows.map((x) => ({ ...x, values: { value: x.value_text, seasons: String(x.seasons), team: x.team_name } })),
      first: (x) => [el("span", { className: "rank" }, String(x.rank)), playerLink(x.name, x.player_id)], rowClass: (x) => (x.is_mine ? "mine" : !x.active ? "muted" : ""), fluid: true }),
    el("p", { className: "muted small" }, "今シーズン(進行中)までの通算。引退した選手も含む。"),
  );
}

// ---- 補強の記録:ドラフト・外国人(当たり外れ。D-216、D-420) ----

async function renderEntries(token) {
  const r = state.records;
  const d = await query("record_entries", { kind: r.kind, team_id: r.team, year: r.year });
  if (token !== state.token) return;
  r.team = d.team_id;
  r.year = d.year;
  const teamSel = select("球団の選択", d.teams.map((t) => [t.team_id, t.team_name + (t.is_mine ? " ★" : "")]), d.team_id, (v) => { r.team = v; r.year = null; renderCurrent(); });
  const yearSel = select("入団した年度の選択", d.years.map((y) => [y, `${y}シーズン目に入団`]).reverse(), d.year, (v) => { r.year = Number(v); renderCurrent(); });
  $("records-controls").replaceChildren(teamSel, yearSel);
  if (d.year === null) return $("records-body").replaceChildren(el("p", { className: "info" }, r.kind === "foreign" ? "まだ外国人市場で入った選手はいません。" : "まだドラフトで入った選手はいません。年度を確定してオフの手続きを終えると、ここに出ます。"));
  const cols = [
    { key: "route", label: "経路" },
    { key: "position", label: "ポジション" },
    { key: "age", label: "年齢" },
    { key: "entry", label: "入団時の見立て" },
    { key: "now", label: "今の総合" },
    { key: "status", label: "今の所属" },
    { key: "games", label: "出場" },
    { key: "war", label: "WAR 累計" },
  ];
  const rows = d.rows.map((x) => ({ ...x, values: { route: x.route_label, position: x.position_label, age: x.age !== null ? `${x.age}歳` : x.entry_age !== null ? `(${x.entry_age}歳)` : "", entry: x.entry_text, now: x.now_text, status: x.status_label, games: String(x.games), war: x.war } }));
  $("records-body").replaceChildren(
    rows.length
      ? table({ firstLabel: "選手", columns: cols, rows, first: (x) => [x.in_league ? playerLink(x.name, x.player_id) : el("span", {}, x.name)], rowClass: (x) => (x.status === "left" ? "muted" : ""), fluid: true })
      : el("p", { className: "info" }, "この年度にこの球団に入った選手はいません。"),
  );
}

// ---- 補強の記録:出入り ----

async function renderMoves(token) {
  const r = state.records;
  const d = await query("record_moves", { year: r.moveYear, team_id: r.moveTeam });
  if (token !== state.token) return;
  r.moveYear = d.year;
  const yearSel = select("年の選択", d.years.map((y) => [y, `${y}シーズン目(シーズン中とオフ)`]).reverse(), d.year, (v) => { r.moveYear = Number(v); renderCurrent(); });
  const teamSel = select("球団の選択", [["", "すべての球団"], ...d.teams.map((t) => [t.team_id, t.team_name + (t.is_mine ? " ★" : "")])], d.team_id || "", (v) => { r.moveTeam = v || null; renderCurrent(); });
  $("records-controls").replaceChildren(yearSel, teamSel);
  if (d.year === null) return $("records-body").replaceChildren(el("p", { className: "info" }, "まだ出入りの記録がありません。"));
  const cols = [{ key: "label", label: "種類" }, { key: "from", label: "前の所属" }, { key: "to", label: "新しい所属" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }];
  $("records-body").replaceChildren(
    d.rows.length
      ? table({ firstLabel: "選手", columns: cols, rows: d.rows.map((x) => ({ ...x, values: { label: x.label, from: x.from_team_name, to: x.to_team_name, position: x.position_label, age: x.age ? `${x.age}歳` : "" } })),
        first: (x) => [x.linkable ? playerLink(x.name, x.player_id) : el("span", {}, x.name)], rowClass: (x) => (x.is_mine ? "mine" : ""), fluid: true })
      : el("p", { className: "info" }, "この条件の出入りはありません。"),
  );
}
