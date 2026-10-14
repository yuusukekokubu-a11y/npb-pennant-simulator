// ドラフトの振り返り(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { query } from "./backend.js";
import { el, playerLink, table } from "./parts.js";

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
  if (d.year === null) {
    $("review-table").replaceChildren(el("p", { className: "info" }, "まだこの球団に入った選手はいません。"));
    $("review-note").textContent = "";
    return;
  }
  const cols = [
    { key: "route", label: "経路" },
    { key: "position", label: "ポジション" },
    { key: "age", label: "年齢" },
    { key: "grade", label: "入団時の評価" },
    { key: "comment", label: "スカウトの一言" },
    { key: "status", label: "今の所属" },
    { key: "games", label: "出場" },
    { key: "war", label: "WAR 累計" },
  ];
  const rows = d.rows.map((r) => {
    return {
      ...r,
      values: {
        route: r.route_label,
        position: r.position_label,
        age: r.age !== null ? `${r.age}歳` : r.entry_age !== null ? `(${r.entry_age}歳)` : "",
        grade: r.entry_grade,
        comment: r.entry_comment || "—",
        status: r.status_label,
        games: String(r.games),
        war: r.war,
      },
    };
  });
  $("review-table").replaceChildren(
    rows.length
      ? table({ firstLabel: "選手", columns: cols, rows, first: (r) => [r.in_league ? playerLink(r.name, r.player_id) : el("span", {}, r.name)], rowClass: (r) => (r.status === "left" ? "muted" : ""), fluid: true })
      : el("p", { className: "info" }, "この年度にこの球団に入った選手はいません。"),
  );
  $("review-note").textContent = "";
}
