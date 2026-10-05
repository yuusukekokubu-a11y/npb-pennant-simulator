// チームのページと球場のページ(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { answer, query } from "./backend.js";
import { openPage, renderCurrent } from "./screens.js";
import { el, kvTable, link, playerLink, table, teamLink } from "./parts.js";
import { seasonCaution } from "./glossary.js";

// ---- チームのページ ----

export async function renderTeam(args, token) {
  const d = await query("team", { team_id: args.id });
  if (token !== state.token) return;
  $("team-name").textContent = d.name + (d.is_mine ? " ★" : "");
  $("team-info").replaceChildren(`${d.league_name} ${d.rank}位 / 本拠地:`, link(d.stadium, () => openPage("stadium", { id: d.team_id })));
  const r = d.record;
  $("team-record").replaceChildren(
    kvTable([
      { label: "試合", values: [String(r.games)] },
      { label: "勝・敗・分", values: [`${r.wins}勝 ${r.losses}敗 ${r.ties}分`] },
      { label: "勝率", values: [r.pct] },
      { label: "ゲーム差", values: [r.games_behind] },
      { label: "得点", values: [String(r.runs)] },
      { label: "失点", values: [String(r.runs_allowed)] },
    ]),
  );
  const w = d.war;
  $("team-war").replaceChildren(
    kvTable([
      { label: "野手の WAR", values: [w.batters] },
      { label: "投手の WAR(失点版)", values: [w.pitchers_ra] },
      { label: "投手の WAR(FIP 版)", values: [w.pitchers_fip] },
      { label: "合計(野手 + 投手の失点版)", values: [w.total_ra] },
    ]),
  );
  $("team-war-note").textContent = seasonCaution("current");
  $("team-budget").replaceChildren(...budgetLines(d.budget));
  $("team-salaries").replaceChildren(
    d.salaries.length
      ? table({ firstLabel: "選手", columns: [{ key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "salary", label: "年俸(万円)" }, { key: "remaining", label: "残り" }], rows: d.salaries.map((r) => ({ ...r, values: { position: r.position, age: `${r.age}歳`, salary: r.salary_text, remaining: `${r.remaining} 年` } })), first: (r) => [playerLink(r.name, r.player_id)], fluid: true, limit: d.salariesShown || 20, more: () => { d.salariesShown = (d.salariesShown || 20) + 50; renderCurrent(); } })
      : el("p", { className: "muted" }, "契約の情報がありません。"),
  );
  const cols = [
    { key: "position", label: "ポジション" },
    { key: "age", label: "年齢" },
    { key: "hand", label: "投打" },
    { key: "games", label: "出場" },
  ];
  const rows = (role) => d.players.filter((p) => p.role === role).map((p) => ({ ...p, values: { position: p.position, age: String(p.age), hand: p.hand || "", games: String(p.games) } }));
  const first = (p) => [playerLink(p.name, p.player_id)];
  $("team-pitchers").replaceChildren(table({ firstLabel: "選手", columns: cols, rows: rows("pitcher"), first }));
  $("team-batters").replaceChildren(table({ firstLabel: "選手", columns: cols, rows: rows("batter"), first }));
}

// 総年俸と予算の表示(F3-2a。D-231):なしは総年俸だけ、ゆるいは目安、標準・きびしいは上限
function budgetLines(b) {
  const lines = [el("div", {}, el("span", { className: "big" }, `総年俸 ${b.total_text}`), b.tier_label ? el("span", { className: "sub" }, `予算の格差:${b.tier_label}`) : null)];
  if (b.cap) {
    const over = b.over > 0;
    lines.push(
      el("div", {}, `${b.hard ? "予算の上限" : "予算の目安"} ${b.cap_text}(使用率 ${b.usage}%)`),
      el("div", { className: over ? "budget-bar over" : "budget-bar" }, el("span", { style: `width: ${Math.min(100, b.usage)}%` })),
      over ? el("div", { className: "warn" }, `${b.hard ? "上限" : "目安"}を ${b.over.toLocaleString()} 万円超えています${b.hard ? "" : "(ゆるいなので契約は結べます)"}`) : null,
    );
  }
  lines.push(el("dl", {}, el("dt", {}, `お金のルール:${b.rule_label}`), el("dd", {}, b.rate ? `年俸の単価:1 WAR あたり約 ${(b.rate / 10000).toFixed(2)} 億円` : "")));
  return lines.filter((x) => x !== null);
}

// ---- 球場のページ(公開用の結果と、答え合わせモードでの真の倍率。D-138) ----

export async function renderStadium(args, token) {
  const d = await query("stadium", { team_id: args.id });
  if (token !== state.token) return;
  $("stadium-name").textContent = d.name;
  $("stadium-info").replaceChildren("本拠地のチーム:", teamLink(d.team_name, d.team_id), d.is_mine ? " ★" : "", ` / ${d.league_name}`);
  $("stadium-record").replaceChildren(
    kvTable([
      { label: "試合", values: [String(d.games)] },
      { label: "本塁打", values: [String(d.home_runs)] },
      { label: "本塁打/試合", values: [d.home_runs_per_game] },
      { label: "得点/試合", values: [d.runs_per_game] },
    ]),
  );
  const cmpCols = [
    { key: "home", label: "本拠地" },
    { key: "away", label: "アウェイ" },
    { key: "ratio", label: "比" },
  ];
  $("stadium-compare").replaceChildren(
    table({
      firstLabel: "項目",
      columns: cmpCols,
      rows: ["home_run", "babip", "runs"].map((k) => ({ key: k, label: k === "home_run" ? "本塁打/打席" : k === "runs" ? "得点/打席" : "BABIP", values: d.this_season[k] })),
      first: (r) => [r.label],
    }),
  );
  if (d.estimate) {
    $("stadium-estimate").replaceChildren(
      kvTable([
        { label: "得点", values: [d.estimate.runs] },
        { label: "本塁打", values: [d.estimate.home_run] },
        { label: "BABIP(参考値)", values: [d.estimate.babip] },
      ]),
    );
  } else {
    $("stadium-estimate").replaceChildren(el("p", { className: "muted small" }, "まだ推定できません(2 シーズン目から)。"));
  }
  const box = $("stadium-answers");
  if (state.answerLevel === 0) {
    box.replaceChildren(el("p", { className: "info" }, "答え合わせモードをオンにすると見られます(上の「メニュー」から)。"));
    return;
  }
  const a = await answer("stadium_answers", { team_id: args.id });
  if (token !== state.token || state.answerLevel === 0) return;
  box.replaceChildren(
    kvTable([
      { label: "本塁打の倍率", values: [a.home_run.text] },
      { label: "BABIP の倍率", values: [a.babip.text] },
    ]),
  );
}
