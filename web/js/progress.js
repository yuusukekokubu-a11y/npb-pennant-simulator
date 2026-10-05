// 進行・順位表・進める(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { call, delayedShow, ready } from "./backend.js";
import { current, renderCurrent, showScreen } from "./screens.js";
import { el, gameCard, teamLink } from "./parts.js";
import { buildStatsFilters } from "./stats.js";

// ---- 進行・順位表 ----

export function setDirty(value) {
  state.dirty = value;
  $("dirty").hidden = !value;
}

function recordText(m) {
  const gb = m.games_behind === "-" ? (m.rank === 1 ? "首位" : "首位と同率") : `首位と ${m.games_behind} ゲーム差`;
  return `${m.league_name} ${m.rank}位 / ${m.wins}勝 ${m.losses}敗 ${m.ties}分 / 勝率 ${m.pct} / ${gb}`;
}

function seasonWord(s) {
  return `${s.year}シーズン目`;
}

export function renderProgress() {
  const s = state.view.status;
  $("day-text").textContent = `${seasonWord(s)} ` + (s.is_over ? `全${s.total_days}日 終了` : `${s.day}日目 / ${s.total_days}日`);
  $("topbar-day").textContent = `${seasonWord(s)} ` + (s.is_over ? "シーズン終了" : `${s.day}日目 / ${s.total_days}日`);
  $("year-end-box").hidden = !s.can_year_end;
  $("year-end").disabled = state.running;
  const off = s.offseason;
  $("offseason-box").hidden = !off;
  if (off) $("offseason-box-text").textContent = `${off.year}シーズン目のオフの手続き(今の段階:${off.phase_label})`;
  $("games-text").textContent = `${s.games_played} / ${s.total_games} 試合`;
  const m = s.my_team;
  $("mine-card").hidden = !m;
  if (m) {
    $("mine-name").textContent = m.name;
    $("mine-record").textContent = recordText(m);
  }
  const last = state.view.last_day;
  $("last-day-title").textContent = last.games.length ? `直近の日の試合(${last.day}日目)` : "直近の日の試合";
  $("last-day").replaceChildren(...last.games.map(gameCard));
  $("last-day-empty").hidden = last.games.length > 0;
  const ul = $("recent");
  ul.replaceChildren();
  for (const g of state.view.recent) {
    const mark = el("span", { className: "mark " + (g.outcome === "勝" ? "win" : g.outcome === "負" ? "loss" : "") }, g.outcome === "勝" ? "○" : g.outcome === "負" ? "●" : "△");
    const extra = [g.innings > 9 ? `延長${g.innings}回` : "", g.walkoff ? (g.outcome === "勝" ? "サヨナラ勝ち" : "サヨナラ負け") : ""].filter(Boolean).join("・");
    const text = el("span", {}, `${g.day}日目 ${g.home ? "(ホーム)" : "(ビジター)"} 対 ${g.opponent} ${g.score}${extra ? `(${extra})` : ""}`);
    ul.appendChild(el("li", {}, mark, text));
  }
  $("recent-empty").hidden = state.view.recent.length > 0;
  for (const b of document.querySelectorAll(".adv")) b.disabled = state.running || s.is_over;
  $("save").disabled = state.running;
  $("open-file-top").disabled = state.running || !ready;
}

export function renderStandings() {
  const table = state.view.standings;
  const sw = $("league-switch");
  if (sw.children.length !== table.leagues.length) {
    sw.replaceChildren();
    for (const lg of table.leagues) {
      sw.appendChild(
        el("button", { type: "button", onclick: () => { state.league = lg.index; renderStandings(); } }, lg.name),
      );
    }
  }
  [...sw.children].forEach((b, i) => {
    b.textContent = table.leagues[i].name;
    b.setAttribute("aria-pressed", String(i === state.league));
  });
  const lg = table.leagues[state.league];
  $("standings-day").textContent = `${table.day}日目まで(全${state.view.status.total_days}日)`;
  const body = $("standings-body");
  body.replaceChildren();
  for (const r of lg.rows) {
    const name = el("td", { className: "sticky" }, el("span", { className: "rank" }, String(r.rank)), teamLink(r.name, r.team_id));
    if (r.is_mine) name.append(el("span", { className: "you", "aria-label": "自球団" }, "★"));
    const tr = el("tr", { className: r.is_mine ? "mine" : "" }, name);
    for (const v of [r.games, r.wins, r.losses, r.ties, r.pct, r.games_behind]) tr.append(el("td", {}, String(v)));
    body.appendChild(tr);
  }
}

export function enterGame(view, dirty) {
  state.view = view;
  state.cache.clear();
  state.pages = [];
  state.gamesDay = null;
  state.stats.league = "";
  state.stats.team = "";
  setDirty(dirty);
  const mine = view.status.my_team;
  if (mine) state.league = view.standings.leagues.findIndex((lg) => lg.rows.some((r) => r.is_mine));
  if (state.league < 0) state.league = 0;
  $("progress-message").textContent = "";
  buildStatsFilters();
  showScreen("progress");
  renderCurrent();
}

// ---- 進める ----

export async function advance(days) {
  if (state.running || !state.view || state.view.status.is_over) return;
  state.running = true;
  state.stopRequested = false;
  $("stop").disabled = false;
  $("progress-message").className = "message";
  $("progress-message").textContent = "";
  renderProgress();
  const start = state.view.status.day;
  const total = days === 0 ? state.view.status.total_days - start : Math.min(days, state.view.status.total_days - start);
  const showRun = () => {
    $("run-progress").max = total;
    $("run-box").hidden = false;
  };
  // 「止める」と進み具合のバーは、0.6 秒を超えたときだけ出す。「1日」では出さない(D-118)
  const hideRun = days === 1 ? async () => {} : delayedShow(showRun, () => ($("run-box").hidden = true));
  let done = 0;
  try {
    // 1日ずつ裏に頼む。日の区切りごとに、止める指示を確かめる
    while (done < total && !state.view.status.is_over && !state.stopRequested) {
      const r = await call("advance", { days: 1 });
      state.view = r.value;
      setDirty(true);
      done += 1;
      $("run-progress").value = done;
      $("run-text").textContent = `${done} / ${total} 日分を進めました(${state.view.status.day}日目)`;
      renderProgress();
    }
    const s = state.view.status;
    if (s.is_over) {
      const m = s.my_team;
      $("progress-message").textContent = m
        ? `シーズンが終わりました。${m.name} は ${m.league_name} ${m.rank}位でした。`
        : "シーズンが終わりました。";
    } else if (state.stopRequested) {
      $("progress-message").textContent = `${s.day}日目の終わりで止めました。`;
    }
  } catch (err) {
    $("progress-message").className = "message ng";
    $("progress-message").textContent = `進められませんでした:${err.message}`;
  } finally {
    await hideRun();
    state.running = false;
    renderProgress();
    if (current().name !== "progress") renderCurrent(); // ほかの画面を見ていたら、新しい日の内容にする
  }
}
