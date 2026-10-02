// 画面の動き(最小のブラウザ画面①。D-107)。計算は worker.js(裏の Python)に任せ、ここは表示と操作だけを行う。
// ブラウザの保存領域(localStorage・sessionStorage・IndexedDB・Cookie)には何も書かない。自動保存もしない。
// 選手の非公開の情報は扱わない(公開用の情報だけを使う。D-108)。

const LOCAL_PYODIDE = new URLSearchParams(location.search).get("pyodide") === "local"; // 試験用(このページに置いた Pyodide を使う)
const MAX_SEED = 4294967295;
// 下のタブ。増やすときは、ここに足して、同じ名前の画面(screen-…)を index.html に作る
const TABS = [
  { id: "progress", label: "進行" },
  { id: "standings", label: "順位表" },
];

const $ = (id) => document.getElementById(id);
let worker = null;
let ready = false;
let nextId = 1;
const pending = new Map();

const state = {
  view: null, // 今のゲームの表示用の情報(status・standings・recent)
  dirty: false, // 未保存の変更があるか
  running: false, // 進めている途中か
  stopRequested: false,
  league: 0, // 順位表で見ているリーグ
  tab: "progress",
  newGame: null, // 新規開始の画面の情報(シードと初期名)
};

// ---- 裏の Python とのやり取り ----

function createWorker() {
  // worker.js を、このページの通信制限(Content-Security-Policy)を引き継ぐ形で起動する
  const url = new URL("worker.js", location.href).href;
  const blob = new Blob([`import ${JSON.stringify(url)};`], { type: "text/javascript" });
  const blobUrl = URL.createObjectURL(blob);
  const w = new Worker(blobUrl, { type: "module" });
  return new Promise((resolve, reject) => {
    w.onmessage = (event) => {
      const msg = event.data;
      if (msg.ready) {
        URL.revokeObjectURL(blobUrl);
        resolve(w);
        return;
      }
      if (msg.progress) {
        bootProgress(msg.progress);
        return;
      }
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      msg.ok ? p.resolve(msg.value) : p.reject(new Error(msg.error));
    };
    w.onerror = (e) => reject(new Error(e.message || "裏の計算を起動できませんでした"));
  });
}

function call(cmd, args) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, cmd, args });
  });
}

function bootProgress({ step, total, text }) {
  $("boot-progress").max = total;
  $("boot-progress").value = step - 1;
  $("boot-text").textContent = `${step} / ${total}:${text}`;
}

async function boot() {
  try {
    worker = await createWorker();
    await call("boot", { local: LOCAL_PYODIDE });
    ready = true;
    $("boot-progress").value = $("boot-progress").max;
    $("boot").hidden = true;
    $("go-new").disabled = false;
    $("open-file-start").disabled = false;
    $("open-file-top").disabled = false;
    $("open-start-label").removeAttribute("aria-disabled");
  } catch (err) {
    $("boot-text").textContent = `準備できませんでした:${err.message}\n通信できる場所で、ページを読み込み直してください。`;
    $("boot-text").className = "message ng";
  }
}

// ---- 画面の切り替え ----

function showScreen(name) {
  for (const id of ["start", "new", "progress", "standings"]) $(`screen-${id}`).hidden = id !== name;
  const inGame = name === "progress" || name === "standings";
  $("topbar").hidden = !inGame;
  $("tabs").hidden = !inGame;
  if (inGame) {
    state.tab = name;
    for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  }
  window.scrollTo(0, 0);
}

function buildTabs() {
  for (const t of TABS) {
    const b = document.createElement("button");
    b.textContent = t.label;
    b.dataset.tab = t.id;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => {
      showScreen(t.id);
      render();
    });
    $("tabs").appendChild(b);
  }
}

// ---- 表示 ----

function setDirty(value) {
  state.dirty = value;
  $("dirty").hidden = !value;
}

function recordText(m) {
  const gb = m.games_behind === "-" ? (m.rank === 1 ? "首位" : "首位と同率") : `首位と ${m.games_behind} ゲーム差`;
  return `${m.league_name} ${m.rank}位 / ${m.wins}勝 ${m.losses}敗 ${m.ties}分 / 勝率 ${m.pct} / ${gb}`;
}

