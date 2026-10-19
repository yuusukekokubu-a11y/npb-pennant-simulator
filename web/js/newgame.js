// 新規開始(名前の入力・シード・自球団・試運転の進み具合)(web/app.js から分けた。保守②。D-291)

import { $, MAX_SEED, state } from "./core.js";
import { call } from "./backend.js";
import { showScreen } from "./screens.js";
import { enterGame } from "./progress.js";
import { termById } from "./glossary.js";
import { busyButton, hideLoading, showLoading, updatePercent } from "./loading.js";
import { el, table } from "./parts.js";

// ---- 新規開始 ----

function randomSeed() {
  return crypto.getRandomValues(new Uint32Array(1))[0];
}

export function parseSeed(id) {
  // 空欄なら null(毎回ちがう値)。正しくない値なら理由の文字
  const text = $(id).value.trim();
  if (text === "") return { value: null };
  if (!/^\d+$/.test(text)) return { error: "0 以上の整数を入れてください" };
  const n = Number(text);
  if (n > MAX_SEED) return { error: `${MAX_SEED} 以下にしてください` };
  return { value: n };
}

export function teamInputs() {
  return [...document.querySelectorAll("#league-fields input[type=text]")];
}

function names() {
  return teamInputs().map((i) => i.value);
}

// リーグ名・球場名の欄(「詳しい設定」の中。D-409)
const leagueNameInputs = () => [...document.querySelectorAll("#place-fields input[data-kind=league]")];
const stadiumInputs = () => [...document.querySelectorAll("#place-fields input[data-kind=stadium]")];

function places() {
  return { leagues: leagueNameInputs().map((i) => i.value), stadiums: stadiumInputs().map((i) => i.value), tiers: state.newGame ? [...state.newGame.tiers] : null };
}

// 球団の資金力の表(設定の段。D-490):1 行に 1 球団(名前の段の球団名。空欄なら初期名)と選ぶ欄。自球団の行は ★
const TIER_OPTIONS = [["auto", "自動"], ["small", "小"], ["medium", "中"], ["large", "大"], ["huge", "超大量"]];

function buildTierFields() {
  const ng = state.newGame;
  const mine = myTeamIndex();
  const teams = ng.preview.leagues.flatMap((lg) => lg.teams);
  const inputs = teamInputs();
  const rows = teams.map((t, i) => {
    const name = (inputs[i] && inputs[i].value.trim()) || t.default_name;
    const sel = el("select", { dataset: { team: String(i) }, "aria-label": `${name}の資金力`, onchange: (e) => { ng.tiers[i] = e.target.value; } },
      ...TIER_OPTIONS.map(([v, label]) => el("option", { value: v, selected: ng.tiers[i] === v }, label)));
    return { index: i, name, mine: i === mine, values: { tier: sel } };
  });
  $("tier-fields").replaceChildren(table({ firstLabel: "球団", columns: [{ key: "tier", label: "資金力" }], rows, first: (r) => [el("span", { className: "pname" }, r.mine ? `★ ${r.name}` : r.name), r.mine ? el("span", { className: "sub" }, "自球団") : null], rowClass: (r) => (r.mine ? "mine" : ""), fluid: true }));
}

function placeInput(kind, index, labelText, placeholder, value) {
  const id = `${kind}-name-${index}`;
  const wrap = document.createElement("div");
  wrap.className = "place-field";
  const label = document.createElement("label");
  label.htmlFor = id;
  label.textContent = labelText;
  const input = document.createElement("input");
  Object.assign(input, { type: "text", id, placeholder, autocomplete: "off", spellcheck: false, value: value || "" });
  input.dataset.kind = kind;
  input.setAttribute("autocapitalize", "off");
  input.setAttribute("aria-describedby", `${id}-error`);
  input.addEventListener("input", schedulePlaceCheck);
  const err = document.createElement("p");
  err.className = "error";
  err.id = `${id}-error`;
  wrap.append(label, input, err);
  return wrap;
}

