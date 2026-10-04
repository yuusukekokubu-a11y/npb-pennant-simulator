// 遊ぶための画面(web/)を、本物のブラウザ(画面を出さない Chromium)で通しで確かめる、開発者向けのスクリプト。
// CI では動かさない(Playwright という、ブラウザを自動で操作する道具が要るため)。
//
// 使い方(先に scripts/build_web.py で _site/ を作り、手元で配信しておく):
//   python scripts/build_web.py --pyodide <Pyodide のフォルダ>
//   python -m http.server 8765 --directory _site
//   node scripts/check_browser.mjs http://127.0.0.1:8765/?pyodide=local
//
// 確かめること(スマホの縦持ちの大きさ 412×924):
//   新規開始(球団名の検証・自球団の選択)→ 進行(1日・1週間・最後まで・止める)→ 順位表(Python の順位と同じか)
//   → 保存 → ページの読み込み直し → 読み込み → 最後まで。最後の結果が、指紋 (d) と同じか。
//   画面の文字に能力値・隠し情報の言葉がないか。通信先と、ブラウザの保存領域の状態。閉じる前の警告。
// Python は、保存したファイルを確かめるときだけ使う(PYTHONPATH=src)。

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const URL0 = process.argv[2] || "http://127.0.0.1:8765/?pyodide=local";
const ROOT = new URL("..", import.meta.url).pathname;
const NAME = "ZZテスト球団QX"; // 入力する球団名(通信やファイル名に出ないことを確かめる)
const expected = JSON.parse(readFileSync(join(ROOT, "tests/data/fingerprints.json"), "utf8"));
const work = mkdtempSync(join(tmpdir(), "pennant-check-"));
const results = [];
let failed = 0;

function check(ok, text) {
  results.push(`${ok ? "○" : "×"} ${text}`);
  if (!ok) failed += 1;
}

function python(code, ...args) {
  return execFileSync("python3", ["-c", code, ...args], { env: { ...process.env, PYTHONPATH: join(ROOT, "src") }, encoding: "utf8" });
}

// 答え合わせモードがオフのとき、画面にも通信にも出てはいけないもの(D-108):能力の項目名・成長タイプ・生成時の型の名前と、その項目名
const HIDDEN = JSON.parse(python(`
import json
from pennant.abilities import ITEM_LABELS
from pennant.config import load_generation_config
c = load_generation_config()
# 能力の項目名(コンタクトなど)は、F3-1 からスカウト評価(推定値)の表示に使うので、隠し情報の言葉には含めない(D-199)。
# 隠し情報の言葉は、成長タイプ・生成時の型・球質・役割の名前。真の能力値は項目名(キー)で確かめる
words = {v["label"] for v in c["aging"]["growth_types"].values()} | {v["label"] for v in c["batter_archetypes"].values()}
words |= {v["label"] for v in c["pitcher_qualities"].values()} | {v["label"] for v in c["pitcher_roles"].values()}
words -= {"標準"}  # ふつうの文章にも出る短い言葉は除く
print(json.dumps({"words": sorted(words), "keys": ["ratings", "potential", "growth_type", "archetype", "ability_drift", "hidden", "park", "home_run_multiplier"]}, ensure_ascii=False))
`));

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 412, height: 924 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, acceptDownloads: true });
// 裏の計算から画面に届いたメッセージを、すべて記録する(答え合わせモードがオフのときに、隠し情報が届いていないかを見る)
await context.addInitScript(() => {
  window.__messages = [];
  window.__runLog = [];
  const Original = window.Worker;
  window.Worker = class extends Original {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (e) => window.__messages.push(JSON.stringify(e.data, (k, v) => (v instanceof Uint8Array ? "(bytes)" : v))));
    }
  };
  // 「止める」の箱の表示・非表示の時刻を記録する(D-118)
  document.addEventListener("DOMContentLoaded", () => {
    const box = document.getElementById("run-box");
    new MutationObserver(() => window.__runLog.push({ hidden: box.hidden, t: performance.now() })).observe(box, { attributes: true, attributeFilter: ["hidden"] });
  });
});
const page = await context.newPage();
const requests = [];
context.on("request", (r) => requests.push({ url: r.url(), body: r.postData() || "" }));
page.on("pageerror", (e) => check(false, `ページのエラー: ${e.message}`));
const texts = []; // 画面に出た文字(あとで隠し情報の言葉を探す)
const grab = async () => texts.push(await page.evaluate(() => document.body.innerText));
async function day() {
  const text = await page.textContent("#day-text");
  if (text.includes("終了")) return 125;
  const m = text.match(/(\d+)日目/);
  return m ? Number(m[1]) : -1;
}
const isDirty = () => page.evaluate(() => { const e = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });

async function standingsOnScreen(endLeague = null) {
  await page.click("#tabs button[data-tab=standings]");
  const out = [];
  const n = await page.locator("#league-switch button").count();
  for (let i = 0; i < n; i++) {
    await page.locator("#league-switch button").nth(i).click();
    out.push(await page.$$eval("#standings-body tr", (trs) => trs.map((tr) => ({ mine: tr.classList.contains("mine"), cells: [...tr.children].map((td) => td.innerText.replace("★", "").trim()) }))));
  }
  if (endLeague !== null) await page.locator("#league-switch button").nth(endLeague).click();
  await grab();
  await page.click("#tabs button[data-tab=progress]");
  return out;
}

async function saveFile(label) {
  const [download] = await Promise.all([page.waitForEvent("download"), page.click("#save")]);
  const path = join(work, `${label}-${download.suggestedFilename()}`);
  await download.saveAs(path);
  return { path, name: download.suggestedFilename() };
}

// ---- 1. 起動 ----
const t0 = Date.now();
await page.goto(URL0);
check((await page.textContent("#boot")).includes("初回は約12MBをダウンロードします"), "初回のダウンロードの案内が出る");
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
results.push(`  起動にかかった時間: ${((Date.now() - t0) / 1000).toFixed(1)} 秒`);
await grab();

// ---- 2. 新規開始 ----
await page.click("#go-new");
await page.waitForSelector("#screen-new:not([hidden])");
check(await page.isVisible("#real-name-notice"), "新規開始の画面に、実名の注意が出ている");
await page.fill("#team-1", "   ");
await page.waitForFunction(() => document.querySelector("#team-1-error").textContent.length > 0);
check((await page.textContent("#team-1-error")).includes("空白だけ"), `空白だけの名前の理由が、欄の下に出る(「${await page.textContent("#team-1-error")}」)`);
await page.fill("#team-1", "あ".repeat(30));
await page.waitForFunction(() => document.querySelector("#team-1-error").textContent.includes("長すぎ"));
check(true, `長すぎる名前の理由が出る(「${await page.textContent("#team-1-error")}」)`);
await page.fill("#team-1", "");
await page.fill("#team-0", NAME);
await page.fill("#team-2", NAME);
await page.waitForFunction(() => document.querySelector("#team-2-error").textContent.length > 0);
check(true, `同じ名前の理由が出る(「${await page.textContent("#team-2-error")}」)`);
await page.click("#new-start");
await page.waitForFunction(() => document.querySelector("#new-message").textContent.length > 0);
check((await page.textContent("#new-message")).includes("入力に問題") && (await page.isVisible("#screen-new")), "問題があるまま始めようとすると、止められる");
await page.fill("#team-2", "");
await grab();
// 指紋 (d) と同じシード(リーグ 1・シーズン 13)にする
await page.click("details summary");
await page.fill("#league-seed", "1");
await page.dispatchEvent("#league-seed", "change");
await page.waitForFunction(() => document.querySelector("#league-fields").dataset.seed === "1");
check((await page.inputValue("#team-0")) === NAME, "シードを変えても、入力済みの名前は残る");
await page.fill("#season-seed", "13");
const placeholder1 = await page.getAttribute("#team-1", "placeholder");
const placeholder7 = await page.getAttribute("#team-7", "placeholder");
await page.check("input[name=my-team][value='7']");
await page.click("#season-seed"); // 欄を移っても(変更の知らせが出ても)、選んだ自球団が変わらないこと
check(await page.isChecked("input[name=baseline-mode][value=trial]"), "基準値の求め方は、最初は「試運転で求める」");
let prerunSeen = false;
const prerunWatch = page.waitForFunction(() => document.querySelector("#trial-text").textContent.includes("リーグの歴史を作っています"), null, { timeout: 60000 }).then(() => (prerunSeen = true)).catch(() => {});
const trialStart = Date.now();
await page.click("#new-start");
// 試運転の進み具合(D-121)
await page.waitForSelector("#trial-box:not([hidden])");
await page.waitForFunction(() => /\d+ \/ 125 日/.test(document.querySelector("#trial-text").textContent));
check((await page.textContent("#trial-box")).includes("リーグの基準値を求めています"), `試運転の進み具合が出る(「${await page.textContent("#trial-text")}」)`);
await page.waitForSelector("#screen-progress:not([hidden])", { timeout: 120000 });
const trialSeconds = (Date.now() - trialStart) / 1000;
results.push(`  事前運転(25 年)と試運転(1シーズン)を含めた新規開始の時間: ${trialSeconds.toFixed(1)} 秒`);
check(prerunSeen, "新規開始の間に、事前運転(リーグの歴史を作っています)の進み具合が出る");
const mineName = await page.textContent("#mine-name");
check(mineName === placeholder7, `選んだ8番目の球団が自球団になる(${mineName})`);
check((await page.textContent("#day-text")).startsWith("1シーズン目 0日目 / 125日"), `進行の画面:「${await page.textContent("#day-text")}」`);
check(await isDirty(), "新規開始のあとは「未保存」(閉じる前に警告が出る)");
check(await page.isVisible("#dirty"), "「未保存の変更があります」が表示される");

