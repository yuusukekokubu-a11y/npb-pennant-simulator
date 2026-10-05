// 選手のページ(成績・試合ごと・スカウト評価・契約・志望・年度別)(web/app.js から分けた。保守②。D-291)

import { $, GAMES_PAGE, state } from "./core.js";
import { answer, query } from "./backend.js";
import { openPage, renderCurrent } from "./screens.js";
import { el, kvTable, link, setPressed, table, teamLink } from "./parts.js";
import { seasonCaution } from "./glossary.js";

export async function renderPlayer(args, token) {
  const data = await query("player", { player_id: args.id });
  if (token !== state.token) return;
  const p = data.player;
  $("player-name").textContent = p.name;
  $("player-info").replaceChildren(
    teamLink(p.team_name, p.team_id),
    p.is_mine ? " ★" : "",
    ` / ${p.position_label} / ${p.age}歳${p.hand ? ` / ${p.hand}` : ""}${p.retired ? "(引退)" : ""}`,
  );
  renderPlayerHistory(data);
  renderPlayerContract(data);
  await renderPlayerScouting(data, token);
  setPressed("player-kind", state.playerKind);
  const box = $("player-season");
  $("player-baseline").hidden = true;
  if (state.playerKind === "ability") {
    if (state.answerLevel === 0) {
      box.replaceChildren(el("p", { className: "info" }, "答え合わせモードをオンにすると見られます(上の「メニュー」から)。"));
    } else {
      const a = await answer("player_answers", { player_id: args.id });
      if (token !== state.token || state.answerLevel === 0) return;
      const rows = a.items.map((i) => ({ label: i.label, values: a.level === 2 ? [i.current, i.potential] : [i.current] }));
      const parts = [kvTable(rows)];
      if (a.level === 2) parts.unshift(el("p", { className: "muted small" }, "左:今の能力 / 右:潜在能力"));
      if (a.level === 2) parts.push(el("p", {}, `成長タイプ:${a.growth_type} / 生成時の型:${a.archetype}`));
      box.replaceChildren(...parts);
    }
  } else if (!data.season) {
    box.replaceChildren(el("p", { className: "muted" }, "まだ試合に出ていません。"));
  } else if (state.playerKind === "war") {
    const w = data.season.war;
    const rows = w.columns.map((c) => ({ label: c.label, values: [w.values[c.key]] }));
    box.replaceChildren(kvTable(rows));
    $("player-baseline").hidden = !seasonCaution("current");
    $("player-baseline").textContent = seasonCaution("current");
  } else {
    const t = data.season.tables[state.playerKind];
    const caution = state.playerKind === "saber" && data.season.baseline_note ? seasonCaution("current") : "";
    $("player-baseline").hidden = !caution;
    $("player-baseline").textContent = caution;
    const rows = t.columns.map((c) => ({ label: c.label, values: [t.values[c.key]] }));
    box.replaceChildren(kvTable(rows), el("p", { className: "muted small" }, data.season.qualified ? "規定に届いています。" : "規定に届いていません。"));
  }
  const gbox = $("player-games");
  if (!data.games.length) {
    gbox.replaceChildren(el("p", { className: "muted" }, "まだ試合に出ていません。"));
    return;
  }
  const cols = [{ key: "result", label: "結果" }, ...data.game_columns];
  const rows = data.games.map((g) => ({
    ...g,
    values: { ...g.values, result: `${g.outcome === "勝" ? "○" : g.outcome === "負" ? "●" : "△"}${g.score}${g.decision ? ` ${g.decision}` : ""}` },
  }));
  const limit = args.gamesShown || GAMES_PAGE;
  gbox.replaceChildren(
    table({
      firstLabel: "日・相手",
      columns: cols,
      rows,
      first: (g) => [link(`${g.day}日目`, () => openPage("game", { game_no: g.game_no })), el("span", { className: "sub" }, `${g.home ? "対" : "@"} ${g.opponent}`)],
      limit,
      more: () => { args.gamesShown = limit + GAMES_PAGE; renderCurrent(); },
    }),
  );
}