function buildPlaceFields(pv, prev) {
  const box = $("place-fields");
  box.replaceChildren();
  pv.leagues.forEach((lg, i) => box.append(placeInput("league", i, `${i + 1}つ目のリーグ名`, lg.name, prev && prev.leagues[i])));
  let i = 0;
  for (const lg of pv.leagues) for (const t of lg.teams) {
    const index = i++;
    box.append(placeInput("stadium", index, `${index + 1}番目の球団(${t.default_name})の球場名`, t.stadium, prev && prev.stadiums[index]));
  }
}

let placeTimer = null;
function schedulePlaceCheck() {
  clearTimeout(placeTimer);
  placeTimer = setTimeout(checkPlaces, 250);
}

async function checkPlaces() {
  if (!state.newGame) return [];
  const p = places();
  const r = await call("checkPlaces", { seed: state.newGame.seed, leagues: p.leagues, stadiums: p.stadiums });
  const show = (inputs, problems) => inputs.forEach((input, i) => {
    const msg = (problems || [])[i] || "";
    $(`${input.id}-error`).textContent = msg;
    input.setAttribute("aria-invalid", String(Boolean(msg)));
  });
  show(leagueNameInputs(), r.value.leagues);
  show(stadiumInputs(), r.value.stadiums);
  return [...(r.value.leagues || []), ...(r.value.stadiums || [])].filter(Boolean);
}

// 新規開始の段(D-448、D-464):最初の 2 つのボタンの段("choice")、球団名などの段(false)、設定(仕組みの選択肢)の段(true)
export function newGameStep(settings) {
  const choice = settings === "choice";
  $("new-step-choice").hidden = !choice;
  $("new-row-choice").hidden = !choice;
  $("new-step-names").hidden = choice || settings === true;
  $("new-row-names").hidden = choice || settings === true;
  $("new-step-settings").hidden = settings !== true;
  $("new-row-settings").hidden = settings !== true;
  $("new-help").hidden = true;
  $("new-message").textContent = "";
  window.scrollTo(0, 0);
}

export async function showNewGame() {
  $("new-help").hidden = true;
  $("new-message").textContent = "";
  $("my-team-none").checked = true; // 既定は観戦のみ(D-198)
  $("league-seed").value = "";
  $("season-seed").value = "";
  $("league-seed-error").textContent = "";
  $("season-seed-error").textContent = "";
  const r = await call("templateInfo", {}); // 用意したリーグの紹介(D-462)
  state.template = r.value;
  const intro = $("template-intro");
  intro.replaceChildren(...r.value.lines.map((line) => Object.assign(document.createElement("li"), { textContent: line })));
  state.newGame = null;
  newGameStep("choice");
  showScreen("new");
}

// 最初の段のボタン(D-464):用意したリーグ(テンプレートのリーグのシードで名前の一覧を作る)か、新しく作るか
export function chooseNewGame(template) {
  return busyButton($(template ? "new-use-template" : "new-build"), chooseNewGameInner(template));
}

async function chooseNewGameInner(template) {
  await preparePreview(template ? state.template.league_seed : randomSeed(), true);
  state.newGame.template = template;
  for (const el of document.querySelectorAll("#screen-new .new-build-only")) el.hidden = template; // リーグのシード・基準値・スカウトはテンプレートのまま(D-464)
  $("new-mode-text").textContent = template ? `${state.template.title}で始めます。` : "新しくリーグを作って始めます。";
  newGameStep(false);
}

async function preparePreview(seed, reset) {
  const r = await call("preview", { seed });
  const pv = r.value;
  // 入力済みの名前と自球団の選択は、作り直す直前の状態を引き継ぐ
  const prev = reset ? null : { names: names(), mine: myTeamIndex(), ...places() };
  state.newGame = { seed, preview: pv, template: Boolean(state.newGame && state.newGame.template), tiers: reset || !state.newGame ? Array(12).fill("auto") : state.newGame.tiers }; // 資金力は全球団「自動」から(D-490)
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
      radio.checked = prev ? prev.mine === index : false; // 既定は観戦のみ(D-198)
      pick.append(radio, "自球団にする");
      div.append(label, input, err, pick);
      fs.appendChild(div);
    }
    box.appendChild(fs);
  }
  buildPlaceFields(pv, prev);
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

