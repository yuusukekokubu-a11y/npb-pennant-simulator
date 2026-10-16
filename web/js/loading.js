// ロード画面(D-405〜D-407):長い待ち(最初の起動・新規開始)は全画面、短い待ち(画面の切り替え)は前の画面を暗くしてボール。
// 長い待ちの真ん中の絵は「球場」(走者が塁を回る)か「歴史」(事前運転の年ごとの出来事)。選択だけを端末に覚える(D-407)。

import { $, state, teamColor } from "./core.js";

const STYLE_KEY = "npb-pennant-loading-style"; // 端末に残すのはこの 1 つの値だけ("stadium" か "history"。D-407)
const STYLES = ["stadium", "history"];
const HINT_MS = 6000; // ヒントの札を入れ替える間隔(仮置き)
const VEIL_MS = 300; // 短い待ち:これより早く終わるときは出さない(ちらつき防止。仮置き。D-405)
const HISTORY_ROWS = 7; // 「歴史」に並べておく行の数
// 起動の間はまだ用語集を受け取っていないので、決まったヒントを出す
const BOOT_HINTS = [
  "「進行」のタブで、1日・1週間・最後まで、好きな速さでシーズンを進められます。",
  "保存は自動では行いません。上の帯の保存のアイコンで、端末にファイルを残してください。",
  "選手名やチーム名を押すと、くわしいページが開きます。",
  "表の見出しを押すと、その列で並べ替えられます。もう一度押すと逆の順になります。",
];

let hintTimer = null;
let hintIndex = 0;

// ---- 選択(球場・歴史) ----

export function loadingStyle() {
  try {
    const v = window.localStorage.getItem(STYLE_KEY);
    return STYLES.includes(v) ? v : "stadium";
  } catch {
    return "stadium"; // 保存領域が使えない端末は球場
  }
}

export function setLoadingStyle(value) {
  if (!STYLES.includes(value)) return;
  try {
    window.localStorage.setItem(STYLE_KEY, value);
  } catch {
    // 覚えられなくても、今回の表示には使う
  }
  renderStyleSwitch(value);
}

export function renderStyleSwitch(value = loadingStyle()) {
  for (const b of $("loading-style").children) b.setAttribute("aria-pressed", String(b.dataset.value === value));
}

// ---- 長い待ち(全画面) ----

// kind は "boot"(最初の起動。いつも球場)か "new"(新規開始。選んだ形)
export function showLoading(kind) {
  const style = kind === "boot" ? "stadium" : loadingStyle();
  $("loading-screen").dataset.style = style;
  $("ls-stadium").hidden = style !== "stadium";
  $("ls-history").hidden = style !== "history";
  $("ls-history").replaceChildren();
  $("boot").hidden = kind !== "boot";
  $("trial-box").hidden = kind !== "new";
  $("loading-screen").hidden = false;
  hintIndex = Math.floor(Math.random() * 1000);
  showHint();
  clearInterval(hintTimer);
  hintTimer = setInterval(showHint, HINT_MS);
}

export function hideLoading() {
  $("loading-screen").hidden = true;
  clearInterval(hintTimer);
  hintTimer = null;
}

// 進み具合の棒の割合(進み具合の要素の値から)
export function updatePercent(bar) {
  const p = bar.max ? Math.round((100 * bar.value) / bar.max) : 0;
  $("ls-percent").textContent = `${Math.max(0, Math.min(100, p))}%`;
}

function hints() {
  const g = state.glossary;
  if (!g) return BOOT_HINTS;
  const keys = new Set(["game", "batting", "pitching", "war"]);
  const out = g.terms.filter((t) => keys.has(t.category) && t.meaning && t.meaning.length <= 80).map((t) => `${t.name}:${t.meaning}`);
  return out.length ? out : BOOT_HINTS;
}

function showHint() {
  const list = hints();
  hintIndex = (hintIndex + 1) % list.length;
  $("ls-tip-text").textContent = list[hintIndex];
}

// 「歴史」:事前運転の 1 年ごとの出来事(D-406)。新しい行を上に足す
function initial(name) {
  return String(name || "").slice(0, 1);
}

function row(year, teamId, teamName, text) {
  const r = document.createElement("div");
  r.className = "ls-row";
  const y = document.createElement("span");
  y.className = "ls-year";
  y.textContent = `${year}年目`;
  r.append(y);
  if (teamId) {
    const e = document.createElement("span");
    e.className = "emblem small";
    e.dataset.initial = initial(teamName);
    e.style.setProperty("--team", teamColor(teamId));
    r.append(e);
  }
  const t = document.createElement("span");
  t.className = "ls-text";
  t.textContent = text;
  r.append(t);
  return r;
}

export function addChronicle(e) {
  const box = $("ls-history");
  const rows = [];
  if (e.strongest) rows.push(row(e.year, e.strongest.team_id, e.strongest.team_name, `戦力 1 位 ${e.strongest.team_name}`));
  if (e.retired) rows.push(row(e.year, null, "", `引退 ${e.retired.name}(${e.retired.age}歳・${e.retired.team_name})`));
  if (e.rookie) rows.push(row(e.year, e.rookie.team_id, e.rookie.team_name, `目玉の新人 ${e.rookie.name}(${e.rookie.team_name})`));
  for (const r of rows) box.prepend(r);
  while (box.children.length > HISTORY_ROWS) box.lastChild.remove();
}

// ---- 押したボタンの「準備中」(D-411):押した瞬間に、押せたことが分かるようにする ----

export function busyButton(button, promise) {
  button.disabled = true;
  button.classList.add("busy");
  button.setAttribute("aria-busy", "true");
  return Promise.resolve(promise).finally(() => {
    button.disabled = false;
    button.classList.remove("busy");
    button.removeAttribute("aria-busy");
  });
}

// ---- 短い待ち(前の画面を暗くしてボール) ----

let veilTimer = null;
let veilCount = 0;

export function waitVeil(promise) {
  veilCount += 1;
  if (veilCount === 1) veilTimer = setTimeout(() => ($("wait-veil").hidden = false), VEIL_MS);
  const done = () => {
    veilCount -= 1;
    if (veilCount > 0) return;
    clearTimeout(veilTimer);
    $("wait-veil").hidden = true;
  };
  return Promise.resolve(promise).finally(done);
}
