// 成績(個人成績の表・並び順・能力の表)(web/app.js から分けた。保守②。D-291)

import { $, STATS_PAGE, state } from "./core.js";
import { answer, query, withLoading } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, playerLink, setPressed, sortToggle, table } from "./parts.js";
import { seasonCaution } from "./glossary.js";

// ---- 成績(個人成績。D-114〜D-116) ----

export function buildStatsFilters() {
  const seasons = state.view.status.seasons || [{ key: "current", label: "今シーズン" }];
  $("stats-season").replaceChildren(...seasons.map((x) => el("option", { value: x.key }, x.label)));
  if (!seasons.some((x) => x.key === state.stats.season)) state.stats.season = "current";
  $("stats-season").value = state.stats.season;
  $("stats-season").hidden = seasons.length < 2; // 1シーズン目は選ぶものがないので出さない
  const leagues = state.view.standings.leagues;
  $("stats-league").replaceChildren(el("option", { value: "" }, "両リーグ"), ...leagues.map((lg) => el("option", { value: String(lg.index) }, lg.name)));
  $("stats-league").value = state.stats.league;
  const teams = leagues.filter((lg) => state.stats.league === "" || String(lg.index) === state.stats.league).flatMap((lg) => lg.rows);
  $("stats-team").replaceChildren(el("option", { value: "" }, "全チーム"), ...teams.map((r) => el("option", { value: r.team_id }, r.name)));
  if (!teams.some((r) => r.team_id === state.stats.team)) state.stats.team = "";
  $("stats-team").value = state.stats.team;
  $("stats-qualified").checked = state.stats.qualified;
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

export function isAbilityKey(key) {
  return state.abilityKeys[state.stats.role].includes(key);
}

function isWarKey(key) {
  return state.warKeys[state.stats.role].includes(key);
}

// 並び順の選択欄(D-132):基本・セイバーの全指標。答え合わせモードがオンなら、能力の項目も
async function buildSortSelect(sortKey, warColumns) {
  const role = state.stats.role;
  const keys = await query("sortable_keys", { role });
  const groups = { basic: [], saber: [], count: [] };
  for (const k of keys) (groups[k.type === "count" ? "count" : k.category] || groups.count).push(k);
  const options = [
    el("optgroup", { label: "基本" }, ...groups.basic.map((k) => el("option", { value: k.key }, k.label))),
    el("optgroup", { label: "セイバー" }, ...groups.saber.map((k) => el("option", { value: k.key }, k.label))),
    el("optgroup", { label: "元の数" }, ...groups.count.map((k) => el("option", { value: k.key }, k.label))),
  ];
  if (warColumns) {
    state.warKeys[role] = warColumns.map((c) => c.key);
    options.unshift(el("optgroup", { label: "WAR(WAR の表でだけ)" }, ...warColumns.map((c) => el("option", { value: c.key }, c.label))));
  }
  if (state.answerLevel > 0) {
    const cols = await answer("ability_columns", { role });
    state.abilityKeys[role] = cols.map((c) => c.key);
    options.push(el("optgroup", { label: "能力(答え合わせモード)" }, ...cols.map((c) => el("option", { value: c.key }, c.label))));
  } else {
    state.abilityKeys[role] = [];
  }
  $("stats-sort").replaceChildren(...options);
  $("stats-sort").value = sortKey;
}

// 「既定」のままの並び順を、実際に使った指標に固定する(基本・セイバー・能力を切り替えても保つため。D-131)
export function commitSort() {
  const s = currentSort();
  const shown = state.shownSort[state.stats.role];
  if (s.key === null && shown) Object.assign(s, shown);
}

function showSortState(sortCol, order) {
  state.shownSort[state.stats.role] = { key: sortCol.key, order };
  $("stats-sort").value = sortCol.key;
  const counts = sortCol.type === "count";
  for (const b of $("stats-order").children) {
    b.textContent = b.dataset.value === "desc" ? (counts ? "多い順" : "高い順") : counts ? "少ない順" : "低い順";
    b.setAttribute("aria-pressed", String(b.dataset.value === order));
  }
}

export async function renderStats(token) {
  const s = state.stats;
  setPressed("stats-role", s.role);
  setPressed("stats-kind", s.kind);
  $("stats-info").replaceChildren();
  if (s.kind === "ability") {
    $("stats-baseline").hidden = true;
    return renderAbilityTable(token);
  }
  const sort = currentSort();
  // 能力の項目で並べていたときは、成績の表ではその表の既定に戻す(能力の値は、公開用の関数では扱わない)。
  // WAR の列は WAR の表でだけ、WAR 以外の列は WAR 以外の表でだけ使える
  const usable = sort.key && !isAbilityKey(sort.key) && (s.kind === "war" ? isWarKey(sort.key) : !isWarKey(sort.key));
  const key = usable ? sort.key : null;
  const args = { ...statsArgs(), kind: s.kind, sort: key, order: key ? sort.order : null };
  const data = await withLoading("stats-loading", query("stats", args));
  if (token !== state.token) return;
  await buildSortSelect(data.sort.key, s.kind === "war" ? data.columns : null);
  if (token !== state.token) return;
  showSortState(data.sort, data.order);
  const first = (r) => [el("span", { className: "rank" }, String(r.rank)), playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)];
  $("stats-table").replaceChildren(
    data.rows.length
      ? table({
          firstLabel: "順位・選手",
          columns: data.columns,
          rows: data.rows,
          first,
          sort: data.sort.key,
          order: data.order,
          onSort: (key) => sortStats(key),
          rowClass: (r) => (r.is_mine ? "mine" : ""),
          limit: s.shown,
          more: () => { s.shown += STATS_PAGE; renderCurrent(); },
          extra: data.extra_column,
        })
      : el("p", { className: "muted" }, s.qualified ? "条件に合う選手がいません(「規定到達者だけ」を外すと出ます)。" : "条件に合う選手がいません。"),
  );
  // 読み違いを防ぐ 1 行の注意書きだけを残す(D-310)。詳しい理由は用語集に
  const caution = data.baseline_note ? seasonCaution(data.season) : "";
  $("stats-baseline").hidden = !caution;
  $("stats-baseline").textContent = caution;
}

function sortStats(key) {
  sortToggle(currentSort(), state.shownSort[state.stats.role], key);
  state.stats.shown = STATS_PAGE;
  renderCurrent();
}

async function renderAbilityTable(token) {
  const s = state.stats;
  if (state.answerLevel === 0) {
    await buildSortSelect(currentSort().key);
    if (token !== state.token) return;
    $("stats-info").replaceChildren(el("strong", {}, "答え合わせモードをオンにすると見られます"), "(上の「メニュー」から)");
    $("stats-table").replaceChildren();
    return;
  }
  const sort = currentSort();
  const data = await withLoading("stats-loading", answer("ability_table", { ...statsArgs(), sort: sort.key, order: sort.order }));
  if (token !== state.token || state.answerLevel === 0) return;
  await buildSortSelect(data.sort.key);
  if (token !== state.token || state.answerLevel === 0) return;
  showSortState(data.sort, data.order);
  $("stats-table").replaceChildren(
    table({
      firstLabel: "順位・選手",
      columns: data.columns,
      rows: data.rows,
      first: (r) => [el("span", { className: "rank" }, String(r.rank)), playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)],
      sort: data.sort.key,
      order: data.order,
      onSort: (key) => sortStats(key),
      rowClass: (r) => (r.is_mine ? "mine" : ""),
      limit: s.shown,
      more: () => { s.shown += STATS_PAGE; renderCurrent(); },
      extra: data.extra_column,
    }),
  );
}