// ---- 3. 進行 ----
await page.evaluate(() => (window.__runLog = []));
await page.click(".adv[data-days='1']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("1シーズン目 1日目"));
await page.waitForFunction(() => !document.querySelector(".adv[data-days='1']").disabled);
check((await page.evaluate(() => window.__runLog.length)) === 0, "「1日」では「止める」と進み具合のバーが出ない");
await page.click(".adv[data-days='7']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("1シーズン目 8日目"));
check(true, "「1日」「1週間」で、1日目・8日目に進む");
await grab();
// 最後まで → 少し待って止める。止めている間も画面が動くかを確かめる
await page.evaluate(() => (window.__runLog = []));
const runStart = await page.evaluate(() => performance.now());
await page.click(".adv[data-days='0']");
check(await page.isDisabled(".adv[data-days='1']"), "進めている間は、ほかの進行ボタンを押せない");
await page.waitForSelector("#run-box:not([hidden])", { timeout: 60000 });
const shownAfter = (await page.evaluate(() => window.__runLog[0].t)) - runStart;
check(shownAfter >= 550, `「止める」と進み具合のバーは、0.6 秒を超えてから出る(${(shownAfter / 1000).toFixed(2)} 秒後)`);
const frame = await page.evaluate(() => new Promise((r) => { const s = performance.now(); requestAnimationFrame(() => r(performance.now() - s)); }));
check(frame < 500, `進めている途中も画面が固まらない(次の描画まで ${frame.toFixed(0)} ミリ秒)`);
await page.click("#stop");
await page.waitForFunction(() => document.querySelector("#progress-message").textContent.includes("止めました"), null, { timeout: 30000 });
const stoppedDay = await day();
check(stoppedDay > 8 && stoppedDay < 125, `「止める」で、日の区切りで止まる(${stoppedDay}日目。「${await page.textContent("#progress-message")}」)`);
await page.waitForFunction(() => !document.querySelector(".adv[data-days='1']").disabled);
check(!(await page.isDisabled(".adv[data-days='1']")), "止めたあと、また進められる");
const runLog = await page.evaluate(() => window.__runLog);
const shownFor = runLog.length >= 2 ? runLog[1].t - runLog[0].t : 0;
check(runLog.length === 2 && runLog[1].hidden && shownFor >= 590, `出したあとは、最低 0.6 秒は表示を続ける(${(shownFor / 1000).toFixed(2)} 秒表示)`);

// ---- 4. 順位表(Python の順位と同じか) ----
const shown = await standingsOnScreen();
const first = await saveFile("stopped");
check(/^save-\d{8}\.sav$/.test(first.name), `保存のファイル名は日付だけ(${first.name})`);
check(!(await isDirty()) && !(await page.isVisible("#dirty")), "保存したあとは「未保存」でない(閉じる前の警告が出ない)");
const pyTable = JSON.parse(python(`
import json, sys
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
t = g.standings()
print(json.dumps([[{"mine": r["is_mine"], "cells": [f"{r['rank']}{r['name']}", str(r["games"]), str(r["wins"]), str(r["losses"]), str(r["ties"]), r["pct"], r["games_behind"]]} for r in lg["rows"]] for lg in t["leagues"]], ensure_ascii=False))
`, first.path));
const flat = (t) => JSON.stringify(t.map((lg) => lg.map((r) => ({ mine: r.mine, cells: r.cells.map((c) => c.replace(/\s+/g, "")) }))));
check(flat(shown) === flat(pyTable), "順位表が、計算本体の順位(season.standings)と同じ");
check(shown.flat().filter((r) => r.mine).length === 1, "自球団の行が1つだけ目立つ表示になっている");
results.push(`  画面の順位表(${stoppedDay}日目。1つ目のリーグ): ${shown[0].map((r) => r.cells.join(" ")).join(" / ")}`);

// ---- 4b. 成績の画面(個人成績・選手・試合・答え合わせ。②) ----
function pyGame(code, ...args) {
  return JSON.parse(python(`
import json, sys
from pennant import api
from pennant.game_stats import narrate
g = api.Game.load(open(sys.argv[1], "rb").read())
a = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
${code}
`, first.path, ...args));
}

async function statsOnScreen() {
  // 「もっと見る」をすべて押して、表の全部の行を読む
  await page.waitForSelector("#stats-loading", { state: "hidden" });
  for (;;) {
    const more = page.locator("#stats-table .more");
    if (!(await more.count())) break;
    const before = await more.textContent();
    await more.click();
    // 押したあとの作り直し(裏の計算を待つことがある)が終わるまで待つ
    await page.waitForFunction((b) => { const m = document.querySelector("#stats-table .more"); return !m || m.textContent !== b; }, before);
  }
  return page.$$eval("#stats-table tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td .link").textContent, ...[...tr.children].slice(1).map((td) => td.textContent)]));
}

async function waitStats(text, timeout = 30000) {
  await page.waitForFunction((t) => document.querySelector("#stats-info").textContent.includes(t), text, { timeout });
  return statsOnScreen();
}

// 画面の表と同じ形(名前、固定の列があればその値、各列の値)
const pyStats = (args) => pyGame(`t = g.stats(**a)\nkeys = ([t["extra_column"]["key"]] if t["extra_column"] else []) + [c["key"] for c in t["columns"]]\nprint(json.dumps([[r["name"]] + [r["values"][k] for k in keys] for r in t["rows"]], ensure_ascii=False))`, JSON.stringify(args));
const headsOnScreen = () => page.$$eval("#stats-table th", (ths) => ths.map((th) => th.textContent.replace(/ [▲▼]$/, "")));

