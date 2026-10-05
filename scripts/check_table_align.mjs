// 表の列のずれの確認(D-267・D-268)。ブラウザで表の要素の位置を測り、次を確かめる。
//   1. 見出しの行と本体の行で、固定列(名前の列・並べ替えた指標の固定列)の右端の線が一致する(許容 1px)。横にずらした状態でも測る
//   2. 並べ替えた列の見出し(▲▼ を除いた文字)の右端と、数値の右端が合う(許容 2px)
//   3. 本体の最初のスクロール列(固定列のすぐ右の列)の値の左に、余白がある
// 対象:個人成績(打者・投手 × 基本・セイバー・WAR・能力 × 並べ替えの指標 × シーズン)、チーム、順位、オフの手続き(契約・FA・ドラフト・市場。①a)、ドラフトの振り返り。
//
// 使い方(計算本体を含むサイトを build_web.py で作り、http.server で配ってから):
//   WIDTH=390 PLAYWRIGHT_MODULE=.../playwright/index.mjs node scripts/check_table_align.mjs "http://127.0.0.1:8765/?pyodide=local"
// 幅を省くと 360・390・412・768・1280 を順に確かめる。セーブデータは Python で作る(架空の球団・選手だけ)。
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const ROOT = new URL("..", import.meta.url).pathname;
const URL_ = process.argv[2] || "http://127.0.0.1:8765/?pyodide=local";
const WIDTHS = process.env.WIDTH ? [Number(process.env.WIDTH)] : [360, 390, 412, 768, 1280];
const LINE_TOL = 1;
const TEXT_TOL = 2;
const GAP_MIN = 8; // 最初のスクロール列の値と固定列の線の間(修正前は 5px。変数 --table-first-scroll-gap は 12px)
const SHOTS = process.env.SHOTS || ""; // 指定すると、そのフォルダに画面の写しを保存する

const work = mkdtempSync(join(tmpdir(), "align-"));
const saves = { season: join(work, "season.sav"), offseason: join(work, "offseason.sav") };
execFileSync("python3", ["-c", `
import sys
from pennant import api
from pennant.savegame import save_game
g = api.Game.new(1, [None] * 12, 0, season_seed=1, baselines="default", money_rule="standard")
g.advance(g.state.season.total_days); g.year_end(); g.offseason_auto(); g.advance(40)
open(sys.argv[1], "wb").write(save_game(g.state))
g = api.Game.new(2, [None] * 12, 0, season_seed=2, baselines="default", money_rule="standard")
g.advance(g.state.season.total_days); g.year_end()
open(sys.argv[2], "wb").write(save_game(g.state))
`, saves.season, saves.offseason], { cwd: ROOT, env: { ...process.env, PYTHONPATH: join(ROOT, "src") } });

let failures = 0;
let checks = 0;
const report = [];

