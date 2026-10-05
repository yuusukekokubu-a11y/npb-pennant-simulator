// 画面の切り替え(タブ・重ねて開くページ・戻る・今の画面の描き直し)(web/app.js から分けた。保守②。D-291)

import { $, SCREENS, TABS, state } from "./core.js";
import { el } from "./parts.js";
import { renderProgress, renderStandings } from "./progress.js";
import { renderStats } from "./stats.js";
import { renderGame, renderGames } from "./games.js";
import { renderPlayer } from "./player.js";
import { renderReview } from "./review.js";
import { renderProcedure } from "./procedure.js";
import { renderOffseason, renderYearEnd } from "./yearend.js";
import { renderStadium, renderTeam } from "./team.js";
import { renderGuide, renderSettings } from "./menu.js";

// ---- 画面の切り替え ----

export function showScreen(name) {
  for (const id of SCREENS) $(`screen-${id}`).hidden = id !== name;
  if (name !== "new") $("new-help").hidden = true; // 新規開始の説明バーは、設定の画面から離れると消す(D-313)
  const inGame = !["start", "new"].includes(name);
  $("topbar").hidden = !inGame;
  $("tabs").hidden = !inGame;
  if (TABS.some((t) => t.id === name)) state.tab = name;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
  window.scrollTo(0, 0);
}

export function current() {
  return state.pages.length ? state.pages[state.pages.length - 1] : { name: state.tab, args: {} };
}

export function showTab(name) {
  state.pages = [];
  showScreen(name);
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
  }[name];
  Promise.resolve(job && job()).catch((err) => showError(name, err));
}

function showError(name, err) {
  const box = { stats: "stats-table", games: "games-list", player: "player-season", team: "team-record", game: "game-log", yearend: "yearend-message", offseason: "offseason-retired", procedure: "proc-message", review: "review-table" }[name];
  if (box) $(box).replaceChildren(el("p", { className: "message ng" }, `表示できませんでした:${err.message}`));
}

export function buildTabs() {
  for (const t of TABS) {
    const b = document.createElement("button");
    b.textContent = t.label;
    b.dataset.tab = t.id;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => showTab(t.id));
    $("tabs").appendChild(b);
  }
}
