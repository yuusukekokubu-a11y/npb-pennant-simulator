// 画面の動き(最小のブラウザ画面①②。D-107、D-114)。計算は worker.js(裏の Python)に任せ、ここは表示と操作だけを行う。
// ブラウザの保存領域(localStorage・sessionStorage・IndexedDB・Cookie)には何も書かない。自動保存もしない。
// 能力は公開の情報として query から受け取る(答え合わせモードはなくした。作り直し①。D-338)。
// 入口(保守②。D-291):閉じる前の警告・ボタンのつなぎ・起動。画面ごとの処理は js/ の各ファイルにある(一覧は web/README.md)。

import { $, state } from "./js/core.js";
import { boot } from "./js/backend.js";
import { back, buildTabs, current, openPage, renderCurrent, showScreen, showTab } from "./js/screens.js";
import { openEntries } from "./js/records.js";
import { advance } from "./js/progress.js";
import { procAuto, procNext, procStageAuto } from "./js/procedure.js";
import { yearEnd } from "./js/yearend.js";
import { chooseNewGame, leagueSeedChanged, newGameStep, nextNewGame, parseSeed, showNewGame, startNewGame, teamInputs } from "./js/newgame.js";
import { openFile, save } from "./js/files.js";
import { busyButton, setLoadingStyle } from "./js/loading.js";

// ---- 閉じる前の警告(未保存の変更があるときだけ) ----

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

// ---- ボタン ----

buildTabs();
$("go-new").addEventListener("click", () => busyButton($("go-new"), showNewGame()).catch((err) => ($("start-message").textContent = err.message))); // 押した瞬間に「準備中」(D-411)
$("new-back").addEventListener("click", () => newGameStep("choice")); // 名前の段の「戻る」は最初の 2 つのボタンの段へ(D-464)
$("new-use-template").addEventListener("click", () => chooseNewGame(true).catch((err) => ($("new-message").textContent = err.message)));
$("new-build").addEventListener("click", () => chooseNewGame(false).catch((err) => ($("new-message").textContent = err.message)));
$("new-choice-back").addEventListener("click", () => {
  for (const input of teamInputs()) input.value = "";
  state.newGame = null;
  if (state.view) {
    showScreen(current().name);
    renderCurrent();
  } else {
    showScreen("start");
  }
});
$("new-start").addEventListener("click", startNewGame);
$("new-next").addEventListener("click", nextNewGame); // 設定の段へ(D-448)
$("new-settings-back").addEventListener("click", () => newGameStep(false));
$("league-seed").addEventListener("change", leagueSeedChanged);
$("season-seed").addEventListener("change", () => ($("season-seed-error").textContent = parseSeed("season-seed").error || ""));
for (const b of document.querySelectorAll(".adv")) b.addEventListener("click", () => advance(Number(b.dataset.days)));
$("stop").addEventListener("click", () => {
  state.stopRequested = true;
  $("stop").disabled = true;
  $("progress-message").textContent = "この日の試合が終わったら止めます…";
});
$("save").addEventListener("click", save);
$("year-end").addEventListener("click", () => openPage("yearend"));
$("open-offseason").addEventListener("click", () => openPage("procedure"));
$("open-review").addEventListener("click", () => { openEntries(); showTab("records"); }); // 記録室の補強の記録(D-423)
$("open-lineup").addEventListener("click", () => { state.lineup = null; openPage("lineup"); });
$("open-roster").addEventListener("click", () => { state.roster = null; openPage("roster"); }); // 一軍登録(D-425)
$("open-trade").addEventListener("click", () => { state.trade = null; openPage("trade"); });
$("team-review").addEventListener("click", () => { openEntries(current().args.id); showTab("records"); });
$("proc-next").addEventListener("click", procNext);
$("proc-stage-auto").addEventListener("click", procStageAuto);
$("proc-auto").addEventListener("click", procAuto);
$("yearend-save").addEventListener("click", save);
$("yearend-go").addEventListener("click", yearEnd);
$("open-file-start").addEventListener("change", (e) => openFile(e, "start-message"));
$("open-file-top").addEventListener("change", (e) => openFile(e, "progress-message"));
$("open-file-start").disabled = true;
$("open-file-top").disabled = true;
$("open-guide").addEventListener("click", () => openPage("guide"));
$("menu").addEventListener("click", () => {
  if (current().name !== "settings") openPage("settings");
});
for (const b of document.querySelectorAll(".back")) b.addEventListener("click", back);
for (const b of $("loading-style").children) b.addEventListener("click", () => setLoadingStyle(b.dataset.value)); // ロード画面の形(D-407)

// 成績(打撃・守備・投球。表示・絞り込み・並びは表の上の帯。stats.js。D-482)
for (const b of $("stats-role").children) {
  b.addEventListener("click", () => {
    // 打者と投手を切り替えたときは、その側の既定の並び順に戻す(絞り込みと表示件数は保つ。D-131)
    state.stats.role = b.dataset.value;
    state.sortBy[state.stats.role] = { key: null, order: null };
    state.shownSort[state.stats.role] = null;
    renderCurrent();
  });
}
// 試合
$("games-day").addEventListener("change", () => {
  state.gamesDay = Number($("games-day").value);
  renderCurrent();
});
$("games-prev").addEventListener("click", () => {
  state.gamesDay = state.gamesPrev ?? state.gamesDay - 1; // 休みの日を飛ばす(D-435)
  renderCurrent();
});
$("games-next").addEventListener("click", () => {
  state.gamesDay = state.gamesNext ?? state.gamesDay + 1;
  renderCurrent();
});

// 選手のページ
for (const b of $("player-kind").children) {
  b.addEventListener("click", () => {
    state.playerKind = b.dataset.value;
    renderCurrent();
  });
}


boot();
