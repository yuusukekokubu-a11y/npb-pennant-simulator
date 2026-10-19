// 成績(個人成績の表・並び順・能力の表)(web/app.js から分けた。保守②。D-291)。
// 表示・絞り込み・並びは表のすぐ上の帯(D-482)。表示を切り替えても列の数・幅・並び順・選んだ選手は変わらない(D-483)。行を押すと選手の札(D-484)

import { $, STATS_PAGE, state } from "./core.js";
import { query, withLoading } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { band, bandSelect, el, emblem, link, nameCell, pinSorted, rankBadge, setPressed, sortControls, sortLabel, sortToggle, table } from "./parts.js";
import { selectRow } from "./card.js";
import { seasonCaution } from "./glossary.js";

const KINDS = [{ key: "basic", label: "基本" }, { key: "saber", label: "セイバー" }, { key: "war", label: "WAR" }, { key: "ability", label: "能力" }];

// ---- 成績(個人成績。D-114〜D-116) ----

const statsSeasons = () => state.view.status.seasons || [{ key: "current", label: "今シーズン" }];
const leagueTeams = () => state.view.standings.leagues.filter((lg) => String(lg.index) === state.stats.league).flatMap((lg) => lg.rows);

// 絞り込みの値を、今のシーズン・リーグ・チームに合うように直す(選ぶ欄は帯を描くときに作る)
export function buildStatsFilters() {
  if (!statsSeasons().some((x) => x.key === state.stats.season)) state.stats.season = "current";
  const leagues = state.view.standings.leagues;
  // 両リーグをまとめた表はない。最初は自球団のリーグ(観戦のみなら 1 つ目。D-431)
  if (!leagues.some((lg) => String(lg.index) === state.stats.league)) {
    const mine = leagues.find((lg) => lg.rows.some((r) => r.is_mine)) || leagues[0];
    state.stats.league = mine ? String(mine.index) : "";
  }
  if (!leagueTeams().some((r) => r.team_id === state.stats.team)) state.stats.team = "";
}

// 帯の「絞り込み」の中身と、閉じているときの文字(D-482)。1シーズン目はシーズンを選ぶ欄を出さない
function filterControls(positions) {
  const s = state.stats;
  const set = (k, v) => {
    s[k] = v;
    s.shown = STATS_PAGE;
    if (k === "league") buildStatsFilters();
    renderCurrent();
  };
  const seasons = statsSeasons();
  const leagues = state.view.standings.leagues.map((lg) => ({ key: String(lg.index), label: lg.name }));
  const teams = [{ key: "", label: "全チーム" }, ...leagueTeams().map((r) => ({ key: r.team_id, label: r.name }))];
  const items = [];
  if (positions) items.push(bandSelect("stats-position", "守備のポジション", positions, s.position, (v) => { s.position = v; state.sortBy.fielder = { key: null, order: null }; state.shownSort.fielder = null; s.shown = STATS_PAGE; renderCurrent(); }));
  if (seasons.length > 1) items.push(bandSelect("stats-season", "シーズン", seasons, s.season, (v) => set("season", v)));
  items.push(bandSelect("stats-league", "リーグ", leagues, s.league, (v) => set("league", v)));
  items.push(bandSelect("stats-team", "チーム", teams, s.team, (v) => set("team", v)));
  items.push(el("label", { className: "check" }, el("input", { type: "checkbox", id: "stats-qualified", checked: s.qualified, onchange: (e) => set("qualified", e.target.checked) }), "規定到達者だけ"));
  const label = (list, key) => (list.find((x) => x.key === key) || {}).label || "";
  const text = [positions ? label(positions, s.position) : "", seasons.length > 1 ? label(seasons, s.season) : "", label(leagues, s.league), label(teams, s.team), s.qualified ? "規定到達者だけ" : "全員"].filter(Boolean).join("・");
  return { items, text };
}

// 並べている指標の 1 位の選手のカード(D-400)。名前を押すと、その選手の札
function renderLeader(data) {
  const r = data.rows[0];
  const col = [...data.columns, data.extra_column].find((c) => c && c.key === data.sort.key);
  if (!r || !col || r.values[data.sort.key] === undefined || r.values[data.sort.key] === "") {
    $("stats-leader").replaceChildren();
    return;
  }
  $("stats-leader").replaceChildren(
    el("div", { className: "leader" },
      emblem(r.team_id, r.team_name),
      el("div", { className: "leader-body" }, el("span", { className: "leader-title" }, `👑 ${col.label} 1位`), el("div", { className: "leader-name" }, link(r.name, () => selectRow("stats", r.player_id)), el("span", { className: "leader-team" }, r.team_name))),
      el("div", { className: "leader-value" }, String(r.values[data.sort.key]))),
  );
}