function renderProgress() {
  const s = state.view.status;
  $("day-text").textContent = s.is_over ? `全${s.total_days}日 終了` : `${s.day}日目 / ${s.total_days}日`;
  $("topbar-day").textContent = s.is_over ? "シーズン終了" : `${s.day}日目 / ${s.total_days}日`;
  $("day-progress").max = s.total_days;
  $("day-progress").value = s.day;
  $("games-text").textContent = `${s.games_played} / ${s.total_games} 試合`;
  const m = s.my_team;
  $("mine-card").hidden = !m;
  if (m) {
    $("mine-name").textContent = m.name;
    $("mine-record").textContent = recordText(m);
  }
  const ul = $("recent");
  ul.replaceChildren();
  for (const g of state.view.recent) {
    const li = document.createElement("li");
    const mark = document.createElement("span");
    mark.className = "mark " + (g.outcome === "勝" ? "win" : g.outcome === "負" ? "loss" : "");
    mark.textContent = g.outcome === "勝" ? "○" : g.outcome === "負" ? "●" : "△";
    const text = document.createElement("span");
    const extra = [g.innings > 9 ? `延長${g.innings}回` : "", g.walkoff ? (g.outcome === "勝" ? "サヨナラ勝ち" : "サヨナラ負け") : ""].filter(Boolean).join("・");
    text.textContent = `${g.day}日目 ${g.home ? "(ホーム)" : "(ビジター)"} 対 ${g.opponent} ${g.score}${extra ? `(${extra})` : ""}`;
    li.append(mark, text);
    ul.appendChild(li);
  }
  $("recent-empty").hidden = state.view.recent.length > 0;
  for (const b of document.querySelectorAll(".adv")) b.disabled = state.running || s.is_over;
  $("stop").hidden = !state.running;
  $("save").disabled = state.running;
  $("open-file-top").disabled = state.running || !ready;
}