await page.click("#tabs button[data-tab=stats]");
let screenRows = await waitStats("打率(高い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic" })), `個人成績(打者・基本・打率の高い順・規定到達者)が、計算本体の集計と同じ(${screenRows.length}人)`);
let heads = await headsOnScreen();
check(heads.indexOf("OPS") === heads.indexOf("長打率") + 1, `基本の打者の表に、長打率の隣に OPS がある(列: ${heads.slice(1).join("・")})`);
await grab();
// 並び順の保持(D-131)と、固定の列・並び順の欄(D-132)
const topByAvg = screenRows[0][0];
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("wRC+")));
screenRows = await waitStats("打率(高い順)");
check(screenRows[0][0] === topByAvg, `「セイバー」に切り替えても並び順(打率の高い順)が保たれ、先頭の選手が同じ(${topByAvg})`);
heads = await headsOnScreen();
check(heads[1] === "打率" && heads.filter((h) => h === "打率").length === 1 && heads.includes("BABIP") && !heads.includes("OPS"), `名前の隣に打率の固定列が出て(重複なし)、BABIP の列が見える。OPS はセイバーにない(列: ${heads.slice(1).join("・")})`);
check(await page.$eval("#stats-table th:nth-child(2)", (th) => th.classList.contains("sticky2")), "固定列は、横にずらしても見えたまま(sticky)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber", sort: "avg" })), "固定列つきの表が、計算本体と同じ");
const sortOptions = await page.$$eval("#stats-sort option", (os) => os.map((o) => o.textContent));
check(sortOptions.length === 18 && ["OPS", "wOBA", "本塁打", "BABIP"].every((x) => sortOptions.includes(x)), `並び順の欄に、基本・セイバーの全指標がある(${sortOptions.length}個)`);
await page.selectOption("#stats-sort", "babip");
screenRows = await waitStats("BABIP(高い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber", sort: "babip", order: "desc" })), "並び順の欄で BABIP を選ぶと並び替わる(計算本体と同じ)");
await page.click("#stats-order button[data-value=asc]");
screenRows = await waitStats("BABIP(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber", sort: "babip", order: "asc" })), "「低い順」を押すと逆の順になる");
await page.selectOption("#stats-league", "1");
await page.waitForFunction(() => document.querySelector("#stats-team").options.length === 7);
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("BABIP")));
await waitStats("BABIP(低い順)");
check((await page.inputValue("#stats-league")) === "1" && (await page.$$eval("#stats-table tbody tr", (trs) => trs.length)) < 60, "「基本」に戻しても、並び順(BABIP の低い順。固定列)とリーグの絞り込みが保たれる");
await page.click("#stats-role button[data-value=pitcher]");
screenRows = await waitStats("防御率(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "basic", league: 1 })) && (await page.inputValue("#stats-league")) === "1", "打者から投手に切り替えると、投手の既定の並び順(防御率の低い順)に戻る(絞り込みは保つ)");
await page.selectOption("#stats-league", "");
await page.click("#stats-role button[data-value=batter]");
await waitStats("打率(高い順)");
// 本塁打の列で並べ替え → もう一度で逆順
await page.click("#stats-table th button.sort >> text=本塁打");
screenRows = await waitStats("本塁打(多い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "desc" })), "見出しを押すと並べ替わる(本塁打の多い順)");
const countDesc = pyGame(`from pennant.api import metrics_config\nprint(json.dumps(metrics_config()["count_descriptions"]["batter"]["HR"], ensure_ascii=False))`);
check((await page.textContent("#stats-info")).includes(countDesc), "見出しを押すと、その列の解説が出る(元の数の解説)");
await page.click("#stats-table th button.sort >> text=本塁打");
screenRows = await waitStats("本塁打(少ない順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc" })), "もう一度押すと逆の順になる");
// 規定到達者の絞り込みを外す・リーグとチームで絞り込む
await page.uncheck("#stats-qualified");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("試合に出た全員"));
screenRows = await statsOnScreen();
const all = pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc", qualified: false });
check(JSON.stringify(screenRows) === JSON.stringify(all), `規定到達者の絞り込みを外すと、試合に出た全員(${all.length}人)`);
await page.selectOption("#stats-league", "1");
await page.waitForFunction(() => document.querySelector("#stats-team").options.length === 7);
const teamId = await page.$eval("#stats-team", (s) => s.options[2].value);
await page.selectOption("#stats-team", teamId);
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length < 40);
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc", qualified: false, league: 1, team_id: teamId })), `リーグ・チームで絞り込める(${screenRows.length}人)`);
await page.selectOption("#stats-team", "");
await page.selectOption("#stats-league", "");
await page.check("#stats-qualified");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("規定に届いた選手だけ"));
// 規定到達の判定が、実装⑤の定義(試合数 × 3.1、× 1.0)どおりか
const qual = pyGame(`
from pennant.records import qualifying_plate_appearances, qualifying_outs
rec = g.records.total
b = sorted(p for p, c in rec.batters.items() if c["PA"] >= (rec.teams[rec.batter_team[p]]["G"] * 31 + 5) // 10)
p = sorted(p for p, c in rec.pitchers.items() if c["OUTS"] >= rec.teams[rec.pitcher_team[p]]["G"] * 3)
print(json.dumps([b == sorted(g.select_players("batter")), p == sorted(g.select_players("pitcher")), len(b), len(p)]))`);
check(qual[0] && qual[1], `規定到達の判定が、規定打席(試合数×3.1)・規定投球回(試合数×1.0)どおり(打者 ${qual[2]}人・投手 ${qual[3]}人)`);
// 投手・セイバー。指標名の解説が、定義データと同じ
await page.click("#stats-role button[data-value=pitcher]");
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent === "K%"));
screenRows = await waitStats("防御率(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "saber" })), `投手のセイバー(防御率の低い順。防御率は固定列)が、計算本体と同じ(${screenRows.length}人)`);
await page.click("#stats-table th button.sort >> text=K%");
await waitStats("K%(高い順)");
const kDesc = pyGame(`from pennant.api import metrics_config\nprint(json.dumps(metrics_config().metrics["k_pct"]["description"], ensure_ascii=False))`);
check((await page.textContent("#stats-info")).includes(kDesc), "指標名を押すと出る解説が、指標の定義データの解説と同じ(K%)");
await grab();
// 第2弾の指標(打者のセイバー:wRC+ の高い順)と注記
await page.click("#stats-role button[data-value=batter]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("wRC+")));
screenRows = await waitStats("wRC+(高い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber" })), `打者のセイバー(wOBA・wRC+・OPS+。wRC+ の高い順)が、計算本体と同じ(${screenRows.length}人)`);
const baseNote = await page.textContent("#stats-baseline");
check(await page.isVisible("#stats-baseline") && baseNote.includes("試運転のシーズンの値に、今シーズンの値を混ぜて"), `表の近くに、基準値を混ぜている注記が出る(「${baseNote}」)`);
await page.click("#stats-table th button.sort >> text=OPS+");
await waitStats("OPS+(高い順)");
check((await page.textContent("#stats-info")).includes("1シーズン目は 1.0") && baseNote.includes("1シーズン目のため 1.0"), "OPS+ の解説と表の注記に、球場補正が1シーズン目は 1.0 であることが出る");
await page.click("#stats-role button[data-value=pitcher]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("FIP")));
await page.click("#stats-table th button.sort >> text=FIP");
screenRows = await waitStats("FIP(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "saber", sort: "fip", order: "asc" })), `投手のセイバーに FIP(低い順)が出て、計算本体と同じ(${screenRows.length}人)`);
await grab();

// WAR(第3弾③b。D-179):打者の表が計算本体と同じ。計算時間を測る。並び順の欄に WAR の列が入る
await page.click("#stats-role button[data-value=batter]");
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0 && [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("打率")));
results.push(`  (WAR の前の並び順: ${await page.textContent("#stats-info")})`);
const warStart = Date.now();
await page.click("#stats-kind button[data-value=war]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("WAR")), null, { timeout: 180000 });
screenRows = await waitStats("WAR(高い順)", 180000);
const warSeconds = (Date.now() - warStart) / 1000;
results.push(`  WAR の計算と表示にかかった時間(打者、初回): ${warSeconds.toFixed(1)} 秒`);
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "war" })), `打者の WAR(WAR の高い順。打席・内訳つき)が、計算本体と同じ(${screenRows.length}人。${warSeconds.toFixed(1)} 秒)`);
heads = await headsOnScreen();
check(heads.slice(1).join("・") === "打席・WAR・打撃・走塁・守備・ポジション補正・控え水準", `打者の WAR の列(${heads.slice(1).join("・")})`);
check((await page.textContent("#stats-baseline")).includes("日目までの値"), "WAR の注記に「○日目までの値」と出る");
check((await page.textContent("#stats-terms-list")).includes("控え水準") && (await page.$eval("#stats-terms", (d) => d.open)), "WAR の表の下に、用語の解説(控え水準など)が開いて出る");
const warGroups = await page.$$eval("#stats-sort optgroup", (gs) => gs.map((g) => g.label));
check(warGroups.some((g) => g.startsWith("WAR")), "並び順の欄に WAR の列の区分が出る");
await page.click("#stats-table th button.sort >> text=守備");
screenRows = await waitStats("守備(高い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "war", sort: "fielding" })), "守備の得点で並べ替えても、計算本体と同じ");
await page.click("#stats-kind button[data-value=basic]");
await waitStats("打率");
check(true, "WAR の列で並べていても、「基本」に戻すと打率の既定に戻る");
await page.click("#stats-kind button[data-value=war]");
await waitStats("守備(高い順)");
check(true, "「WAR」に戻すと、守備の得点の並び順が保たれる(D-131)");
await page.click("#stats-role button[data-value=pitcher]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("WAR(失点版)")), null, { timeout: 60000 });
screenRows = await waitStats("WAR(失点版)(高い順)", 60000);
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "war" })), `投手の WAR(失点版の高い順。投球回・失点版・FIP 版)が、計算本体と同じ(${screenRows.length}人)`);
// 選手のページの WAR と、チームのページの WAR の合計
await page.click("#stats-table tbody tr:first-child td.sticky .link");
await page.waitForFunction(() => document.querySelector("#player-name").textContent.length > 0);
await page.click("#player-kind button[data-value=war]");
await page.waitForFunction(() => document.querySelectorAll("#player-season td").length === 3);
const warPid = pyGame(`print(json.dumps(g.stats("pitcher", "war")["rows"][0]["player_id"]))`);
const pyWarPlayer = pyGame(`d = g.player(a["id"])["season"]["war"]
print(json.dumps([d["values"][c["key"]] for c in d["columns"]], ensure_ascii=False))`, JSON.stringify({ id: warPid }));
check(JSON.stringify(await page.$$eval("#player-season td", (tds) => tds.map((td) => td.textContent))) === JSON.stringify(pyWarPlayer), `選手のページの WAR(投球回・失点版・FIP 版)が、計算本体と同じ`);
await page.click("#player-info .link");
await page.waitForFunction(() => document.querySelectorAll("#team-war td").length === 4);
const warTid = pyGame(`print(json.dumps(g.player(a["id"])["player"]["team_id"]))`, JSON.stringify({ id: warPid }));
const pyTeamWar = pyGame(`w = g.team(a["id"])["war"]
print(json.dumps([w["batters"], w["pitchers_ra"], w["pitchers_fip"], w["total_ra"]]))`, JSON.stringify({ id: warTid }));
check(JSON.stringify(await page.$$eval("#team-war td", (tds) => tds.map((td) => td.textContent))) === JSON.stringify(pyTeamWar), `チームのページの WAR の合計(野手・投手別)が、計算本体と同じ(合計 ${pyTeamWar[3]})`);
await page.click("#screen-team .back");
await page.waitForFunction(() => !document.querySelector("#screen-player").hidden);
await page.click("#player-kind button[data-value=basic]"); // 後の確認は「基本」を前提にしている
await page.waitForFunction(() => document.querySelectorAll("#player-season td").length > 3);
await page.click("#screen-player .back");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0);
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("FIP")));
await page.click("#stats-table th button.sort >> text=FIP");
await waitStats("FIP(低い順)");