export async function leagueSeedChanged() {
  const s = parseSeed("league-seed");
  $("league-seed-error").textContent = s.error || "";
  if (s.error) return;
  if (!state.newGame || state.newGame.template) return; // 用意したリーグはリーグのシードを変えない
  if (s.value !== null && state.newGame.seed === s.value) return; // 同じシードなら作り直さない
  const seed = s.value === null ? randomSeed() : s.value;
  await preparePreview(seed, false);
  await checkNames();
}

export function startNewGame() {
  return busyButton($("new-start"), startNewGameInner()); // 押した瞬間に「準備中」(D-411)
}

// 「次へ(設定)」:入力を確かめてから、設定の段へ
export async function nextNewGame() {
  if (!(await checkNewGameInputs())) return;
  buildTierFields(); // 名前の段の球団名と自球団で作り直す(選んだ資金力は残す)
  newGameStep(true);
}

async function checkNewGameInputs() {
  $("new-message").textContent = "";
  const ls = state.newGame && state.newGame.template ? { value: null } : parseSeed("league-seed"); // 用意したリーグはリーグのシードを使わない
  const ss = parseSeed("season-seed");
  $("league-seed-error").textContent = ls.error || "";
  $("season-seed-error").textContent = ss.error || "";
  const problems = await checkNames();
  const bad = problems.findIndex(Boolean);
  const placeProblems = await checkPlaces(); // リーグ名・球場名(D-409)
  if (ls.error || ss.error || bad >= 0 || placeProblems.length) {
    $("new-message").textContent = "入力に問題があります。赤い字の説明を見て、直してください。";
    if (bad >= 0) teamInputs()[bad].focus();
    else if (placeProblems.length) {
      document.querySelector("#screen-new details").open = true; // 詳しい設定の中の欄を見せる
      [...leagueNameInputs(), ...stadiumInputs()].find((i) => i.getAttribute("aria-invalid") === "true")?.focus();
    }
    return false;
  }
  return true;
}

async function startNewGameInner() {
  if (!(await checkNewGameInputs())) {
    newGameStep(false);
    $("new-message").textContent = "入力に問題があります。赤い字の説明を見て、直してください。";
    return;
  }
  const ss = parseSeed("season-seed");
  const mine = myTeamIndex(); // -1 は観戦のみ(球団を操作しない。D-198)
  if (state.dirty && !confirm("今のゲームに、未保存の変更があります。新しく始めると失われます。始めますか?")) return;
  $("new-start").disabled = true;
  try {
    const rules = Object.fromEntries([...document.querySelectorAll("input[name=rules]")].map((x) => [x.value, x.checked]));  // 仕組みの選択肢(D-381)
    if (state.newGame.template) { // 用意したリーグ(D-465):事前運転も試運転もしないので、ロード画面は出さない(ボタンのボールだけ)
      const seasonSeed = ss.value === null ? randomSeed() : ss.value; // シーズンは毎回ちがう
      const t0 = performance.now();
      const r = await call("newFromTemplate", { seasonSeed, names: names(), myTeamIndex: mine, rules, places: places() });
      state.templateSeconds = (performance.now() - t0) / 1000;
      state.trialSeconds = null;
      if (!r.ok) {
        $("new-message").textContent = [r.message, ...r.problems].join("\n");
        return;
      }
      for (const input of [...teamInputs(), ...leagueNameInputs(), ...stadiumInputs()]) input.value = "";
      state.newGame = null;
      enterGame(r.value, true);
      return;
    }
    const seed = state.newGame.seed;
    const seasonSeed = ss.value === null ? randomSeed() : ss.value;
    const baselines = document.querySelector("input[name=baseline-mode]:checked").value;
    showLoading("new"); // 事前運転(リーグの歴史を作る)は、基準値の求め方にかかわらず行う(D-190)。全画面のロード画面(D-405)
    $("trial-progress").value = 0;
    $("trial-title").textContent = "リーグの歴史を作っています";
    $("trial-text").textContent = "数十年分の選手の入れ替わりを進めています…";
    updatePercent($("trial-progress"));
    const t0 = performance.now();
    const scoutLevel = document.querySelector("input[name=scout-level]:checked").value;
    const r = await call("newGame", { seed, seasonSeed, names: names(), myTeamIndex: mine, baselines, scoutLevel, rules, places: places() }).finally(hideLoading);
    state.trialSeconds = baselines === "trial" ? (performance.now() - t0) / 1000 : null;
    if (!r.ok) {
      $("new-message").textContent = [r.message, ...r.problems].join("\n");
      return;
    }
    // 入力欄は空にしておく(画面に名前を残さない)
    for (const input of [...teamInputs(), ...leagueNameInputs(), ...stadiumInputs()]) input.value = "";
    state.newGame = null;
    enterGame(r.value, true);
  } finally {
    $("new-start").disabled = false;
  }
}

