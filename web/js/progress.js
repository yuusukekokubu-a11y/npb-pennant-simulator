// 進行・順位表・進める(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { call, delayedShow, ready } from "./backend.js";
import { current, renderCurrent, showScreen } from "./screens.js";
import { allstarCard, el, emblem, gameCard, playerLink, rankBadge, seriesCard, teamLink } from "./parts.js";
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

// 日の書き方:レギュラーシーズンは「N日目 / 全日数」、その後は「ポストシーズン N日目」(D-439)
export function dayWord(s) {
  if (s.is_over) return `全${s.total_days}日 終了` + (s.postseason && s.postseason.champion ? `・日本一 ${s.postseason.champion}` : "");
  if (s.regular_over) return s.day === s.total_days ? "レギュラーシーズン終了" : `ポストシーズン ${s.day - s.total_days}日目`;
  return `${s.day}日目 / ${s.total_days}日`;
}

export function renderProgress() {
  const s = state.view.status;
  $("day-text").textContent = `${seasonWord(s)} ${dayWord(s)}`;
  $("topbar-day").textContent = `${seasonWord(s)} ` + (s.is_over ? "シーズン終了" : s.regular_over ? (s.day === s.total_days ? "ポストシーズンへ" : `ポストシーズン ${s.day - s.total_days}日目`) : `${s.day}日目 / ${s.total_days}日`);
  $("year-end-box").hidden = !s.can_year_end;
  $("year-end").disabled = state.running;
  const off = s.offseason;
  $("offseason-box").hidden = !off;
  if (off) $("offseason-box-text").textContent = `${off.year}シーズン目のオフの手続き(今の段階:${off.phase_label})`;
  $("games-text").textContent = `${s.games_played} / ${s.total_games} 試合`;
  const m = s.my_team;
  $("mine-card").hidden = !m;
  $("open-lineup").hidden = !m || s.is_over || Boolean(off);
  $("open-roster").hidden = !m || s.is_over || Boolean(off);
  const tr = s.trade;  // トレード(D-378):シーズン中の期限まで。提案があれば件数を出す
  $("open-trade").hidden = !m || !tr || !tr.open;
  if (tr) $("open-trade").textContent = tr.offers ? `トレード ${tr.offers}` : "トレード";
  renderBoard(s);
  if (m) renderForm(m);
  const nd = s.next_day; // 次の日の種類(休みは「1日すすめる」で飛ばす。D-435)
  $("next-day-text").textContent = nd ? `次の日(${s.day + 1}日目)は${nd.label}${nd.kind === "off" || nd.kind === "allstar" ? "(「1日すすめる」で次の公式戦の日まで進みます)" : ""}` : "";
  const ps = s.postseason; // ポストシーズン(D-440):シリーズの勝敗と次の試合
  $("post-box").hidden = !ps;
  const inj = s.injuries; // 自球団のけが(D-445):直前の日に起きたけがと、今けがをしている選手
  $("injury-box").hidden = !inj || (!inj.new.length && !inj.current.length);
  if (inj) $("injury-list").replaceChildren(
    ...inj.new.map((x) => el("p", { className: "small injury-new" }, "けが:", playerLink(x.name, x.player_id), `(${x.where})${x.text}`)),
    ...(inj.current.length ? [el("p", { className: "small muted" }, `けがで離脱中 ${inj.current.length} 人:`, ...inj.current.flatMap((x, i) => [i ? "・" : "", playerLink(x.name, x.player_id), `(${x.text})`]))] : []),
  );
  if (ps) $("post-list").replaceChildren(...(ps.next_label ? [el("p", { className: "small" }, `次(${ps.next_day - s.total_days}日目):${ps.next_label}`)] : []), ...ps.series.map((x) => seriesCard(x, false)));
  $("allstar-box").hidden = !s.allstar.length;
  $("allstar-list").replaceChildren(...s.allstar.map(allstarCard));
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
  const il = table.interleague; // 交流戦の表(交流戦が始まってから。D-437)
  const ps = table.postseason; // ポストシーズンの組み合わせ表(D-440)
  const fm = table.farm; // 二軍の順位表(D-446)
  const tabs = [...table.leagues.map((lg) => ({ index: lg.index, name: lg.name })), ...(il ? [{ index: "il", name: "交流戦" }] : []), ...(ps ? [{ index: "ps", name: "ポストシーズン" }] : []), ...(fm ? [{ index: "farm", name: "二軍" }] : [])];
  if ((!il && state.league === "il") || (!ps && state.league === "ps") || (!fm && state.league === "farm")) state.league = 0;
  if (sw.children.length !== tabs.length) {
    sw.replaceChildren();
    for (const t of tabs) {
      sw.appendChild(
        el("button", { type: "button", onclick: () => { state.league = t.index; renderStandings(); } }, t.name),
      );
    }
  }
  [...sw.children].forEach((b, i) => {
    b.textContent = tabs[i].name;
    b.setAttribute("aria-pressed", String(tabs[i].index === state.league));
  });
  const post = state.league === "ps" || state.league === "farm";
  $("standings-post").hidden = !post;
  $("standings-table").hidden = post;
  if (state.league === "farm") {
    $("standings-day").textContent = `二軍の試合 ${fm.games} 試合・${fm.note}`;
    $("standings-champion").hidden = true;
    const head = el("tr", {}, ...["順位・チーム", "勝", "敗", "分", "勝率", "差"].map((x, i) => el("th", { scope: "col", className: i ? "" : "sticky" }, x)));
    $("standings-post").replaceChildren(...fm.leagues.map((lg) => el("section", { className: "farm-league" }, el("h2", {}, `${lg.league_name}(二軍)`),
      el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, head.cloneNode(true)), el("tbody", {}, ...lg.rows.map((r) =>
        el("tr", { className: r.mine ? "mine" : "" }, el("td", { className: "sticky" }, el("span", { className: "team-cell" }, rankBadge(r.rank), emblem(r.team_id, r.team_name), teamLink(r.team_name, r.team_id))),
          ...[r.wins, r.losses, r.ties, r.pct, r.games_behind].map((v) => el("td", {}, String(v)))))))))));
    return;
  }
  if (post) {
    $("standings-day").textContent = ps.champion ? `日本一:${ps.champion}` : "ポストシーズン(クライマックスシリーズ・日本シリーズ)";
    $("standings-champion").hidden = true;
    $("standings-post").replaceChildren(...ps.series.map((x) => seriesCard(x)));
    return;
  }
  const lg = state.league === "il" ? il : table.leagues[state.league];
  $("standings-day").textContent = `${Math.min(table.day, state.view.status.total_days)}日目まで(全${state.view.status.total_days}日)・差はゲーム差` + (state.league === "il" ? `・交流戦だけの成績(各球団 ${il.games} 試合)` : "");
  $("standings-champion").hidden = !(state.league === "il" && il.champion);
  if (state.league === "il" && il.champion) $("standings-champion").textContent = `交流戦の優勝:${il.champion}`;
  const body = $("standings-body");
  body.replaceChildren();
  for (const r of lg.rows) {
    const cell = el("span", { className: "team-cell" }, rankBadge(r.rank), emblem(r.team_id, r.name), teamLink(r.name, r.team_id));
    if (r.league_name) cell.append(el("span", { className: "sub" }, r.league_name));
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
  const st0 = state.view.status; // 「最後まで」はポストシーズンまで(日数は目安。D-440)
  const total = days === 0 ? Math.max(1, st0.total_days + st0.post_days_max - start) : days;
  const showRun = () => {
    $("run-progress").max = total;
    $("run-box").hidden = false;
  };
  // 「止める」と進み具合のバーは、0.6 秒を超えたときだけ出す。「1日」では出さない(D-118)
  const hideRun = days === 1 ? async () => {} : delayedShow(showRun, () => ($("run-box").hidden = true));
  let done = 0;
  try {
    // 1日ずつ裏に頼む(休みの日は飛ばして次の公式戦の日まで。D-435)。日の区切りごとに、止める指示を確かめる
    while (done < total && !state.view.status.is_over && !state.stopRequested) {
      const r = await call("advance", { days: 1 });
      state.view = r.value;
      setDirty(true);
      done = state.view.status.day - start;
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