// ページの中で、見えている表をすべて測る
function measureAll() {
  const textBox = (node, skipArrow) => {
    // 要素の中の文字の外枠(▲▼ と .sub・.rank は除く)
    const r = document.createRange();
    let left = Infinity;
    let right = -Infinity;
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      if (t.parentElement.closest(".sub, .rank, .arrow")) continue;
      let text = t.textContent;
      let end = text.length;
      if (skipArrow) end = text.replace(/\s*[▲▼]\s*$/, "").length;
      if (end === 0 || !text.trim()) continue;
      r.setStart(t, 0);
      r.setEnd(t, end);
      for (const rect of r.getClientRects()) {
        if (rect.width === 0) continue;
        left = Math.min(left, rect.left);
        right = Math.max(right, rect.right);
      }
    }
    return Number.isFinite(left) ? { left, right } : null;
  };
  const out = [];
  for (const wrap of document.querySelectorAll(".table-wrap")) {
    if (!wrap.offsetParent) continue;
    const table = wrap.querySelector("table");
    if (!table || !table.tHead) continue;
    const hr = table.tHead.rows[0];
    const rows = [...table.tBodies[0].rows].filter((tr) => !tr.classList.contains("detail") && tr.cells.length === hr.cells.length);
    if (!rows.length) continue;
    const id = wrap.closest("[id]") ? wrap.closest("[id]").id : "?";
    const scrollable = wrap.scrollWidth > wrap.clientWidth + 1;
    const res = { id, scrollable, issues: [], lines: [], sorted: null, gap: null };
    const states = scrollable ? [0, Math.min(80, wrap.scrollWidth - wrap.clientWidth)] : [0];
    for (const sl of states) {
      wrap.scrollLeft = sl;
      for (const cls of ["sticky", "sticky2"]) {
        const th = hr.querySelector(`th.${cls}`);
        if (!th || getComputedStyle(th).position !== "sticky") continue;
        for (const tr of [rows[0], rows[rows.length - 1]]) {
          const td = tr.querySelector(`td.${cls}`);
          if (!td) continue;
          const a = th.getBoundingClientRect();
          const b = td.getBoundingClientRect();
          res.lines.push({ cls, scroll: sl, head: a.right, body: b.right, diff: Math.abs(a.right - b.right), headLeft: a.left, bodyLeft: b.left });
        }
      }
    }
    wrap.scrollLeft = 0;
    // 並べ替えた列:▲▼ を除いた見出しの右端と、数値の右端
    const sortedTh = hr.querySelector("th.sorted");
    if (sortedTh) {
      const idx = [...hr.cells].indexOf(sortedTh);
      const head = textBox(sortedTh, true);
      const vals = rows.slice(0, 5).map((tr) => textBox(tr.cells[idx], false)).filter(Boolean);
      if (head && vals.length) {
        const right = Math.max(...vals.map((v) => v.right));
        res.sorted = { label: sortedTh.textContent.trim(), head: head.right, body: right, diff: Math.abs(head.right - right) };
      }
    }
    // 固定列のすぐ右の列の、値の左の余白(横に動かさない状態)
    const stickyCount = [...hr.cells].filter((c) => getComputedStyle(c).position === "sticky").length;
    if (stickyCount && hr.cells.length > stickyCount) {
      const lastSticky = rows[0].cells[stickyCount - 1].getBoundingClientRect().right;
      const gaps = rows.slice(0, 10).map((tr) => textBox(tr.cells[stickyCount], false)).filter(Boolean).map((v) => v.left - lastSticky);
      const headBox = textBox(hr.cells[stickyCount], true);
      if (gaps.length) res.gap = { body: Math.min(...gaps), head: headBox ? headBox.left - hr.cells[stickyCount - 1].getBoundingClientRect().right : null };
    }
    out.push(res);
  }
  return out;
}

async function measure(page, label) {
  await page.waitForTimeout(150);
  const tables = await page.evaluate(measureAll);
  const shown = tables.filter((t) => t.lines.length || t.sorted || t.gap);
  for (const t of shown) {
    const worst = t.lines.reduce((m, l) => Math.max(m, l.diff), 0);
    const ok1 = worst <= LINE_TOL;
    const ok2 = !t.sorted || t.sorted.diff <= TEXT_TOL;
    const ok3 = !t.gap || t.gap.body >= GAP_MIN;
    checks += 3;
    const bad = [!ok1 && `固定列の線のずれ ${worst.toFixed(1)}px`, !ok2 && `並べ替えた列「${t.sorted.label}」の右端のずれ ${t.sorted.diff.toFixed(1)}px`, !ok3 && `最初のスクロール列の左の余白 ${t.gap.body.toFixed(1)}px`].filter(Boolean);
    if (bad.length) failures += bad.length;
    report.push(`${bad.length ? "×" : "○"} ${label} [${t.id}] 線 ${worst.toFixed(1)}px${t.sorted ? `・並べ替え「${t.sorted.label}」${t.sorted.diff.toFixed(1)}px` : ""}${t.gap ? `・余白 本体 ${t.gap.body.toFixed(1)}px / 見出し ${t.gap.head === null ? "-" : t.gap.head.toFixed(1)}px` : ""}${bad.length ? `(${bad.join("、")})` : ""}`);
  }
  if (!shown.length) report.push(`- ${label}:測れる表がありません`);
  return shown;
}