export function prerunProgress({ year, total }) {
  $("trial-progress").max = total;
  $("trial-progress").value = year;
  $("trial-title").textContent = "リーグの歴史を作っています";
  $("trial-text").textContent = `${year} / ${total} 年(試合はせず、選手の入れ替わりだけを進めます)`;
  updatePercent($("trial-progress"));
}

export function trialProgress({ day, total }) {
  $("trial-progress").max = total;
  $("trial-progress").value = day;
  $("trial-title").textContent = "リーグの基準値を求めています";
  $("trial-text").textContent = `試運転のシーズン:${day} / ${total} 日(この結果は、画面に出さず保存もしません)`;
  updatePercent($("trial-progress"));
}

// ---- 説明バー(UI の整理②。D-313) ----
// 設定の項目(入力欄・選択肢)を選ぶと、その項目の説明を用語集から引いて、画面の下のバーに出す。選び直すと入れ替わる。
// 設定の画面から離れると消える(screens.js の showScreen)。

export function helpIdFor(target) {
  if (!target || !target.matches) return null;
  if (target.id === "new-use-template") return "setting.template";
  if (target.id === "new-build") return "setting.build";
  if (target.matches("#league-fields input[type=text]")) return "setting.team_name";
  if (target.dataset && target.dataset.kind === "league") return "setting.league_name";
  if (target.dataset && target.dataset.kind === "stadium") return "setting.stadium_name";
  if (target.matches("#tier-fields select")) return "setting.team_tiers";
  if (target.name === "my-team") return target.value === "-1" ? "setting.watch" : "setting.my_team";
  if (target.id === "league-seed") return "setting.league_seed";
  if (target.id === "season-seed") return "setting.season_seed";
  const groups = { "baseline-mode": "setting.baseline", "scout-level": "setting.scout" };
  if (groups[target.name]) return `${groups[target.name]}.${target.value}`;
  if (target.name === "rules") return `setting.rules.${target.value}`;
  return null;
}

function showHelp(event) {
  const t = termById(helpIdFor(event.target));
  if (!t) return;
  $("new-help-name").textContent = t.name;
  $("new-help-text").textContent = t.meaning;
  $("new-help").hidden = false;
}

// 入力欄は選んだとき(focusin)、選択肢は選び終わったとき(change)に出す。押している途中にバーの中身が変わって
// 画面がずれ、押した場所がずれないように(選択肢の focusin では変えない)
$("screen-new").addEventListener("focusin", (e) => { if (e.target.type !== "radio" && e.target.type !== "checkbox") showHelp(e); });
$("screen-new").addEventListener("change", showHelp);
