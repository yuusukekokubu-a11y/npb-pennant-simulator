// 年度の確定と、オフの結果(web/app.js から分けた。保守②。D-291)

import { $, STATS_PAGE, freshProc, state } from "./core.js";
import { answer, call, query } from "./backend.js";
import { current, openPage, renderCurrent, showScreen } from "./screens.js";
import { el, playerLink, table } from "./parts.js";
import { renderProgress, setDirty } from "./progress.js";
import { buildStatsFilters } from "./stats.js";

// ---- 年度の確定(F2。D-185)と、オフの結果 ----

export async function renderYearEnd(token) {
  const r = await call("query", { name: "year_end_preview", args: {} });
  if (!r.ok) throw new Error(r.message);
  if (token !== state.token) return;
  const d = r.value;
  $("yearend-title").textContent = `${d.year}シーズン目を終えて、${d.year + 1}シーズン目に進みます。`;
  $("yearend-champions").replaceChildren(...d.champions.map((c) => el("p", {}, `${c.league_name} 優勝:${c.teams.join("・")}`)));
  $("yearend-note").textContent = d.note;
  $("yearend-dirty").textContent = d.dirty ? "今のゲームには、未保存の変更があります。確定の前の状態を残しておきたいときは、先に保存してください(確定したあとの保存とは、別のファイルになります)。" : "今のゲームは保存済みです(確定したあとに保存すると、別のファイルになります)。";
  $("yearend-go").disabled = !d.is_over || state.running;
  $("yearend-message").textContent = "";
}

export async function yearEnd() {
  if (state.running || !state.view || !state.view.status.can_year_end) return;
  state.running = true;
  $("yearend-go").disabled = true;
  $("yearend-message").className = "message";
  $("yearend-message").textContent = "年度を確定しています(集計を履歴に残し、選手の年齢・能力・引退・新人を決めています)…";
  try {
    const r = await call("yearEnd", {});
    if (!r.ok) throw new Error(r.message);
    state.view = r.value.view;
    state.cache.clear();
    state.stats.season = "current";
    state.gamesDay = null;
    setDirty(true);
    buildStatsFilters();
    const s = r.value.summary;
    state.pages = [];
    showScreen("progress");
    renderProgress();
    if (state.view.status.offseason) {
      // 操作する球団があるときは、オフの手続き(自由契約 → ドラフト → 市場)へ(F3-1)
      $("progress-message").className = "message ok";
      $("progress-message").textContent = `${s.year}シーズン目を確定しました(引退 ${s.counts.retired}人)。オフの手続きを進めてください。`;
      state.proc = freshProc();
      openPage("procedure");
    } else {
      $("progress-message").className = "message ok";
      $("progress-message").textContent = `${s.year}シーズン目を確定し、${s.next_year}シーズン目が始まりました(引退 ${s.counts.retired}人・新人 ${s.counts.rookies}人)。`;
      openPage("offseason", { year: s.year });
    }
  } catch (err) {
    $("yearend-message").className = "message ng";
    $("yearend-message").textContent = `年度を確定できませんでした:${err.message}`;
  } finally {
    state.running = false;
    if (current().name === "yearend") $("yearend-go").disabled = !state.view.status.can_year_end;
  }
}

function playerTable(rows, withOrigin) {
  const columns = [{ key: "position", label: "ポジション" }, { key: "age", label: "年齢" }].concat(withOrigin ? [{ key: "origin", label: "出身" }] : []);
  return table({
    firstLabel: "選手",
    columns,
    rows: rows.map((r) => ({ ...r, values: { position: r.position, age: `${r.age}歳`, origin: r.origin || "" } })),
    first: (r) => [playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)],
    rowClass: (r) => (r.is_mine ? "mine" : ""),
  });
}

export async function renderOffseason(args, token) {
  const d = await query("offseason_summary", { year: args.year ?? null });
  if (token !== state.token) return;
  if (!d.available) {
    $("offseason-note").textContent = "まだ年度を確定していません。";
    for (const id of ["offseason-retired", "offseason-rookies", "offseason-answers"]) $(id).replaceChildren();
    return;
  }
  $("offseason-title").textContent = `オフの結果(${d.year}シーズン目の終わり)`;
  $("offseason-note").textContent = d.note;
  $("offseason-counts").textContent = `引退 ${d.counts.retired}人 / 新人 ${d.counts.rookies}人 / 選手の数 ${d.counts.players}人(変わりません)`;
  $("offseason-retired").replaceChildren(d.retired.length ? playerTable(d.retired, false) : el("p", { className: "muted" }, "引退した選手はいません。"));
  $("offseason-rookies").replaceChildren(d.rookies.length ? playerTable(d.rookies, true) : el("p", { className: "muted" }, "入団した新人はいません。"));
  const box = $("offseason-answers");
  if (state.answerLevel === 0) {
    box.replaceChildren(el("p", { className: "info" }, "答え合わせモードをオンにすると、残った選手の能力の増減が見られます(上の「メニュー」から)。"));
    return;
  }
  const a = await answer("offseason_answers", { year: d.year });
  if (token !== state.token || state.answerLevel === 0) return;
  const columns = [{ key: "age", label: "年齢" }, { key: "mean_change", label: "平均の増減", description: "能力の項目ごとの増減の平均" }, { key: "items", label: "項目ごと", description: "項目名と増減" }];
  box.replaceChildren(
    el("p", { className: "muted small" }, a.note),
    table({
      firstLabel: "選手",
      columns,
      rows: a.players.map((r) => ({ ...r, values: { age: `${r.age}歳`, mean_change: r.mean_change, items: r.items.map((i) => `${i.label}${i.change}`).join(" ") } })),
      first: (r) => [playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)],
      rowClass: (r) => (r.is_mine ? "mine" : ""),
      limit: args.answersShown || STATS_PAGE,
      more: () => { args.answersShown = (args.answersShown || STATS_PAGE) + STATS_PAGE; renderCurrent(); },
    }),
  );
}