// 能力:オフのときは「オンにすると見られます」だけ
await page.click("#stats-kind button[data-value=ability]");
await page.waitForFunction(() => document.querySelector("#stats-info").textContent.includes("答え合わせモードをオンにすると見られます"));
check((await page.locator("#stats-table table").count()) === 0, "答え合わせモードがオフのとき、「能力」は表を出さない");
const offOptions = await page.$$eval("#stats-sort option", (os) => os.map((o) => o.textContent));
check(!offOptions.some((o) => HIDDEN.words.includes(o)) && !(await page.$$eval("#stats-sort optgroup", (gs) => gs.some((g) => g.label.includes("能力")))), "オフのとき、並び順の欄に能力の項目が出ない");
await grab();

// 選手のページ
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.startsWith("登板")));
await waitStats("FIP(低い順)"); // 能力 → 基本 と切り替えても、FIP の低い順(固定列)が保たれる
const pitcherName = (await statsOnScreen())[0][0];
await page.click("#stats-table tbody tr:first-child .link");
await page.waitForFunction((n) => document.querySelector("#player-name").textContent === n, pitcherName);
await page.waitForSelector("#player-games table");
const pid = pyGame(`print(json.dumps(g.stats("pitcher", sort="fip", order="asc")["rows"][0]["player_id"]))`);
const pyPlayer = pyGame(`d = g.player(a["id"])\nprint(json.dumps({"season": [d["season"]["tables"]["basic"]["values"][c["key"]] for c in d["season"]["tables"]["basic"]["columns"]], "games": [x["day"] for x in d["games"]]}, ensure_ascii=False))`, JSON.stringify({ id: pid }));
const seasonOnScreen = await page.$$eval("#player-season td", (tds) => tds.map((td) => td.textContent));
check(JSON.stringify(seasonOnScreen) === JSON.stringify(pyPlayer.season), `選手のページのシーズン通算が、計算本体と同じ(${pitcherName})`);
const gameDays = await page.$$eval("#player-games tbody td.sticky .link", (ls) => ls.map((l) => Number(l.textContent.replace("日目", ""))));
check(JSON.stringify(gameDays) === JSON.stringify(pyPlayer.games.slice(0, 20)), `試合ごとの成績が新しい順(${gameDays.length}試合)`);
await page.click("#player-kind button[data-value=ability]");
await page.waitForFunction(() => document.querySelector("#player-season").textContent.includes("答え合わせモードをオンにすると見られます"));
check(true, "選手のページの「能力」は、オフなら「答え合わせモードをオンにすると見られます」だけ");
await grab();
await page.click("#player-kind button[data-value=basic]");

// 試合のページ(文章ログが、確認用の形式と同じ内容か)
await page.click("#player-games tbody tr:first-child td.sticky .link");
await page.waitForFunction(() => document.querySelectorAll("#game-log li").length > 20);
const gameNo = pyGame(`print(json.dumps(g.player(a["id"])["games"][0]["game_no"]))`, JSON.stringify({ id: pid }));
const logOnScreen = await page.$$eval("#game-log li", (lis) => lis.map((li) => li.textContent));
const pyLog = pyGame(`
s = g.state.season
text = narrate(s.played[a["n"]].result, s.players, {t.id: t.name for t in s.league.teams})
print(json.dumps([l[2:] if l.startswith("- ") else l.strip()[2:] for l in text.splitlines() if l.startswith("- [") or l.startswith("  - 【投手交代】")], ensure_ascii=False))`, JSON.stringify({ n: gameNo }));
check(JSON.stringify(logOnScreen) === JSON.stringify(pyLog), `試合の文章ログが、確認用の形式(narrate)と同じ内容(${logOnScreen.length}行)`);
const marks = await page.$$eval("#game-pitchers tbody tr", (trs) => trs.map((tr) => tr.children[1].textContent).filter(Boolean));
check(marks.includes("勝") && marks.includes("敗"), `投手の成績に勝敗の印が出る(${marks.join("・")})`);
await grab();

// 試合のタブと、進行の画面の直近の日の試合
await page.click("#tabs button[data-tab=games]");
await page.waitForSelector("#games-list .game-card");
const cards = await page.locator("#games-list .game-card").count();
check(cards === 6, `試合のタブで、その日の試合の一覧が出る(${stoppedDay}日目・${cards}試合)`);
await page.click("#games-prev");
await page.waitForFunction((d) => document.querySelector("#games-day").value === String(d - 1), stoppedDay);
await page.click("#games-list .game-card");
await page.waitForFunction((d) => document.querySelector("#game-title").textContent.startsWith(`${d - 1}日目`), stoppedDay);
check(true, "前の日の試合を開ける");
await page.click("#tabs button[data-tab=progress]");
check((await page.locator("#last-day .game-card").count()) === 6, "進行の画面に、直近の日の全試合のスコアが出る");
await page.click("#last-day .game-card");
await page.waitForFunction((d) => document.querySelector("#game-title").textContent.startsWith(`${d}日目`), stoppedDay);
check(true, "直近の日の試合を押すと、試合のページへ移る");
// 球場のページ(②a。D-138)
const gameStadium = await page.textContent("#game-stadium");
check(gameStadium.startsWith("球場:") && gameStadium.includes("の本拠地"), `試合のページに球場名が出る(「${gameStadium}」)`);
await page.click("#game-stadium .link");
await page.waitForFunction(() => document.querySelector("#stadium-name").textContent.length > 0 && document.querySelectorAll("#stadium-record td").length > 0);
const homeTeamId = pyGame(`print(json.dumps(g.last_day_games()["games"][0]["home"]["team_id"]))`);
const stadiumPy = pyGame(`d = g.stadium(a["id"])\nprint(json.dumps([d["name"], str(d["games"]), str(d["home_runs"]), d["home_runs_per_game"], d["runs_per_game"]], ensure_ascii=False))`, JSON.stringify({ id: homeTeamId }));
const stadiumOnScreen = [await page.textContent("#stadium-name"), ...(await page.$$eval("#stadium-record td", (tds) => tds.map((td) => td.textContent)))];
check(JSON.stringify(stadiumOnScreen) === JSON.stringify(stadiumPy), `球場のページの実際の結果(試合・本塁打・本塁打/試合・得点/試合)が、計算本体と同じ(${stadiumPy[0]})`);
check((await page.textContent("#stadium-answers")).includes("答え合わせモードをオンにすると見られます") && !/\d\.\d{3}/.test(await page.textContent("#stadium-answers")), "オフのとき、球場のページに真の倍率が出ない");
// 本拠地とアウェイの比較の表と、推定(1シーズン目は「まだ推定できません」)(②b)
const cmpOnScreen = await page.$$eval("#stadium-compare tbody tr", (trs) => trs.map((tr) => [...tr.children].slice(1).map((td) => td.textContent)));
const cmpPy = pyGame(`d = g.stadium(a["id"])\nprint(json.dumps([[d["this_season"][k][c] for c in ("home", "away", "ratio")] for k in ("home_run", "babip", "runs")], ensure_ascii=False))`, JSON.stringify({ id: homeTeamId }));
check(JSON.stringify(cmpOnScreen) === JSON.stringify(cmpPy), `本拠地とアウェイの比較の表(本塁打/打席・BABIP・得点/打席)が、計算本体と同じ(本塁打の比 ${cmpPy[0][2]})`);
check((await page.textContent("#stadium-estimate")).includes("まだ推定できません"), "1シーズン目は、推定した球場補正の代わりに「まだ推定できません」と出る");
await page.click("#screen-stadium .back");
await page.click("#screen-game .back");

// 答え合わせモードがオフの間に届いたメッセージに、隠し情報がない
const offMessages = await page.evaluate(() => window.__messages.join("\n"));
const leaked = [...HIDDEN.words.filter((w) => offMessages.includes(w)), ...HIDDEN.keys.filter((k) => offMessages.includes(`"${k}"`))];
check(leaked.length === 0, `答え合わせモードがオフの間、裏の計算から届いたメッセージに隠し情報がない${leaked.length ? `(見つかった: ${leaked.join("、")})` : ""}`);