// 表の中のボタンは、画面の上下の帯に隠れることがあるので、要素を直接押す
async function tap(page, selector) {
  await page.waitForSelector(selector);
  await page.$eval(selector, (b) => b.click());
}

// 表を画面に入れ、横に少しずらした状態(固定列の線が見える)で写す
async function shot(page, name, selector) {
  if (!SHOTS) return;
  await page.evaluate((sel) => {
    const wrap = document.querySelector(`${sel} .table-wrap`);
    wrap.scrollIntoView({ block: "start" });
    window.scrollBy(0, -140);
    wrap.scrollLeft = Math.min(60, wrap.scrollWidth - wrap.clientWidth);
  }, selector);
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(SHOTS, name), fullPage: false });
  await page.evaluate((sel) => { document.querySelector(`${sel} .table-wrap`).scrollLeft = 0; }, selector);
}

async function openSave(page, path) {
  await page.goto(URL_);
  await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
  await page.setInputFiles("#open-file-start", path);
  await page.waitForSelector("#tabs:not([hidden])", { timeout: 120000 });
}

async function statsTable(page) {
  await page.waitForFunction(() => !document.querySelector("#stats-loading") || document.querySelector("#stats-loading").hidden);
  await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0, null, { timeout: 60000 });
}

const browser = await chromium.launch(process.env.PW_EXECUTABLE ? { executablePath: process.env.PW_EXECUTABLE } : {});
for (const width of WIDTHS) {
  report.push(`\n## 幅 ${width}`);
  const page = await browser.newPage({ viewport: { width, height: width < 700 ? 900 : 900 } });
  page.on("dialog", (d) => d.accept().catch(() => {}));
  await openSave(page, saves.season);
  // ---- 個人成績 ----
  await page.click("#tabs button[data-tab=stats]");
  await statsTable(page);
  for (const role of ["batter", "pitcher"]) {
    await page.click(`#stats-role button[data-value=${role}]`);
    await statsTable(page);
    for (const kind of ["basic", "saber", "war"]) {
      await page.click(`#stats-kind button[data-value=${kind}]`);
      await statsTable(page);
      const keys = await page.$$eval("#stats-sort option", (os) => os.map((o) => [o.value, o.parentElement.label || "", o.textContent]));
      const usable = keys.filter(([, g]) => (kind === "war" ? g.startsWith("WAR") : !g.startsWith("WAR") && !g.startsWith("能力")));
      const pick = [...new Set([usable[0], usable[Math.floor(usable.length / 3)], usable[Math.floor((2 * usable.length) / 3)], usable[usable.length - 1]].filter(Boolean))];
      for (const [key, , text] of pick) {
        await page.selectOption("#stats-sort", key);
        await page.waitForFunction((t) => document.querySelector("#stats-info").textContent.includes(`並び順:${t}`), text);
        await statsTable(page);
        await measure(page, `個人成績 ${role === "batter" ? "打者" : "投手"} ${kind} 並び順 ${text}`);
        if (role === "pitcher" && kind === "saber" && /防御率/.test(text)) await shot(page, `align-${width}-pitcher-saber-era.png`, "#stats-table");
      }
    }
  }
  // シーズンの切り替え(1シーズン目)
  await page.click("#stats-kind button[data-value=basic]");
  await statsTable(page);
  const seasons = await page.$$eval("#stats-season option", (os) => os.map((o) => o.value));
  if (seasons.length > 1) {
    await page.selectOption("#stats-season", seasons[seasons.length - 1]);
    await statsTable(page);
    await measure(page, `個人成績 投手 基本 シーズン ${seasons[seasons.length - 1]}`);
    await page.selectOption("#stats-season", seasons[0]);
    await statsTable(page);
  }
  // 能力(答え合わせモード)
  await page.click("#menu");
  await page.check("input[name=answer-level][value='1']");
  await page.click("#screen-settings .back");
  await page.click("#tabs button[data-tab=stats]");
  for (const role of ["batter", "pitcher"]) {
    await page.click(`#stats-role button[data-value=${role}]`);
    await page.click("#stats-kind button[data-value=ability]");
    await statsTable(page);
    await measure(page, `個人成績 ${role === "batter" ? "打者" : "投手"} 能力`);
  }
  await page.click("#menu");
  await page.check("input[name=answer-level][value='0']");
  await page.click("#screen-settings .back");
  // ---- 順位 ----
  await page.click("#tabs button[data-tab=standings]");
  await page.waitForSelector("#screen-standings:not([hidden]) tbody tr");
  await measure(page, "順位");
  // ---- チーム(順位表の先頭のチーム) ----
  await tap(page, "#screen-standings tbody tr td.sticky .link");
  await page.waitForSelector("#team-batters tbody tr", { timeout: 60000 });
  await measure(page, "チーム");
  // ---- ドラフトの振り返り ----
  await page.click("#tabs button[data-tab=progress]");
  if (await page.$("#open-review")) {
    await page.click("#open-review");
    await page.waitForSelector("#review-table tbody tr", { timeout: 60000 });
    await measure(page, "ドラフトの振り返り");
  }
  // ---- オフの手続き ----
  await openSave(page, saves.offseason);
  if (await page.isHidden("#screen-procedure")) await page.click("#open-offseason");
  await page.waitForSelector("#proc-body tbody tr", { timeout: 60000 });
  await measure(page, "契約(全員)");
  await page.click("#renew-auto");
  await page.waitForFunction(() => !document.querySelector("#proc-info").textContent.includes("未提示"), null, { timeout: 60000 });
  await measure(page, "契約(提示の後)");
  await page.selectOption("#contract-group", "pitcher");
  await page.waitForSelector("#contract-kind");
  await page.selectOption("#contract-kind", "saber");
  await page.waitForFunction(() => document.querySelector("#proc-body thead") && document.querySelector("#proc-body thead").textContent.includes("FIP"));
  await measure(page, "契約(投手・セイバー)");
  await page.$$eval("#proc-body thead th button.sort", (bs) => bs.find((b) => b.textContent.startsWith("年齢")).click()); // 上の帯の下に隠れることがあるので、要素を直接押す
  await page.waitForFunction(() => document.querySelector("#proc-body thead th.sorted") && document.querySelector("#proc-body thead th.sorted").textContent.startsWith("年齢"));
  await measure(page, "契約(投手・年齢で並べ替え)");
  await page.click("#proc-stage-auto"); // この段階をおまかせ(D-271)
  await page.waitForFunction(() => /FA|ドラフト/.test(document.querySelector("#proc-title").textContent), null, { timeout: 60000 });
  if ((await page.textContent("#proc-title")) === "FA") {
    await page.waitForSelector("#fa-round");
    await measure(page, "FA");
    await shot(page, `align-${width}-fa.png`, "#proc-body");
    await page.click("#proc-stage-auto");
    await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "ドラフト", null, { timeout: 60000 });
  }
  await page.locator("#proc-body button", { hasText: "次の自分の番まで進める" }).click();
  await page.waitForFunction(() => document.querySelector("#proc-body").textContent.includes("あなたの番"), null, { timeout: 60000 });
  await measure(page, "ドラフト");
  await page.click("#proc-stage-auto");
  await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "市場", null, { timeout: 60000 });
  await page.waitForSelector("#proc-body tbody tr");
  await measure(page, "自由契約市場");
  await page.close();
}
await browser.close();
console.log("# 表の列のそろい(D-267・D-268)");
console.log(report.join("\n"));
console.log(`\n${failures ? `× ずれ ${failures} 件` : "○ すべてそろっています"}(確かめた項目 ${checks})`);
process.exit(failures ? 1 : 0);
