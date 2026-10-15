// 進行・順位表・進める(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { call, delayedShow, ready } from "./backend.js";
import { current, renderCurrent, showScreen } from "./screens.js";
import { el, emblem, gameCard, rankBadge, teamLink } from "./parts.js";
import { buildStatsFilters } from "./stats.js";

// ---- 進行・順位表 ----

export function setDirty(value) {
  state.dirty = value;
  $("dirty").hidden = !value;
  $("save").classList.toggle("unsaved", value); // 未保存は保存のアイコンに点(D-401)
}

const recText = (r) => `${r.wins}勝${r.losses}敗${r.ties}分`;

// 得点板(D-400):自球団 VS 次の相手(シーズンが終わったら最後の相手)
function renderBoard(s) {
  const m = s.my_team;
  const n = m && m.next_game;
  $("vs").hidden = !n;
  $("board-note").textContent = n ? `${n.finished ? "最後の試合" : `次 ${n.day}日目`}・${n.home ? "ホーム" : "ビジター"}` : "";
  if (n) {
    const side = (id, name, rec) => el("div", { className: "vs-team" }, emblem(id, name, "large"), el("span", { className: "vs-name" }, name), el("span", { className: "vs-rec" }, recText(rec)));
    $("vs").replaceChildren(side(m.id, m.name, m), el("span", { className: "vs-x" }, "VS"), side(n.opponent_id, n.opponent_name, n.opponent_record));
  }
  const done = s.is_over ? 1 : s.day / s.total_days;
  $("season-bar").style.width = `${Math.round(done * 1000) / 10}%`;
}

// 自球団の順位・勝率・ゲーム差と調子(D-400)。首位のときは 2 位との差
function renderForm(m) {
  const rows = (state.view.standings.leagues.find((lg) => lg.rows.some((r) => r.is_mine)) || { rows: [] }).rows;
  const second = rows.find((r) => r.rank === 2);
  const lead = m.rank === 1 && second && second.games_behind !== "-" ? second.games_behind : null;
  const item = (label, value) => el("div", { className: "form-item" }, el("span", {}, label), el("b", {}, value));
  $("mine-form").replaceChildren(
    item(`${m.league_name}`, rankBadge(m.rank)),
    item("勝率", m.pct),
    m.rank === 1 ? item("2位との差", lead || "-") : item("首位との差", m.games_behind),
  );
  const note = [];
  if (m.streak && m.streak.count >= 2) note.push(el("span", { className: "streak" + (m.streak.outcome === "負" ? " lose" : "") }, `${m.streak.count}連${m.streak.outcome === "勝" ? "勝" : "敗"}中`));
  note.push(el("span", {}, `${m.wins}勝 ${m.losses}敗 ${m.ties}分`));
  if (m.last10 && m.last10.games) note.push(el("span", {}, `直近${m.last10.games}試合 ${m.last10.wins}勝${m.last10.losses}敗${m.last10.ties ? `${m.last10.ties}分` : ""}`));
  $("mine-note").replaceChildren(...note);
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
  $("open-lineup").hidden = !m || s.is_over || Boolean(off);
  const tr = s.trade;  // トレード(D-378):シーズン中の期限まで。提案があれば件数を出す
  $("open-trade").hidden = !m || !tr || !tr.open;
  if (tr) $("open-trade").textContent = tr.offers ? `トレード ${tr.offers}` : "トレード";
  renderBoard(s);
  if (m) renderForm(m);
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
  $("open-top-label").setAttribute("aria-disabled", String(state.running || !ready));
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
  $("standings-day").textContent = `${table.day}日目まで(全${state.view.status.total_days}日)・差はゲーム差`;
  const body = $("standings-body");
  body.replaceChildren();
  for (const r of lg.rows) {
    const cell = el("span", { className: "team-cell" }, rankBadge(r.rank), emblem(r.team_id, r.name), teamLink(r.name, r.team_id));
    if (r.is_mine) cell.append(el("span", { className: "you", "aria-label": "自球団" }, "★"));
    const name = el("td", { className: "sticky" }, cell);
    const tr = el("tr", { className: r.is_mine ? "mine" : "" }, name);
    for (const v of [r.games, r.wins, r.losses, r.ties, r.pct, r.games_behind]) tr.append(el("td", {}, String(v)));
    body.appendChild(tr);
  }
}

export function enterGame(view, dirty) {
  state.view = view;
  state.cache.clear();
  state.pages = [];
  state.tab = "progress";
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