// 答え合わせモードをオンにする(確認が出る)→ 能力が見える → オフに戻すと消える
let dialogText = "";
page.once("dialog", (d) => { dialogText = d.message(); d.accept(); });
await page.click("#menu");
await page.check("input[name=answer-level][value='2']");
check(dialogText.includes("見ると、成績から実力を推理する楽しみが減ります"), "オンにするとき、確認が出る");
check(await page.isVisible("#answer-badge"), "オンの間は、上の帯に「答え合わせモード」と出る");
await page.click("#screen-settings .back");
await page.click("#tabs button[data-tab=stats]");
await page.click("#stats-kind button[data-value=ability]");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("「+」は 80 より上") && document.querySelectorAll("#stats-table tbody tr").length > 0);
const pyAbility = pyGame(`
from pennant import answers
t = answers.ability_table(g, "pitcher", 2, sort="fip", order="asc")
keys = ([t["extra_column"]["key"]] if t["extra_column"] else []) + [c["key"] for c in t["columns"]]
print(json.dumps([[r["name"]] + [r["values"][k] for k in keys] for r in t["rows"]], ensure_ascii=False))`);
screenRows = await statsOnScreen();
const abilityText = await page.textContent("#stats-table");
check(JSON.stringify(screenRows) === JSON.stringify(pyAbility), `オンのとき、「能力」の表が、並び順(FIP の低い順。固定列)を保ったまま出る(答え合わせ用の関数と同じ。${screenRows.length}人)`);
check(screenRows.every((r) => r.slice(2, 10).every((v) => /^(20|25|30|35|40|45|50|55|60|65|70|75|80)[+-]?$/.test(v))), "能力は 20〜80・5刻みで表示");
const onOptions = await page.$$eval("#stats-sort optgroup", (gs) => gs.map((g) => g.label));
check(onOptions.some((g) => g.includes("能力")), "オンのとき、並び順の欄に能力の項目も出る");
// 答え合わせモードがオンなら、球場のページに真の倍率が出る(段階1以上)
await page.click("#tabs button[data-tab=standings]");
await page.click("#standings-body tr:first-child .link");
await page.waitForFunction(() => document.querySelector("#team-info .link") !== null);
const firstTeamId = pyGame(`lg = next(l for l in g.standings()["leagues"] if any(r["is_mine"] for r in l["rows"]))\nprint(json.dumps(lg["rows"][0]["team_id"]))`);
await page.click("#team-info .link");
await page.waitForFunction(() => document.querySelectorAll("#stadium-answers td").length === 2);
const parkPy = pyGame(`from pennant import answers\na2 = answers.stadium_answers(g, a["id"], 2)\nprint(json.dumps([a2["home_run"]["text"], a2["babip"]["text"]]))`, JSON.stringify({ id: firstTeamId }));
const parkOnScreen = await page.$$eval("#stadium-answers td", (tds) => tds.map((td) => td.textContent));
check(JSON.stringify(parkOnScreen) === JSON.stringify(parkPy), `オンのとき、球場のページに真の倍率が出る(本塁打 ${parkPy[0]}・BABIP ${parkPy[1]}。答え合わせ用の関数と同じ)`);
await page.click("#screen-stadium .back");
await page.click("#screen-team .back");
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0);
await page.selectOption("#stats-sort", "stamina");
await page.waitForFunction(() => document.querySelector("#stats-info").textContent.includes("(高い順)") && !document.querySelector("#stats-info").textContent.includes("FIP"));
check((await headsOnScreen())[1] !== "FIP", "並び順の欄で能力の項目を選ぶと、その項目で並び替わり、固定列は消える");
check(HIDDEN.words.some((w) => abilityText.includes(w)), "オンのときは、能力の項目名・成長タイプなどが表示される");
await page.click("#menu");
await page.check("input[name=answer-level][value='0']");
await page.click("#screen-settings .back");
await page.waitForFunction(() => document.querySelector("#stats-info").textContent.includes("答え合わせモードをオンにすると見られます"));
const afterOff = await page.evaluate(() => document.documentElement.textContent);
const left = HIDDEN.words.filter((w) => afterOff.includes(w));
check(left.length === 0, `オフに戻すと、能力の表示が画面(隠れている画面も含む)から消える${left.length ? `(残っている: ${left.join("、")})` : ""}`);
check((await page.textContent("#stadium-answers")) === "", "オフに戻すと、球場のページの真の倍率も消える");
await page.click("#stats-kind button[data-value=basic]");
await page.click("#stats-role button[data-value=batter]");
await page.click("#tabs button[data-tab=progress]");

// ---- 5. 読み込み直して開く ----
await page.reload();
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
// 壊れたファイルは、分かりやすいエラーになる
const broken = join(work, "broken.sav");
execFileSync("python3", ["-c", `open(${JSON.stringify(broken)}, "wb").write(b"this is not a save file")`]);
await page.setInputFiles("#open-file-start", broken);
await page.waitForFunction(() => document.querySelector("#start-message").textContent.includes("読み込めません"));
check(true, `壊れたファイルは日本語のエラーになる(「${(await page.textContent("#start-message")).split("\n")[0]}」)`);
await page.setInputFiles("#open-file-start", first.path);
await page.waitForSelector("#screen-progress:not([hidden])");
check((await day()) === stoppedDay, `読み込むと、${stoppedDay}日目から再開する`);
check(!(await isDirty()), "読み込んだ直後は「未保存」でない");
check(flat(await standingsOnScreen()) === flat(shown), "読み込んだあとの順位表が、保存前と同じ");
// ゲーム中に壊れたファイルを開いても、今のゲームはそのまま
await page.setInputFiles("#open-file-top", broken);
await page.waitForFunction(() => document.querySelector("#progress-message").textContent.includes("今のゲームは、そのまま"));
check((await day()) === stoppedDay, "ゲーム中に壊れたファイルを開いても、今のゲームはそのまま");

// ---- 6. 最後まで ----
await page.click(".adv[data-days='0']");
await page.waitForFunction(() => document.querySelector("#progress-message").textContent.includes("シーズンが終わりました"), null, { timeout: 600000 });
check(await page.isDisabled(".adv[data-days='1']"), `最後まで進んだ(「${await page.textContent("#progress-message")}」)`);
await grab();
await standingsOnScreen();
const last = await saveFile("end");
const digest = python(`
import sys
from pennant import api
from pennant.fingerprint import _digest, season_record
g = api.Game.load(open(sys.argv[1], "rb").read())
print(g.state.season.day, g.state.my_team_id, _digest(season_record(g.state.season.result())))
`, last.path).trim().split(" ");
check(digest[0] === "125", "保存したファイルは125日目(最後)");
check(digest[2] === expected.season, `最後の結果が、指紋 (d) と同じ(${digest[2].slice(0, 16)}…)`);
results.push(`  自球団(セーブデータの中): ${digest[1]}。指紋には入らない`);
await standingsOnScreen(0);
// シーズンの最後で、成績の画面を開く時間(集計は進めた日の分だけ足してあるので、すぐ開く)
let s0 = Date.now();
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("規定") && document.querySelectorAll("#stats-table tbody tr").length > 0);
results.push(`  シーズンの最後で、個人成績を開くまで: ${((Date.now() - s0) / 1000).toFixed(2)} 秒`);
// 読み込み直したセーブデータ(125日目)では、最初の1回だけ集計を作る
await page.reload();
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
await page.setInputFiles("#open-file-start", last.path);
await page.waitForSelector("#screen-progress:not([hidden])");
s0 = Date.now();
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("規定") && document.querySelectorAll("#stats-table tbody tr").length > 0);
const firstOpen = (Date.now() - s0) / 1000;
s0 = Date.now();
await page.click("#tabs button[data-tab=progress]");
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0);
results.push(`  125日目のセーブデータを開いて、個人成績を開くまで: 1回目 ${firstOpen.toFixed(2)} 秒 / 開き直し ${((Date.now() - s0) / 1000).toFixed(2)} 秒`);
const frame2 = await page.evaluate(() => new Promise((r) => { const s = performance.now(); requestAnimationFrame(() => r(performance.now() - s)); }));
check(frame2 < 500, `成績の表示中も画面が固まらない(次の描画まで ${frame2.toFixed(0)} ミリ秒)`);
await grab();

