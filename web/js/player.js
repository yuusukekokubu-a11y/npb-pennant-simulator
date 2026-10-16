// 選手のページ(能力・成績・試合ごと・契約・年度別)(web/app.js から分けた。保守②。D-291)

import { $, GAMES_PAGE, state } from "./core.js";
import { query } from "./backend.js";
import { openPage, renderCurrent } from "./screens.js";
import { abilityPanel, conditionMark, el, emblem, kvTable, link, setPressed, table, teamLink } from "./parts.js";
import { seasonCaution } from "./glossary.js";

export async function renderPlayer(args, token) {
  const data = await query("player", { player_id: args.id });
  if (token !== state.token) return;
  const p = data.player;
  $("player-name").replaceChildren(p.name, p.condition ? conditionMark(p.condition) : ""); // 今日の調子(D-424)
  // 選手のカード(D-400):マーク・背景にポジションの 1 文字
  $("player-emblem").replaceWith(Object.assign(emblem(p.team_id, p.team_name || "?", "large"), { id: "player-emblem" }));
  $("player-card").dataset.pos = String(p.position_label || "").slice(0, 1);
  $("player-info").replaceChildren(
    teamLink(p.team_name, p.team_id),
    p.is_mine ? " ★" : "",
    ` / ${p.position_label} / ${p.age}歳${p.hand ? ` / ${p.hand}` : ""}${p.foreign ? " / 外国人" : ""}${p.development ? ` / ${p.development.label}` : ""}${p.retired ? "(引退)" : ""}`,
  );
  renderPlayerHistory(data);
  renderPlayerContract(data);
  renderPlayerAbilities(data);
  if (state.playerKind === "ability") state.playerKind = "basic"; // 能力は上に常に出すので、成績の切り替えからは外した(作り直し①)
  setPressed("player-kind", state.playerKind);
  const box = $("player-season");
  $("player-baseline").hidden = true;
  if (!data.season) {
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

// 能力(作り直し①。D-335、D-347):野手は弾道・ミート・パワー・走力・肩力・守備力・捕球、投手は球速・コントロール・スタミナ・変化球
function renderPlayerAbilities(data) {
  const items = data.player.abilities;
  const box = $("player-abilities-box");
  renderEntryEstimate(data);
  box.hidden = !items;
  if (!items) {
    $("player-abilities").replaceChildren();
    return;
  }
  $("player-abilities").replaceChildren(abilityPanel(items, data.player.skills || [], { skillsId: "player-skills" }));
}

// 入団時の見立て(D-420):ドラフト・育成ドラフト・外国人市場で入った選手だけ。見立てと今の数字を並べ、差を出す
function renderEntryEstimate(data) {
  const e = data.player.entry_estimate;
  const box = $("player-entry-box");
  box.hidden = !e;
  if (!e) {
    box.replaceChildren();
    return;
  }
  const now = Object.fromEntries((data.player.abilities || []).map((a) => [a.key, a]));
  const sign = (n) => (n > 0 ? `+${n}` : String(n));
  const rows = e.abilities.map((a) => {
    const b = now[a.key];
    const diff = b ? b.value - a.value : null;
    return { ...a, values: { est: a.text, now: b ? b.text : "—", diff: diff === null ? "—" : diff === 0 ? "±0" : sign(diff) } };
  });
  const names = (list) => (list === null ? "記録なし" : list.length ? list.map((x) => x.label).join("・") : "なし");
  rows.push({ key: "skills", label: "特殊能力", values: { est: names(e.skills), now: names(data.player.skills || []), diff: "" } });
  box.replaceChildren(
    el("details", { id: "player-entry" },
      el("summary", {}, `入団時の見立て(${e.year}シーズン目・${e.route_label}・総合 ${e.overall_text})`),
      table({ firstLabel: "能力", columns: [{ key: "est", label: "見立て" }, { key: "now", label: "今の能力" }, { key: "diff", label: "差" }], rows, first: (r) => [el("span", {}, r.label)], fluid: true })),
  );
}

// 契約(F3-2a。D-232):年俸・契約年数・残り年数・年俸の履歴(公開情報。他球団の選手も)
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
}

// 年度別の成績(過去シーズン・今シーズン・通算。F2。D-182)。種類の切り替え(基本・セイバー・WAR)は上の表と同じ
function renderPlayerHistory(data) {
  const h = data.history;
  const box = $("player-history-box");
  if (!h || !h.rows.length) {
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
