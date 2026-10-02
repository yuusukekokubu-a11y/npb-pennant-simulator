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
const HIDDEN_WORDS = ["能力", "潜在", "成長", "隠し", "ミート", "パワー", "選球眼", "走力", "スタミナ", "制球", "球威", "奪三振力", "rating", "potential", "archetype", "growth"];
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

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 412, height: 924 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, acceptDownloads: true });
const page = await context.newPage();
const requests = [];
context.on("request", (r) => requests.push({ url: r.url(), body: r.postData() || "" }));
page.on("pageerror", (e) => check(false, `ページのエラー: ${e.message}`));
const texts = []; // 画面に出た文字(あとで隠し情報の言葉を探す)
const grab = async () => texts.push(await page.evaluate(() => document.body.innerText));
async function day() {
  const text = await page.textContent("#day-text");
  if (text.includes("終了")) return 125;
  const m = text.match(/^(\d+)日目/);
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
await page.click("#new-start");
await page.waitForSelector("#screen-progress:not([hidden])");
const mineName = await page.textContent("#mine-name");
check(mineName === placeholder7, `選んだ8番目の球団が自球団になる(${mineName})`);
check((await page.textContent("#day-text")).startsWith("0日目 / 125日"), `進行の画面:「${await page.textContent("#day-text")}」`);
check(await isDirty(), "新規開始のあとは「未保存」(閉じる前に警告が出る)");
check(await page.isVisible("#dirty"), "「未保存の変更があります」が表示される");

// ---- 3. 進行 ----
await page.click(".adv[data-days='1']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("1日目"));
await page.click(".adv[data-days='7']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("8日目"));
check(true, "「1日」「1週間」で、1日目・8日目に進む");
await grab();
// 最後まで → 少し待って止める。止めている間も画面が動くかを確かめる
await page.click(".adv[data-days='0']");
await page.waitForFunction(() => /^(1[2-9]|[2-9]\d)日目/.test(document.querySelector("#day-text").textContent));
const frame = await page.evaluate(() => new Promise((r) => { const s = performance.now(); requestAnimationFrame(() => r(performance.now() - s)); }));
check(frame < 500, `進めている途中も画面が固まらない(次の描画まで ${frame.toFixed(0)} ミリ秒)`);
await page.click("#stop");
await page.waitForFunction(() => document.querySelector("#progress-message").textContent.includes("止めました"), null, { timeout: 30000 });
const stoppedDay = await day();
check(stoppedDay > 8 && stoppedDay < 125, `「止める」で、日の区切りで止まる(${stoppedDay}日目。「${await page.textContent("#progress-message")}」)`);
check(!(await page.isDisabled(".adv[data-days='1']")), "止めたあと、また進められる");

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

// ---- 7. 隠し情報・通信・保存領域 ----
const allText = texts.join("\n") + (await page.evaluate(() => document.documentElement.textContent));
const found = HIDDEN_WORDS.filter((w) => allText.includes(w));
check(found.length === 0, `画面の文字に、能力値・隠し情報の言葉がない${found.length ? `(見つかった: ${found.join("、")})` : ""}`);
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