// ---- 6b. 年度の確定(F2。D-185)→ オフの結果 → 2シーズン目 → 過去シーズン・通算の成績 → 保存と読み込み ----
// Python 側:125日目のセーブデータを読み、同じように年度を確定して1日進めた状態(同じシードなので同じ結果になるはず)
function pyYear2(code, ...args) {
  return JSON.parse(python(`
import json, sys
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
a = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
g.year_end()
# 画面と同じ操作(F3-1):最初に外せる 1 人を自由契約 → ドラフトへ → 自分の番まで → 一覧の先頭を指名 → 残りを自動で市場へ → おまかせ
v = g.offseason_view()
g.offseason_release([next(r["player_id"] for r in v["roster"] if r["can_release"])])
g.offseason_next()
v = g.offseason_advance()
g.offseason_pick(v["pool"][0]["player_id"])
g.offseason_next()
g.offseason_auto()
summary = g.offseason_summary()
g.advance(1)
${code}
`, last.path, ...args));
}
await page.click("#tabs button[data-tab=progress]");
check((await page.textContent("#day-text")).startsWith("1シーズン目 全125日 終了") && (await page.isVisible("#year-end-box")), `シーズンが終わると、進行の画面に「年度を確定する」が出る(「${await page.textContent("#day-text")}」)`);
await page.click("#year-end");
await page.waitForFunction(() => !document.querySelector("#screen-yearend").hidden && document.querySelector("#yearend-title").textContent.length > 0);
const champsPy = JSON.parse(python(`
import json, sys
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
print(json.dumps([f"{c['league_name']} 優勝:{'・'.join(c['teams'])}" for c in g.year_end_preview()["champions"]], ensure_ascii=False))`, last.path));
const champsOnScreen = await page.$$eval("#yearend-champions p", (ps) => ps.map((p) => p.textContent));
check(JSON.stringify(champsOnScreen) === JSON.stringify(champsPy) && (await page.textContent("#yearend-note")).includes("この操作は戻せません"), `確認の画面に、優勝チームと「戻せません」の説明が出る(${champsPy.join(" / ")})`);
check((await page.textContent("#yearend-dirty")).includes("保存済み"), "確認の画面に、保存の状態(保存済み)が出る");
const [beforeDl] = await Promise.all([page.waitForEvent("download"), page.click("#yearend-save")]);
await beforeDl.saveAs(join(work, `before-yearend-${beforeDl.suggestedFilename()}`));
await page.waitForFunction(() => document.querySelector("#yearend-dirty").textContent.includes("保存済み"));
check(!(await page.locator("#screen-yearend").isHidden()), "確認の画面の「先に保存する」で保存しても、確認の画面に留まる");
const ye0 = Date.now();
await page.click("#yearend-go");
// 操作する球団があるので、オフの手続き(自由契約 → ドラフト → 市場)の画面になる(F3-1。D-201)
await page.waitForFunction(() => !document.querySelector("#screen-procedure").hidden && document.querySelector("#proc-body").children.length > 0, null, { timeout: 120000 });
const yeSeconds = (Date.now() - ye0) / 1000;
check((await page.textContent("#proc-info")).includes("自由契約") && (await page.$$eval("#proc-steps li", (ls) => ls.length)) === 4, `年度を確定すると、オフの手続きの画面(自由契約の段階)になる(${yeSeconds.toFixed(1)} 秒)`);
const rosterRows = await page.$$eval("#proc-body tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td .link").textContent, ...[...tr.children].slice(1).map((td) => td.textContent)]));
const rosterPy = JSON.parse(python(`
import json, sys
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
g.year_end()
v = g.offseason_view()
print(json.dumps([[r["name"], r["position_label"], f"{r['age']}歳", r["scouting"]["overall_text"]] for r in v["roster"]], ensure_ascii=False))`, last.path));
check(rosterRows.length === rosterPy.length && rosterRows.every((r, i) => r[0] === rosterPy[i][0] && r[1] === rosterPy[i][1] && r[2] === rosterPy[i][2] && r[3] === rosterPy[i][3]), `自由契約の画面に、自球団の全選手(${rosterRows.length}人)がスカウト評価(推定 ± 幅)つきで出て、計算本体と同じ`);
check(!HIDDEN.keys.some((k) => JSON.stringify(rosterRows).includes(`"${k}"`)) && (await page.$$eval("#proc-body tbody tr th, #proc-body thead th", (ths) => ths.map((t) => t.textContent))).includes("天井"), "自由契約の表に天井(S〜D)の列があり、真の能力の項目名はない");
page.on("dialog", (d) => d.accept().catch(() => {})); // これ以降の確認(自由契約・次の手続き・おまかせ・答え合わせモード)はすべて承諾する
const firstBox = page.locator("#proc-body input[type=checkbox]:not([disabled])").first();
await firstBox.check();
await page.click("#proc-release");
await page.waitForFunction(() => document.querySelector("#proc-body").textContent.includes("済んでいます"));
check(true, "選手に印を付けて「自由契約にする」を押すと、確認のあと手放せる");
await page.click("#proc-next");
await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("ドラフト"));
const draftHead = await page.textContent("#proc-body");
check(draftHead.includes("1 / 6 巡目") && draftHead.includes("候補 108 人"), "「次の手続きへ」でドラフトに進み、1 / 6 巡目と候補 108 人が出る");
await page.locator("#proc-body button", { hasText: "次の自分の番まで進める" }).click();
await page.waitForFunction(() => document.querySelector("#proc-body").textContent.includes("あなたの番"));
const poolPy = JSON.parse(python(`
import json, sys
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
g.year_end()
v = g.offseason_view()
g.offseason_release([next(r["player_id"] for r in v["roster"] if r["can_release"])])
g.offseason_next()
v = g.offseason_advance()
print(json.dumps([[p["name"], p["scouting"]["overall_text"], p["scouting"]["ceiling"]] for p in v["pool"][:10]] + [v["round"], v["is_my_turn"]], ensure_ascii=False))`, last.path));
const poolRows = await page.$$eval("#proc-body tbody tr:not(.detail)", (trs) => trs.slice(0, 10).map((tr) => [tr.querySelector("td .link").textContent, [...tr.children][3].textContent, [...tr.children][4].textContent.slice(0, 1)]));
check(JSON.stringify(poolRows) === JSON.stringify(poolPy.slice(0, 10)) && poolPy[11] === true, `「次の自分の番まで進める」で自分の番になり、候補の一覧(総合の推定値の高い順)が計算本体と同じ(先頭 ${poolRows[0][0]} ${poolRows[0][1]})`);
await page.click("#proc-body .name-cell .link >> nth=0");
await page.waitForSelector("#proc-body tr.detail");
check((await page.textContent("#proc-body tr.detail")).includes("±"), "名前を押すと、項目別の推定値 ± ふれ幅が出る");
await page.selectOption("#proc-body select >> nth=1", "SP");
await page.waitForFunction(() => [...document.querySelectorAll("#proc-body tbody tr:not(.detail) td:nth-child(2)")].every((td) => td.textContent === "先発"));
check(true, "ポジションで絞り込める(先発だけ)");
await page.selectOption("#proc-body select >> nth=1", "");
await page.selectOption("#proc-body select >> nth=0", "age");
await page.waitForFunction(() => { const ages = [...document.querySelectorAll("#proc-body tbody tr:not(.detail) td:nth-child(3)")].map((td) => parseInt(td.textContent)); return ages.every((a, i) => i === 0 || a >= ages[i - 1]); });
check(true, "並べ替えができる(年齢が若い順)");
await page.selectOption("#proc-body select >> nth=0", "overall");
await page.waitForFunction(() => document.querySelector("#proc-body .pick-btn") !== null);
const pk0 = Date.now();
await page.click("#proc-body .pick-btn >> nth=0");
await page.waitForFunction(() => document.querySelector("#proc-body").textContent.includes("2 巡目"), null, { timeout: 60000 });
check((await page.textContent("#proc-history")).includes("ドラフト 1 巡"), `「指名」で入団し、次の自分の番(2 巡目)まで AI が進む(${((Date.now() - pk0) / 1000).toFixed(1)} 秒)。履歴に 1 巡目の指名が出る`);
await page.click("#proc-next");
await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("自由契約市場"), null, { timeout: 60000 });
check((await page.textContent("#proc-body")).includes("前の球団"), "ドラフトの残りを自動で進めて市場へ。市場の表には前の球団の列がある");
const au0 = Date.now();
await page.click("#proc-auto");
await page.waitForFunction(() => !document.querySelector("#screen-offseason").hidden && document.querySelectorAll("#offseason-retired tbody tr").length > 0, null, { timeout: 120000 });
check(true, `「おまかせ」で残りを自動で進め、オフの結果の画面になる(${((Date.now() - au0) / 1000).toFixed(1)} 秒)`);
const offPy = pyYear2(`print(json.dumps([summary["counts"]["retired"], summary["counts"]["rookies"], summary["counts"]["players"], [r["name"] for r in summary["retired"]], [r["name"] for r in summary["rookies"]]], ensure_ascii=False))`);
const retiredOnScreen = await page.$$eval("#offseason-retired tbody tr td .link", (as) => as.map((a) => a.textContent));
const rookiesOnScreen = await page.$$eval("#offseason-rookies tbody tr td .link", (as) => as.map((a) => a.textContent));
const countsText = await page.textContent("#offseason-counts");
check(JSON.stringify(retiredOnScreen) === JSON.stringify(offPy[3]) && JSON.stringify(rookiesOnScreen) === JSON.stringify(offPy[4]) && countsText.includes(`引退 ${offPy[0]}人`) && countsText.includes(`新人 ${offPy[1]}人`) && countsText.includes(`${offPy[2]}人`), `オフの結果(引退 ${offPy[0]}人・入団 ${offPy[1]}人・選手 ${offPy[2]}人)が、計算本体で同じ操作をした結果と同じ`);
check((await page.textContent("#offseason-answers")).includes("答え合わせモードをオンにすると") && !/[+-]\d\.\d/.test(await page.textContent("#offseason-answers")), "オフのとき、オフの結果に能力の増減は出ない");
// 入団した新人の選手ページに、入団時のスカウト評価が出る(答え合わせモードがオフなら真の能力は出ない)
await page.click("#offseason-rookies tbody tr td .link >> nth=0");
await page.waitForFunction(() => !document.querySelector("#screen-player").hidden && !document.querySelector("#player-scouting-box").hidden);
const scoutText = await page.textContent("#player-scouting");
check(scoutText.includes("±") && scoutText.includes("天井") && (await page.textContent("#player-scouting-note")).includes("答え合わせモードをオンにすると"), "入団した選手のページに、入団時のスカウト評価(推定 ± 幅、天井)が出る。オフなら真の能力は出ない");
await page.click("#screen-player .back");
check(await isDirty(), "年度を確定したあとは「未保存」になる");
await grab();
await page.click("#screen-offseason .back");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("2シーズン目 0日目"));
check(await page.isHidden("#year-end-box") && (await page.textContent("#progress-message")).includes("2シーズン目が始まりました"), `確定のあと、進行の画面は2シーズン目の0日目(「${await page.textContent("#progress-message")}」)`);
await page.click(".adv[data-days='1']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("2シーズン目 1日目"));
check(true, "2シーズン目を1日進められる");
// ドラフトの振り返り(D-216):進行の画面のボタンから開く。計算本体と同じ行。オフなら真の値は出ない
await page.click("#open-review");
await page.waitForFunction(() => !document.querySelector("#screen-review").hidden && document.querySelectorAll("#review-table tbody tr").length > 0);
const reviewPy = pyYear2(`v = g.draft_review()\nprint(json.dumps([v["team_name"], v["year"], v["years"], [[r["name"], r["route_label"], r["entry_text"], r["status_label"], r["war"]] for r in v["rows"]]], ensure_ascii=False))`);
const reviewRows = async () => page.$$eval("#review-table tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td").textContent, tr.children[1].textContent, tr.children[4].textContent, tr.children[6].textContent, tr.children[8].textContent]));
let reviewOnScreen = await reviewRows();
const reviewHead = await page.$$eval("#review-table thead th", (ths) => ths.map((th) => th.textContent));
check(JSON.stringify(reviewOnScreen) === JSON.stringify(reviewPy[3]) && (await page.inputValue("#review-year")) === String(reviewPy[1]) && (await page.textContent("#review-team option:checked")).startsWith(reviewPy[0]), `ドラフトの振り返り:自球団(${reviewPy[0]})の最新の年度(${reviewPy[1]}シーズン目に入団)の ${reviewOnScreen.length} 人が、計算本体と同じ(経路・入団時の総合 ± ふれ幅・今の所属・WAR 累計)`);
check(reviewHead.length === 9 && !reviewHead.some((h) => h.includes("真の") || h.includes("差")) && !(await page.textContent("#review-answers")).trim() && (await page.textContent("#review-answers-note")).includes("答え合わせモードをオンにすると"), "オフのとき、振り返りに真の総合・差・実際の天井の列は出ない");
check(reviewOnScreen.every((r) => /^ドラフト \d 巡$|^市場$|^自動補充$/.test(r[1]) && /^\d+ ± \d+$/.test(r[2])), "経路は「ドラフト n 巡」「市場」「自動補充」、入団時の総合は「推定値 ± ふれ幅」の形");
await grab();
// 別の球団を選ぶと、その球団の入団者になる
await page.selectOption("#review-team", "T05");
await page.waitForFunction(() => document.querySelector("#review-note").textContent.includes("T05") || document.querySelectorAll("#review-table tbody tr").length > 0, null, { timeout: 60000 });
await page.waitForFunction((name) => document.querySelector("#review-note").textContent.includes(name), await page.textContent("#review-team option[value=T05]"));
const reviewT05Py = pyYear2(`v = g.draft_review("T05")\nprint(json.dumps([[r["name"], r["route_label"], r["entry_text"], r["status_label"], r["war"]] for r in v["rows"]], ensure_ascii=False))`);
reviewOnScreen = await reviewRows();
check(JSON.stringify(reviewOnScreen) === JSON.stringify(reviewT05Py), `球団を切り替えると、その球団の入団者(${reviewOnScreen.length} 人)が出る(観戦でも見られる形。計算本体と同じ)`);
// 答え合わせモードをオンにすると、真の総合・差・実際の天井と、球団ごとの「見る目」の目安が出る
await page.click("#screen-review .back");
await page.click("#menu");
await page.check("input[name=answer-level][value='1']");
await page.click("#screen-settings .back");
await page.click("#open-review");
await page.waitForFunction(() => !document.querySelector("#screen-review").hidden && document.querySelectorAll("#review-answers tbody tr").length > 0, null, { timeout: 60000 });
const reviewAnsPy = pyYear2(`from pennant import answers\nv = g.draft_review()\na = answers.draft_review_answers(g, None, None, 1)\nprint(json.dumps([[a["players"][r["player_id"]]["overall"], a["players"][r["player_id"]]["diff"], a["players"][r["player_id"]]["actual_ceiling"]] if r["player_id"] in a["players"] else ["-", "-", "-"] for r in v["rows"]] + [[a["league"]["count"], a["league"]["mean"], a["league"]["sd"]]], ensure_ascii=False))`);
const reviewTruth = await page.$$eval("#review-table tbody tr", (trs) => trs.map((tr) => [tr.children[9].textContent, tr.children[10].textContent, tr.children[11].textContent]));
const leagueRow = await page.$$eval("#review-answers tbody tr:last-child td", (tds) => tds.map((td) => td.textContent));
check(JSON.stringify(reviewTruth) === JSON.stringify(reviewAnsPy.slice(0, -1)) && leagueRow[0].startsWith("リーグ全体") && leagueRow[1] === String(reviewAnsPy.at(-1)[0]) && leagueRow[2] === reviewAnsPy.at(-1)[1] && leagueRow[3] === reviewAnsPy.at(-1)[2], `オンのとき、今の真の総合・差・実際の天井と、球団ごとの差の平均・標準偏差(リーグ全体 ${leagueRow[1]} 人、差の平均 ${leagueRow[2]})が、答え合わせ用の関数と同じ`);
check((await page.$$eval("#review-answers tbody tr", (trs) => trs.length)) === 13, "「見る目」の目安の表は 12 球団 + リーグ全体");
await grab();
await page.click("#screen-review .back");
await page.click("#menu");
await page.check("input[name=answer-level][value='0']");
await page.click("#screen-settings .back");
await page.click("#open-review");
await page.waitForFunction(() => !document.querySelector("#screen-review").hidden && document.querySelectorAll("#review-table tbody tr").length > 0 && document.querySelectorAll("#review-table thead th").length === 9);
check(!(await page.textContent("#review-answers")).trim(), "オフに戻すと、振り返りの真の値と「見る目」の表は消える");
await page.click("#screen-review .back");
// 成績:シーズンの選択(今シーズン・1シーズン目・通算)
await page.click("#tabs button[data-tab=stats]");
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0 || document.querySelector("#stats-table").textContent.includes("条件に合う選手"));
const seasonOptions = await page.$$eval("#stats-season option", (os) => os.map((o) => o.textContent));
check(seasonOptions.length === 3 && seasonOptions[1] === "1シーズン目" && seasonOptions[2] === "通算" && (await page.isVisible("#stats-season")), `成績の画面に、シーズンの選択(${seasonOptions.join("・")})が出る`);
const pyStats2 = (args) => pyYear2(`t = g.stats(**a)\nkeys = ([t["extra_column"]["key"]] if t["extra_column"] else []) + [c["key"] for c in t["columns"]]\nprint(json.dumps([[r["name"]] + [r["values"][k] for k in keys] for r in t["rows"]], ensure_ascii=False))`, JSON.stringify(args));
await page.selectOption("#stats-season", "1");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("1シーズン目の成績"));
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats2({ role: "batter", kind: "basic", season: "1" })), `1シーズン目を選ぶと、確定した1シーズン目の成績が出る(${screenRows.length}人。計算本体と同じ)`);
await page.click("#stats-kind button[data-value=war]");
await page.waitForFunction(() => document.querySelector("#stats-baseline").textContent.includes("確定した WAR"));
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats2({ role: "batter", kind: "war", season: "1" })), `1シーズン目の WAR も、確定した値(${screenRows.length}人)`);
await page.selectOption("#stats-season", "career");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("通算"));
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats2({ role: "batter", kind: "war", season: "career" })), `通算の WAR(各シーズンの合計)が、計算本体と同じ(${screenRows.length}人)`);
await page.click("#stats-kind button[data-value=saber]");
// 「基本」で使っていた並び順(打率)は、切り替えても保たれる(D-131)ので、名前の隣に打率の固定列が出る
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("通算") && document.querySelector("#stats-baseline").textContent.includes("足し合わせ") && document.querySelector("#stats-info").textContent.includes("並び順:打率"));
screenRows = await statsOnScreen();
const careerSaber = pyStats2({ role: "batter", kind: "saber", season: "career", sort: "avg" });
const careerDiff = screenRows.findIndex((r, i) => JSON.stringify(r) !== JSON.stringify(careerSaber[i]));
check(careerDiff < 0 && screenRows.length === careerSaber.length, `通算のセイバー(元の数の合計から今の基準値で計算。打率の並び順を保ったまま)が、計算本体と同じ(${screenRows.length}人)${careerDiff >= 0 ? `(違い: ${JSON.stringify(screenRows[careerDiff])} / ${JSON.stringify(careerSaber[careerDiff])})` : ""}`);
await page.selectOption("#stats-season", "current");
await page.waitForFunction(() => !document.querySelector("#stats-rule").textContent.includes("通算") && document.querySelector("#stats-baseline").textContent.includes("前のシーズンまで(1シーズン分)"));
check(true, "2シーズン目の注記に、球場補正が前のシーズンまで(1シーズン分)の推定であることが出る");
// 選手のページ:年度別の成績
await page.selectOption("#stats-season", "1");
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => document.querySelector("#stats-rule").textContent.includes("1シーズン目の成績") && document.querySelector("#stats-info").textContent.includes("打率"));
await page.click("#stats-table tbody tr:first-child td .link");
await page.waitForFunction(() => !document.querySelector("#screen-player").hidden && document.querySelectorAll("#player-history tbody tr").length > 0);
const topId = pyYear2(`print(json.dumps(g.stats("batter", "basic", season="1")["rows"][0]["player_id"]))`);
const histPy = pyYear2(`h = g.player(a["id"])["history"]\nprint(json.dumps([[r["label"] if r["season"] == "career" else str(r["year"])] + [r["tables"]["basic"][c["key"]] for c in h["columns"]["basic"]] for r in h["rows"]], ensure_ascii=False))`, JSON.stringify({ id: topId }));
const histOnScreen = await page.$$eval("#player-history tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td span").textContent, ...[...tr.children].slice(1).map((td) => td.textContent)]));
const histSame = histOnScreen.length === histPy.length && histOnScreen.every((r, i) => r.slice(1).join("|") === histPy[i].slice(1).join("|"));
check(histSame, `選手のページに年度別の成績(${histOnScreen.length}行:1シーズン目・進行中・通算)が出て、計算本体と同じ`);
await page.click("#screen-player .back");
// 球場のページ:2シーズン目は、推定した球場補正が出る
await page.click("#tabs button[data-tab=standings]");
await page.click("#standings-body tr:first-child .link");
await page.waitForFunction(() => document.querySelector("#team-info .link") !== null);
await page.click("#team-info .link");
await page.waitForFunction(() => document.querySelectorAll("#stadium-estimate td").length > 0);
check((await page.textContent("#stadium-estimate-note")).includes("前のシーズンまで(1シーズン分)") && /\d\.\d{3}/.test(await page.textContent("#stadium-estimate")), "2シーズン目の球場のページに、1シーズン分の結果から推定した球場補正が出る");
await page.click("#screen-stadium .back");
await page.click("#screen-team .back");
// 保存 → 計算本体で読む:2シーズン目・履歴1つ・打席ログは今シーズンの分だけ(D-189)。1シーズン目の成績は確定した値のまま
await page.click("#tabs button[data-tab=progress]");
const year2 = await saveFile("year2");
const y2 = JSON.parse(python(`
import io, json, sys, zipfile
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
names = sorted(n for n in zipfile.ZipFile(io.BytesIO(open(sys.argv[1], "rb").read())).namelist() if n.startswith("logs/"))
from pennant.savegame import SAVE_FORMAT_VERSION
t = g.stats("batter", "basic", season="1")
print(json.dumps([g.state.year, len(g.state.history), names, [[r["name"], r["values"]["avg"]] for r in t["rows"][:5]], SAVE_FORMAT_VERSION], ensure_ascii=False))
`, year2.path));
const y2Py = pyYear2(`t = g.stats("batter", "basic", season="1")\nprint(json.dumps([[r["name"], r["values"]["avg"]] for r in t["rows"][:5]], ensure_ascii=False))`);
check(y2[0] === 2 && y2[1] === 1 && JSON.stringify(y2[2]) === JSON.stringify(["logs/season-2.jsonl"]) && JSON.stringify(y2[3]) === JSON.stringify(y2Py), `保存したファイル(版 ${y2[4]})は2シーズン目・履歴1つ・打席ログは今シーズンの分だけ。1シーズン目の成績は確定した値のまま`);
const y2Messages = await page.evaluate(() => window.__messages.join("\n"));
const leakedY2 = [...HIDDEN.words.filter((w) => y2Messages.includes(w)), ...HIDDEN.keys.filter((k) => y2Messages.includes(`"${k}"`))];
check(leakedY2.length === 0, `年度の確定の前後に届いたメッセージにも、隠し情報がない${leakedY2.length ? `(見つかった: ${leakedY2.join("、")})` : ""}`);
// 答え合わせモードをオンにすると、オフの結果に能力の増減が出る
await page.click("#menu");
await page.check("input[name=answer-level][value='1']");
await page.click("#screen-settings .back");
await page.click("#tabs button[data-tab=stats]");
await page.selectOption("#stats-season", "current");
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0 || document.querySelector("#stats-table").textContent.includes("条件に合う選手"));
// オフの結果のページは、進行の画面から開き直せないので、確定の直後に出したページと同じ内容を問い合わせで確かめる
const ansPy = pyYear2(`from pennant import answers\nx = answers.offseason_answers(g, None, 1)\nprint(json.dumps([len(x["players"]), x["players"][0]["mean_change"]]))`);
check(ansPy[0] > 700 && /^[+-]\d+\.\d$/.test(ansPy[1]), `答え合わせ用の関数で、残った選手(${ansPy[0]}人)の能力の増減が見られる(先頭 ${ansPy[1]})`);
await page.click("#menu");
await page.check("input[name=answer-level][value='0']");
await page.click("#screen-settings .back");
// 1シーズン目の終わりのセーブデータに戻して、以降の確認を続ける
await page.reload();
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
await page.setInputFiles("#open-file-start", last.path);
await page.waitForSelector("#screen-progress:not([hidden])");

