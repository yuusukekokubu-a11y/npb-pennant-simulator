// ドラフトの振り返り(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { answer, query } from "./backend.js";
import { el, playerLink, table } from "./parts.js";
import { ceilingText } from "./procedure.js";

// ---- ドラフトの振り返り(当たり外れの一覧。D-216) ----

export async function renderReview(args, token) {
  const rv = state.review;
  if (args.team_id) {
    rv.team = args.team_id;
    rv.year = null;
    args.team_id = null;
  }
  $("review-loading").hidden = false;
  let d;
  try {
    d = await query("draft_review", { team_id: rv.team, year: rv.year });
  } finally {
    if (token === state.token) $("review-loading").hidden = true;
  }
  if (token !== state.token) return;
  rv.team = d.team_id;
  rv.year = d.year;
  const teamSel = $("review-team");
  teamSel.replaceChildren(...d.teams.map((t) => el("option", { value: t.team_id }, t.team_name + (t.is_mine ? " ★" : ""))));
  teamSel.value = d.team_id;
  const yearSel = $("review-year");
  yearSel.replaceChildren(...d.years.map((y) => el("option", { value: String(y) }, `${y}シーズン目に入団`)));
  yearSel.disabled = d.years.length === 0;
  if (d.year !== null) yearSel.value = String(d.year);
  $("review-answers").replaceChildren();
  $("review-answers-note").textContent = "";
  if (d.year === null) {
    $("review-table").replaceChildren(el("p", { className: "info" }, "まだこの球団に入った選手はいません。"));
    $("review-note").textContent = "";
    return;
  }
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("draft_review_answers", { team_id: d.team_id, year: d.year });
    if (token !== state.token) return;
    if (state.answerLevel === 0) truth = null;
  }
  const on = truth && truth.available;
  const cols = [
    { key: "route", label: "経路" },
    { key: "position", label: "ポジション" },
    { key: "age", label: "年齢" },
    { key: "entry", label: "入団時の総合" },
    { key: "ceiling", label: "天井" },
    { key: "status", label: "今の所属" },
    { key: "games", label: "出場" },
    { key: "war", label: "WAR 累計" },
  ];
  if (on) {
    cols.push(
      { key: "truth", label: "今の真の総合" },
      { key: "diff", label: "差" },
      { key: "actual", label: "実際の天井" },
    );
    if (truth.level === 2) cols.push({ key: "potential", label: "潜在能力" });
  }
  const rows = d.rows.map((r) => {
    const t = on ? truth.players[r.player_id] : null;
    return {
      ...r,
      values: {
        route: r.route_label,
        position: r.position_label,
        age: r.age !== null ? `${r.age}歳` : r.entry_age !== null ? `(${r.entry_age}歳)` : "",
        entry: r.entry_text,
        ceiling: ceilingText(r.entry_ceiling),
        status: r.status_label,
        games: String(r.games),
        war: r.war,
        truth: t ? t.overall : r.in_league ? "" : "-",
        diff: t ? t.diff : r.in_league ? "" : "-",
        actual: t ? t.actual_ceiling : "-",
        potential: t && t.potential ? t.potential : "",
      },
    };
  });
  $("review-table").replaceChildren(
    rows.length
      ? table({ firstLabel: "選手", columns: cols, rows, first: (r) => [r.in_league ? playerLink(r.name, r.player_id) : el("span", {}, r.name)], rowClass: (r) => (r.status === "left" ? "muted" : ""), fluid: true })
      : el("p", { className: "info" }, "この年度にこの球団に入った選手はいません。"),
  );
  $("review-note").textContent = on ? "入団直後は、差がマイナスに偏りやすい。" : "";
  if (!on) {
    $("review-answers-note").textContent = "";
    return;
  }
  const tcols = [
    { key: "count", label: "人数" },
    { key: "mean", label: "差の平均" },
    { key: "sd", label: "差の標準偏差" },
  ];
  const trows = truth.teams.map((t) => ({ ...t, values: { count: String(t.count), mean: t.mean, sd: t.sd } }));
  trows.push({ team_id: "", team_name: "リーグ全体", selected: false, is_mine: false, values: { count: String(truth.league.count), mean: truth.league.mean, sd: truth.league.sd } });
  $("review-answers").replaceChildren(
    el("h2", {}, `球団ごとの「見る目」の目安(${d.year}シーズン目の入団)`),
    table({ firstLabel: "球団", columns: tcols, rows: trows, first: (r) => [el("span", { className: r.selected ? "sorted" : "" }, r.team_name + (r.is_mine ? " ★" : ""))], rowClass: (r) => (r.selected ? "mine" : ""), fluid: true }),
  );
}
