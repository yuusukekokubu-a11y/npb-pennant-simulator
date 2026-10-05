// 新規開始(名前の入力・シード・自球団・試運転の進み具合)(web/app.js から分けた。保守②。D-291)

import { $, MAX_SEED, state } from "./core.js";
import { call } from "./backend.js";
import { showScreen } from "./screens.js";
import { enterGame } from "./progress.js";

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

export async function showNewGame() {
  $("new-message").textContent = "";
  $("my-team-none").checked = true; // 既定は観戦のみ(D-198)
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
      radio.checked = prev ? prev.mine === index : false; // 既定は観戦のみ(D-198)
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

export async function leagueSeedChanged() {
  const s = parseSeed("league-seed");
  $("league-seed-error").textContent = s.error || "";
  if (s.error) return;
  if (s.value !== null && state.newGame && state.newGame.seed === s.value) return; // 同じシードなら作り直さない
  const seed = s.value === null ? randomSeed() : s.value;
  await preparePreview(seed, false);
  await checkNames();
}

export async function startNewGame() {
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
  const mine = myTeamIndex(); // -1 は観戦のみ(球団を操作しない。D-198)
  if (state.dirty && !confirm("今のゲームに、未保存の変更があります。新しく始めると失われます。始めますか?")) return;
  $("new-start").disabled = true;
  try {
    const seed = state.newGame.seed;
    const seasonSeed = ss.value === null ? randomSeed() : ss.value;
    const baselines = document.querySelector("input[name=baseline-mode]:checked").value;
    $("trial-box").hidden = false; // 事前運転(リーグの歴史を作る)は、基準値の求め方にかかわらず行う(D-190)
    $("trial-progress").value = 0;
    $("trial-text").textContent = "リーグの歴史を作っています(数十年分の選手の入れ替わり)…";
    const t0 = performance.now();
    const scoutLevel = document.querySelector("input[name=scout-level]:checked").value;
    const moneyRule = document.querySelector("input[name=money-rule]:checked").value;
    const r = await call("newGame", { seed, seasonSeed, names: names(), myTeamIndex: mine, baselines, scoutLevel, moneyRule }).finally(() => ($("trial-box").hidden = true));
    state.trialSeconds = baselines === "trial" ? (performance.now() - t0) / 1000 : null;
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

export function prerunProgress({ year, total }) {
  $("trial-progress").max = total;
  $("trial-progress").value = year;
  $("trial-text").textContent = `リーグの歴史を作っています:${year} / ${total} 年(試合はせず、選手の入れ替わりだけを進めます)`;
}

export function trialProgress({ day, total }) {
  $("trial-progress").max = total;
  $("trial-progress").value = day;
  $("trial-text").textContent = `試運転のシーズン:${day} / ${total} 日(この結果は、画面に出さず保存もしません)`;
}