// 指標の解説のページ(指標の定義データと同じ)
await page.click("#menu");
await page.click("#open-guide");
await page.waitForSelector("#guide-body .guide-item");
const guideOnScreen = await page.$$eval("#guide-body .guide-item", (items) => items.map((i) => [i.dataset.key, i.querySelector("h3").textContent, i.querySelector(".description").textContent]));
const guidePy = JSON.parse(python(`
import json
from pennant.api import metrics_config
c = metrics_config()
print(json.dumps([[k, c.metrics[k]["name"], c.metrics[k]["description"]] for k in c.in_category("basic") + c.in_category("saber")], ensure_ascii=False))`));
check(JSON.stringify(guideOnScreen) === JSON.stringify(guidePy), `「指標の解説」ページが、指標の定義データと同じ(${guideOnScreen.length}個の指標)`);
check((await page.textContent("#guide-body")).includes("式(打者):(四球の重み × 四球"), "指標の解説に、式が日本語で出る");
await grab();
await page.click("#screen-guide .back");
await page.click("#screen-settings .back");

// 速い選択肢(既定値を使う)
await page.reload();
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
await page.click("#go-new");
await page.waitForSelector("#screen-new:not([hidden])");
await page.click("details summary");
await page.check("input[name=baseline-mode][value=default]");
const fastStart = Date.now();
await page.click("#new-start");
await page.waitForSelector("#screen-progress:not([hidden])");
const fastSeconds = (Date.now() - fastStart) / 1000;
await page.click(".adv[data-days='1']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("1シーズン目 1日目"));
await page.click("#tabs button[data-tab=stats]");
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => document.querySelector("#stats-baseline").textContent.includes("設定ファイルの既定値"));
check(fastSeconds < trialSeconds, `「既定値を使う(速い)」なら、すぐ始まる(${fastSeconds.toFixed(1)} 秒。事前運転 25 年を含む。試運転ありは ${trialSeconds.toFixed(1)} 秒)。注記も「設定ファイルの既定値」になる`);
await grab();

