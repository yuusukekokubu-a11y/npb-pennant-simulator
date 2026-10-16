// 画面の切り替え(タブ・重ねて開くページ・戻る・今の画面の描き直し)(web/app.js から分けた。保守②。D-291)

import { $, DUAL_BASES, DUAL_PAGES, SCREENS, TABS, isWide, onWideChange, state, tabScreen } from "./core.js";
import { el } from "./parts.js";
import { renderProgress, renderStandings } from "./progress.js";
import { renderStats } from "./stats.js";
import { renderGame, renderGames } from "./games.js";
import { renderPlayer } from "./player.js";
import { renderReview } from "./review.js";
import { renderLineup } from "./lineup.js";
import { renderTrade } from "./trade.js";
import { renderProcedure } from "./procedure.js";
import { renderOffseason, renderYearEnd } from "./yearend.js";
import { renderStadium, renderTeam } from "./team.js";
import { renderGuide, renderSettings } from "./menu.js";

// ---- 画面の切り替え ----

export function showScreen(name) {
  const inGame = !["start", "new"].includes(name);
  if (TABS.some((t) => t.id === name && !t.screen)) state.tab = name;
  // 広い画面の 2 列(D-402):タブの画面を左に残し、開いたページを右に並べる。何も開いていなければ右に案内
  const base = tabScreen(state.tab);
  const top = state.pages.length ? state.pages[state.pages.length - 1].name : null;
  const dual = inGame && isWide() && DUAL_BASES.includes(base) && (top === null ? name === base : top === name && DUAL_PAGES.includes(name) && name !== base);
  for (const id of SCREENS) {
    const e = $(`screen-${id}`);
    e.hidden = !(id === name || (dual && id === base));
    e.classList.toggle("left-pane", dual && id === base);
    e.classList.toggle("right-pane", dual && id === name && id !== base);
  }
  $("side-hint").hidden = !(dual && top === null);
  document.body.classList.toggle("dual", dual);
  document.body.classList.toggle("in-game", inGame);
  if (name !== "new") $("new-help").hidden = true; // 新規開始の説明バーは、設定の画面から離れると消す(D-313)
  $("topbar").hidden = !inGame;
  $("tabs").hidden = !inGame;
  const mine = state.view && state.view.status.my_team;
  for (const b of $("tabs").children) {
    b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
    if (b.dataset.tab === "myteam") b.hidden = !mine; // 観戦のみのときは自球団のタブを出さない(D-401)
  }
  if (dual && top !== null) $(`screen-${name}`).scrollTop = 0; // 右のページだけ先頭へ(左の表の位置は保つ)
  else window.scrollTo(0, 0);
}

export function current() {
  if (state.pages.length) return state.pages[state.pages.length - 1];
  const mine = state.view && state.view.status.my_team;
  if (state.tab === "myteam" && mine) return { name: "team", args: { id: mine.id, root: true } };
  return { name: state.tab === "myteam" ? "progress" : state.tab, args: {} };
}

export function showTab(name) {
  state.pages = [];
  state.tab = name;
  showScreen(current().name);
  renderCurrent();
}

export function openPage(name, args = {}) {
  state.pages.push({ name, args });
  showScreen(name);
  renderCurrent();
}

export function back() {
  state.pages.pop();
  showScreen(current().name);
  renderCurrent();
}

// 幅が変わったら、今の画面の並べ方(1 列・2 列)を選び直す
onWideChange(() => {
  if (state.view && !$("topbar").hidden) showScreen(current().name);
});

export function renderCurrent() {
  if (!state.view) return;
  const { name, args } = current();
  const token = ++state.token;
  const job = {
    progress: () => renderProgress(),
    standings: () => renderStandings(),
    stats: () => renderStats(token),
    games: () => renderGames(token),
    player: () => renderPlayer(args, token),
    team: () => renderTeam(args, token),
    game: () => renderGame(args, token),
    settings: () => renderSettings(),
    guide: () => renderGuide(),
    stadium: () => renderStadium(args, token),
    yearend: () => renderYearEnd(token),
    offseason: () => renderOffseason(args, token),
    procedure: () => renderProcedure(token),
    review: () => renderReview(args, token),
    lineup: () => renderLineup(token),
    trade: () => renderTrade(token),
  }[name];
  Promise.resolve(job && job()).catch((err) => showError(name, err));
}

function showError(name, err) {
  const box = { stats: "stats-table", games: "games-list", player: "player-season", team: "team-record", game: "game-log", yearend: "yearend-message", offseason: "offseason-retired", procedure: "proc-message", review: "review-table", lineup: "lineup-message", trade: "trade-message" }[name];
  if (box) $(box).replaceChildren(el("p", { className: "message ng" }, `表示できませんでした:${err.message}`));
}

export function buildTabs() {
  for (const t of TABS) {
    const b = document.createElement("button");
    b.innerHTML = `<span class="tab-icon">${t.icon}</span>`;
    b.append(t.label);
    b.dataset.tab = t.id;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => showTab(t.id));
    $("tabs").appendChild(b);
  }
}