// 入団時のスカウト評価(F3-1。D-199、D-206)。答え合わせモードがオンなら、真の能力を並べる
// 契約(F3-2a。D-232):年俸・契約年数・残り年数・年俸の履歴(公開情報。他球団の選手も)
// 志望の重み(答え合わせモードがオンのときだけ。F3-2b。D-245)
async function renderPlayerPreference(playerId) {
  const token = state.token;
  const a = await answer("player_answers", { player_id: playerId });
  if (token !== state.token || state.answerLevel === 0 || !a.preference || !a.preference.length) return;
  $("player-contract").append(el("p", { className: "small answer", id: "player-preference" }, `志望(答え合わせ):${a.preference.map((x) => `${x.label} ${x.text}${x.active ? "" : "(効かない)"}`).join("・")}`));
}

function renderPlayerContract(data) {
  const c = data.player.contract;
  const box = $("player-contract-box");
  box.hidden = !c;
  if (!c) return;
  const years = c.history.length ? c.history[c.history.length - 1].years : null;
  $("player-contract").replaceChildren(
    kvTable([
      { label: "年俸", values: [c.salary_text] },
      { label: "契約年数", values: [years ? `${years} 年` : "-"] },
      { label: "残り年数", values: [`${c.remaining} 年(${c.until}シーズン目まで)`] },
      { label: "FA 権", values: [c.fa_text] },
    ]),
    el("details", { id: "player-contract-history" }, el("summary", {}, "年俸と更改の履歴"), table({ firstLabel: "シーズン", columns: [{ key: "salary", label: "年俸" }, { key: "years", label: "年数" }, { key: "reason", label: "きっかけ" }, { key: "offers", label: "提示" }], rows: [...c.history].reverse().map((h) => ({ ...h, values: { salary: h.salary_text, years: `${h.years} 年`, reason: h.reason_label, offers: h.offers ? `${h.offers} 回` : "-" } })), first: (h) => [el("span", {}, `${h.year}シーズン目〜`)] })),
  );
  if (state.answerLevel > 0) renderPlayerPreference(data.player.id);
}

async function renderPlayerScouting(data, token) {
  const sc = data.player.scouting;
  const box = $("player-scouting-box");
  if (!sc) {
    box.hidden = true;
    $("player-scouting").replaceChildren();
    return;
  }
  box.hidden = false;
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("scouting_answers", { player_id: data.player.id });
    if (token !== state.token) return;
    if (state.answerLevel === 0) truth = null;
  }
  const byKey = truth && truth.available ? Object.fromEntries(truth.items.map((i) => [i.key, i])) : null;
  const rows = [{ label: "総合", values: [sc.overall_text, byKey ? "" : ""].slice(0, byKey ? 2 : 1) }];
  for (const i of sc.items) rows.push({ label: i.label, values: byKey ? [i.text, `${byKey[i.key]?.current ?? ""}${byKey[i.key]?.potential ? ` / ${byKey[i.key].potential}` : ""}`] : [i.text] });
  rows.push({ label: "天井", values: byKey ? [sc.ceiling, ""] : [sc.ceiling] });
  $("player-scouting").replaceChildren(
    el("p", { className: "muted small" }, `${sc.team_name}・${sc.year ? `${sc.year}シーズン目` : "ゲーム開始前"}の入団時${sc.method === 1 ? "(旧方式)" : ""}${byKey ? `。右は真の能力(今${truth.level === 2 ? " / 潜在" : ""})` : ""}`),
    kvTable(rows),
  );
}

// 年度別の成績(過去シーズン・今シーズン・通算。F2。D-182)。種類の切り替え(基本・セイバー・WAR)は上の表と同じ
function renderPlayerHistory(data) {
  const h = data.history;
  const box = $("player-history-box");
  if (!h || !h.rows.length || state.playerKind === "ability") {
    box.hidden = true;
    $("player-history").replaceChildren();
    return;
  }
  box.hidden = false;
  const kind = state.playerKind;
  const columns = kind === "war" ? h.war_columns : h.columns[kind];
  const rows = h.rows.map((r) => ({ ...r, values: kind === "war" ? r.war || {} : r.tables[kind] }));
  $("player-history").replaceChildren(
    table({
      firstLabel: "シーズン",
      columns,
      rows,
      first: (r) => [el("span", {}, r.season === "career" ? "通算" : r.season === "current" ? `${r.year}(進行中)` : String(r.year)), el("span", { className: "sub" }, r.season === "career" ? "" : `${r.age}歳 ${r.team_name} ${r.position}`)],
      rowClass: (r) => (r.season === "career" ? "career" : ""),
    }),
  );
}