// ---- 7. 隠し情報・通信・保存領域 ----
const allText = texts.join("\n") + (await page.evaluate(() => document.documentElement.textContent));
const found = HIDDEN.words.filter((w) => allText.includes(w));
check(found.length === 0, `答え合わせモードがオフの画面の文字に、能力の項目名・成長タイプ・生成時の型がない${found.length ? `(見つかった: ${found.join("、")})` : ""}`);
const laterMessages = await page.evaluate(() => window.__messages.join("\n"));
const leakedLater = [...HIDDEN.words.filter((w) => laterMessages.includes(w)), ...HIDDEN.keys.filter((k) => laterMessages.includes(`"${k}"`))];
check(leakedLater.length === 0, "読み込み直したあと(オフのまま)届いたメッセージにも、隠し情報がない");
check(allText.includes(placeholder1), `空欄の球団は、架空の初期名になる(2番目の球団: ${placeholder1})`);
// blob: は、ページの中で作った一時的な URL(裏の計算の起動に使う)。通信ではない
const hosts = [...new Set(requests.filter((r) => !r.url.startsWith("blob:")).map((r) => new URL(r.url).host))];
const pageHost = new URL(URL0).host;
check(hosts.every((h) => h === pageHost || h === "cdn.jsdelivr.net"), `通信先は、このページと Pyodide の配布元だけ(${hosts.join("、")})`);
check(!requests.some((r) => decodeURIComponent(r.url).includes(NAME) || r.body.includes(NAME)), "入力した球団名は、どの通信にも入っていない");
check(!first.name.includes(NAME) && !last.name.includes(NAME), "入力した球団名は、ファイル名に入っていない");
const storage = await page.evaluate(async () => ({
  localStorage: localStorage.length,
  sessionStorage: sessionStorage.length,
  indexedDB: (await indexedDB.databases()).length,
  cookie: document.cookie.length,
  cacheStorage: (await caches.keys()).length,
}));
check(Object.values(storage).every((v) => v === 0), `ブラウザの保存領域は空のまま(${JSON.stringify(storage)})`);
const paths = [...new Set(requests.filter((r) => !r.url.startsWith("blob:")).map((r) => { const u = new URL(r.url); return u.host === pageHost ? u.pathname : u.href.split("?")[0]; }))];
await browser.close();

console.log("# ブラウザでの通しの確認(412×924)");
console.log(results.join("\n"));
console.log(`\n## 通信した先(${paths.length} 件)`);
console.log(paths.map((p) => `- ${p}`).join("\n"));
console.log(`\n${failed ? `× ${failed} 件の問題があります` : "○ すべて確認できました"}`);
process.exit(failed ? 1 : 0);
