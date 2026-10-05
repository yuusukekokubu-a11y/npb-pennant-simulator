// 試合(日付ごとの一覧と、試合のページ)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { query } from "./backend.js";
import { openPage } from "./screens.js";
import { el, gameCard, link, playerLink, table, teamLink } from "./parts.js";

// ---- 試合(日付ごとの一覧) ----

export async function renderGames(token) {
  const last = state.view.status.day;
  const sel = $("games-day");
  if (sel.options.length !== last) {
    sel.replaceChildren(...Array.from({ length: last }, (_, i) => el("option", { value: String(i + 1) }, `${i + 1}日目`)));
  }
  if (state.gamesDay === null || state.gamesDay > last) state.gamesDay = last;
  $("games-empty").hidden = last > 0;
  $("games-prev").disabled = state.gamesDay <= 1;
  $("games-next").disabled = state.gamesDay >= last;
  if (last === 0) {
    $("games-list").replaceChildren();
    return;
  }
  sel.value = String(state.gamesDay);
  const data = await query("games_on", { day: state.gamesDay });
  if (token !== state.token) return;
  $("games-list").replaceChildren(...data.games.map(gameCard));
}

// ---- 試合のページ(文章ログと投手の成績) ----

export async function renderGame(args, token) {
  const d = await query("game", { game_no: args.game_no });
  if (token !== state.token) return;
  const s = d.summary;
  const st = d.story;
  $("game-title").textContent = `${s.day}日目 ${s.away.name} ${s.away.runs} - ${s.home.runs} ${s.home.name}`;
  $("game-tags").textContent = [s.innings > 9 ? `延長${s.innings}回` : "", ...st.tags.filter((t) => t !== "延長")].filter(Boolean).join("・");
  $("game-stadium").replaceChildren("球場:", link(s.stadium, () => openPage("stadium", { id: s.home.team_id })), `(${s.home.name}の本拠地)`);
  const lineCols = [...st.line.innings.map((i) => ({ key: i, label: i })), { key: "total", label: "計" }];
  $("game-line").replaceChildren(
    table({
      firstLabel: "チーム",
      columns: lineCols,
      rows: st.line.rows.map((r) => ({ ...r, values: { ...Object.fromEntries(st.line.innings.map((i, k) => [i, r.cells[k]])), total: String(r.total) } })),
      first: (r) => [teamLink(r.name, r.team_id)],
    }),
    el("p", { className: "muted small" }, "イニングごとの得点。X は、後攻のチームが勝っていて、9回裏などの攻撃をしなかったこと。"),
  );
  const pcols = [
    { key: "decision", label: "結果" },
    { key: "role", label: "区分" },
    { key: "innings", label: "投球回" },
    { key: "batters_faced", label: "対戦打者" },
    { key: "hits", label: "被安打" },
    { key: "runs", label: "失点" },
    { key: "earned_runs", label: "自責点" },
    { key: "exit_reason", label: "降板の理由" },
  ];
  $("game-pitchers").replaceChildren(
    table({
      firstLabel: "投手",
      columns: pcols,
      rows: st.pitchers.map((p) => ({ ...p, values: Object.fromEntries(pcols.map((c) => [c.key, String(p[c.key] ?? "")])) })),
      first: (p) => [playerLink(p.name, p.id), el("span", { className: "sub" }, p.team)],
    }),
  );
  $("game-lineups").replaceChildren(
    ...st.teams.map((t) =>
      el(
        "div",
        {},
        el("h3", {}, `${t.name}(${t.side})`),
        el("p", {}, "先発投手:", playerLink(t.starter.name, t.starter.id)),
        el("ol", {}, ...t.lineup.map((x) => el("li", {}, playerLink(x.name, x.id), `(${x.position})${x.rest_sub ? "※休養の代わり" : ""}`))),
      ),
    ),
  );
  const away = st.away.name;
  const home = st.home.name;
  $("game-log").replaceChildren(
    ...st.halves.map((h) =>
      el(
        "section",
        {},
        el("h3", {}, `${h.title}(${h.batting_team}の攻撃)`),
        el(
          "ul",
          {},
          ...h.events.map((e) => {
            if (e.type === "pitching_change") return el("li", { className: "change" }, `【投手交代】${e.team}:${e.pitcher}`);
            const extra = e.runs ? el("span", { className: "runs" }, ` → ${e.runs}点(スコア ${away} ${e.score.away} - ${e.score.home} ${home})`) : null;
            return el("li", { className: "pa" }, `[${e.situation}] ${e.order}番 ${e.batter}:${e.result}`, extra);
          }),
        ),
      ),
    ),
  );
}