function statsArgs() {
  const s = state.stats;
  return {
    role: s.role,
    qualified: s.qualified,
    league: s.league === "" ? null : Number(s.league),
    team_id: s.team || null,
    season: s.season === "current" ? null : s.season,
  };
}

export function currentSort() {
  return state.sortBy[state.stats.role];
}

// 帯の「並び」の選択肢(D-132、D-483):基本・セイバー・元の数・WAR・能力。どの表示でも全部から選べる
async function sortGroups(role) {
  const [keys, war, ability] = await Promise.all([query("sortable_keys", { role }), query("war_columns", { role }), query("ability_columns", { role })]);
  const groups = { basic: [], saber: [], count: [] };
  for (const k of keys) (groups[k.type === "count" ? "count" : k.category] || groups.count).push(k);
  return [{ label: "基本", items: groups.basic }, { label: "セイバー", items: groups.saber }, { label: "元の数", items: groups.count }, { label: "WAR", items: war }, { label: "能力", items: ability }];
}

// 「既定」のままの並び順を、実際に使った指標に固定する(基本・セイバー・能力を切り替えても保つため。D-131)
export function commitSort() {
  const s = currentSort();
  const shown = state.shownSort[state.stats.role];
  if (s.key === null && shown) Object.assign(s, shown);
}

export async function renderStats(token) {
  const s = state.stats;
  setPressed("stats-role", s.role);
  const fielding = s.role === "fielder"; // 守備は基本・セイバーなどの切り替えがなく、ポジションを選ぶ(D-431)
  const sort = currentSort();
  const args = { ...statsArgs(), sort: sort.key, order: sort.key ? sort.order : null };
  let data;
  if (fielding) {
    delete args.role;
    data = await withLoading("stats-loading", query("fielding_stats", { ...args, position: s.position }));
  } else if (s.kind === "ability") {
    data = await withLoading("stats-loading", query("ability_table", { ...args, season: undefined }));
  } else {
    data = await withLoading("stats-loading", query("stats", { ...args, kind: s.kind }));
  }
  if (token !== state.token) return;
  const groups = fielding ? null : await sortGroups(s.role);
  if (token !== state.token) return;
  state.shownSort[s.role] = { key: data.sort.key, order: data.order };
  renderLeader(data);
  // 読み違いを防ぐ注意書きは、帯の中の小さな印(D-310、D-482)。詳しい理由は用語集に
  const caution = fielding || s.kind === "war" || data.baseline_note ? seasonCaution(data.season) : "";
  const filters = filterControls(fielding ? data.positions : null);
  const onKey = (key) => { state.sortBy[s.role] = { key, order: null }; s.shown = STATS_PAGE; renderCurrent(); };
  const onOrder = (o) => { commitSort(); currentSort().order = o; s.shown = STATS_PAGE; renderCurrent(); };
  $("stats-band").replaceChildren(band({
    id: "stats", views: fielding ? [] : KINDS, viewsId: "stats-kind", view: s.kind,
    onView: (k) => { commitSort(); s.kind = k; renderCurrent(); }, // 並び順・絞り込み・表示件数は保つ(D-131)
    filters: filters.items, filterText: filters.text, note: caution,
    sortText: sortLabel(data.sort.label, data.order),
    sortControls: sortControls("stats", { groups, options: data.columns, key: data.sort.key, order: data.order, counts: data.sort.type === "count", onKey, onOrder }),
  }));
  if (!data.rows.length) {
    $("stats-table").replaceChildren(el("p", { className: "muted" }, s.qualified ? "条件に合う選手がいません(「規定到達者だけ」を外すと出ます)。" : "条件に合う選手がいません。"));
    return;
  }
  const { pinned, rest } = pinSorted(data.columns, data.sort, data.extra_column); // 並べた列は名前のすぐ右(D-483)
  $("stats-table").replaceChildren(table({
    firstLabel: "順位・選手",
    columns: rest,
    extra: pinned,
    rows: data.rows,
    first: (r) => [rankBadge(r.rank), ...nameCell(r.name, r.team_name)],
    sort: data.sort.key,
    order: data.order,
    onSort: (key) => sortStats(key),
    rowClass: (r) => (r.is_mine ? "mine" : ""),
    limit: s.shown,
    more: () => { s.shown += STATS_PAGE; renderCurrent(); },
    pick: "stats",
    slots: data.slots,
    keep: "stats",
  }));
}

function sortStats(key) {
  sortToggle(currentSort(), state.shownSort[state.stats.role], key);
  state.stats.shown = STATS_PAGE;
  renderCurrent();
}