function renderStandings() {
  const table = state.view.standings;
  const sw = $("league-switch");
  if (sw.children.length !== table.leagues.length) {
    sw.replaceChildren();
    for (const lg of table.leagues) {
      const b = document.createElement("button");
      b.textContent = lg.name;
      b.addEventListener("click", () => {
        state.league = lg.index;
        renderStandings();
      });
      sw.appendChild(b);
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
    const tr = document.createElement("tr");
    if (r.is_mine) tr.className = "mine";
    const name = document.createElement("td");
    name.className = "sticky";
    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = r.rank;
    name.append(rank, r.name);
    if (r.is_mine) {
      const you = document.createElement("span");
      you.className = "you";
      you.textContent = "★";
      you.setAttribute("aria-label", "自球団");
      name.append(you);
    }
    tr.appendChild(name);
    for (const v of [r.games, r.wins, r.losses, r.ties, r.pct, r.games_behind]) {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function render() {
  if (!state.view) return;
  renderProgress();
  renderStandings();
}

function enterGame(view, dirty) {
  state.view = view;
  setDirty(dirty);
  const mine = view.status.my_team;
  if (mine) state.league = view.standings.leagues.findIndex((lg) => lg.rows.some((r) => r.is_mine));
  if (state.league < 0) state.league = 0;
  $("progress-message").textContent = "";
  showScreen("progress");
  render();
}

// ---- 新規開始 ----

function randomSeed() {
  return crypto.getRandomValues(new Uint32Array(1))[0];
}

function parseSeed(id) {
  // 空欄なら null(毎回ちがう値)。正しくない値なら理由の文字
  const text = $(id).value.trim();
  if (text === "") return { value: null };
  if (!/^\d+$/.test(text)) return { error: "0 以上の整数を入れてください" };
  const n = Number(text);
  if (n > MAX_SEED) return { error: `${MAX_SEED} 以下にしてください` };
  return { value: n };
}

function teamInputs() {
  return [...document.querySelectorAll("#league-fields input[type=text]")];
}

function names() {
  return teamInputs().map((i) => i.value);
}

async function showNewGame() {
  $("new-message").textContent = "";
  $("league-seed").value = "";
  $("season-seed").value = "";
  $("league-seed-error").textContent = "";
  $("season-seed-error").textContent = "";
  await preparePreview(randomSeed(), true);
  showScreen("new");
}

async function preparePreview(seed, reset) {
  const r = await call("preview", { seed });
  const pv = r.value;
  // 入力済みの名前と自球団の選択は、作り直す直前の状態を引き継ぐ
  const prev = reset ? null : { names: names(), mine: myTeamIndex() };
  state.newGame = { seed, preview: pv };
  const box = $("league-fields");
  box.dataset.seed = String(seed);
  box.replaceChildren();
  let i = 0;
  for (const lg of pv.leagues) {
    const fs = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = lg.name;
    fs.appendChild(legend);
    for (const t of lg.teams) {
      const index = i++;
      const div = document.createElement("div");
      div.className = "team";
      const label = document.createElement("label");
      label.className = "name-label";
      label.htmlFor = `team-${index}`;
      label.textContent = `${index + 1}番目の球団(本拠地:${t.stadium})`;
      const input = document.createElement("input");
      input.type = "text";
      input.id = `team-${index}`;
      input.placeholder = t.default_name;
      input.autocomplete = "off";
      input.spellcheck = false;
      input.setAttribute("autocapitalize", "off");
      input.setAttribute("aria-describedby", `team-${index}-error`);
      input.value = prev ? prev.names[index] || "" : "";
      input.addEventListener("input", scheduleCheck);
      const err = document.createElement("p");
      err.className = "error";
      err.id = `team-${index}-error`;
      const pick = document.createElement("label");
      pick.className = "pick";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "my-team";
      radio.value = String(index);
      radio.checked = prev ? prev.mine === index : index === 0;
      pick.append(radio, "自球団にする");
      div.append(label, input, err, pick);
      fs.appendChild(div);
    }
    box.appendChild(fs);
  }
}

function myTeamIndex() {
  const r = document.querySelector("input[name=my-team]:checked");
  return r ? Number(r.value) : -1;
}

let checkTimer = null;
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(checkNames, 250);
}

function showProblems(problems) {
  teamInputs().forEach((input, i) => {
    const p = problems[i];
    $(`team-${i}-error`).textContent = p || "";
    input.setAttribute("aria-invalid", String(Boolean(p)));
  });
}

async function checkNames() {
  if (!state.newGame) return [];
  const r = await call("check", { seed: state.newGame.seed, names: names() });
  showProblems(r.value);
  return r.value;
}

async function leagueSeedChanged() {
  const s = parseSeed("league-seed");
  $("league-seed-error").textContent = s.error || "";
  if (s.error) return;
  if (s.value !== null && state.newGame && state.newGame.seed === s.value) return; // 同じシードなら作り直さない
  const seed = s.value === null ? randomSeed() : s.value;
  await preparePreview(seed, false);
  await checkNames();
}

async function startNewGame() {
  $("new-message").textContent = "";
  const ls = parseSeed("league-seed");
  const ss = parseSeed("season-seed");
  $("league-seed-error").textContent = ls.error || "";
  $("season-seed-error").textContent = ss.error || "";
  const problems = await checkNames();
  const bad = problems.findIndex(Boolean);
  if (ls.error || ss.error || bad >= 0) {
    $("new-message").textContent = "入力に問題があります。赤い字の説明を見て、直してください。";
    if (bad >= 0) teamInputs()[bad].focus();
    return;
  }
  const mine = myTeamIndex();
  if (mine < 0) {
    $("new-message").textContent = "「自球団にする」で、球団を1つ選んでください。";
    return;
  }
  if (state.dirty && !confirm("今のゲームに、未保存の変更があります。新しく始めると失われます。始めますか?")) return;
  $("new-start").disabled = true;
  try {
    const seed = state.newGame.seed;
    const seasonSeed = ss.value === null ? randomSeed() : ss.value;
    const r = await call("newGame", { seed, seasonSeed, names: names(), myTeamIndex: mine });
    if (!r.ok) {
      $("new-message").textContent = [r.message, ...r.problems].join("\n");
      return;
    }
    // 入力欄は空にしておく(画面に名前を残さない)
    for (const input of teamInputs()) input.value = "";
    state.newGame = null;
    enterGame(r.value, true);
  } finally {
    $("new-start").disabled = false;
  }
}

// ---- 進める ----

async function advance(days) {
  if (state.running || !state.view || state.view.status.is_over) return;
  state.running = true;
  state.stopRequested = false;
  $("stop").disabled = false;
  $("progress-message").className = "message";
  $("progress-message").textContent = "";
  render();
  let left = days === 0 ? Infinity : days;
  try {
    // 1日ずつ裏に頼む。日の区切りごとに、止める指示を確かめる
    while (left > 0 && !state.view.status.is_over && !state.stopRequested) {
      const r = await call("advance", { days: 1 });
      state.view = r.value;
      setDirty(true);
      left -= 1;
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
    state.running = false;
    render();
  }
}

// ---- 保存と読み込み ----

function todayText() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

async function save() {
  if (!state.view || state.running) return;
  $("save").disabled = true;
  try {
    const r = await call("save", { today: todayText() });
    const blob = new Blob([r.value.bytes], { type: "application/octet-stream" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = r.value.file_name; // 日付だけのファイル名(球団名は入れない)
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    state.view.status = r.value.status;
    setDirty(false);
    $("progress-message").className = "message ok";
    $("progress-message").textContent = `保存しました(${r.value.file_name}、${(r.value.bytes.length / 1024 / 1024).toFixed(1)}MB)。ダウンロードのフォルダに入ります。`;
    if (state.tab !== "progress") showScreen("progress");
  } catch (err) {
    $("progress-message").className = "message ng";
    $("progress-message").textContent = `保存できませんでした:${err.message}`;
  } finally {
    render();
  }
}

async function openFile(event, messageId) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file || !ready || state.running) return;
  const el = $(messageId);
  el.className = "message";
  if (state.dirty && !confirm("今のゲームに、未保存の変更があります。開くと失われます。開きますか?")) return;
  el.textContent = "読み込んでいます…";
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const r = await call("load", { bytes });
    if (!r.ok) {
      el.className = "message ng";
      el.textContent = [r.message, ...r.problems.slice(0, 10).map((p) => `・${p}`), r.problems.length > 10 ? `…ほか ${r.problems.length - 10} 件` : ""]
        .filter(Boolean)
        .join("\n");
      if (state.view) showScreen("progress"); // 上の「開く」から開いたときは、進行の画面に説明を出す
      return;
    }
    el.textContent = "";
    enterGame(r.value, false);
    $("progress-message").className = "message ok";
    $("progress-message").textContent = `開きました(${r.value.status.day}日目から)。`;
  } catch (err) {
    el.className = "message ng";
    el.textContent = `読み込めませんでした:${err.message}`;
  }
}

// ---- 閉じる前の警告(未保存の変更があるときだけ) ----

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

// ---- ボタン ----

buildTabs();
$("go-new").addEventListener("click", () => showNewGame().catch((err) => ($("start-message").textContent = err.message)));
$("new-back").addEventListener("click", () => {
  for (const input of teamInputs()) input.value = "";
  state.newGame = null;
  showScreen(state.view ? state.tab : "start");
});
$("new-start").addEventListener("click", startNewGame);
$("league-seed").addEventListener("change", leagueSeedChanged);
$("season-seed").addEventListener("change", () => ($("season-seed-error").textContent = parseSeed("season-seed").error || ""));
for (const b of document.querySelectorAll(".adv")) b.addEventListener("click", () => advance(Number(b.dataset.days)));
$("stop").addEventListener("click", () => {
  state.stopRequested = true;
  $("stop").disabled = true;
  $("progress-message").textContent = "この日の試合が終わったら止めます…";
});
$("save").addEventListener("click", save);
$("open-file-start").addEventListener("change", (e) => openFile(e, "start-message"));
$("open-file-top").addEventListener("change", (e) => openFile(e, "progress-message"));
$("open-file-start").disabled = true;
$("open-file-top").disabled = true;
boot();
