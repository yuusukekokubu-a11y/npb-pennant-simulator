// 遊ぶための画面(web/)を、本物のブラウザ(画面を出さない Chromium)で通しで確かめる、開発者向けのスクリプト。
// PR の CI では動かさない(Playwright という、ブラウザを自動で操作する道具が要るため)。GitHub の Actions の「e2e」で手動で回せる(D-413)。
// ○×は 1 つずつその場で標準エラーに出し、最後のまとめを標準出力に出す。
//
// 使い方(先に scripts/build_web.py で _site/ を作り、手元で配信しておく):
//   python scripts/build_web.py --pyodide <Pyodide のフォルダ>
//   python -m http.server 8765 --directory _site
//   node scripts/check_browser.mjs http://127.0.0.1:8765/?pyodide=local
//
// 確かめること(既定はスマホの縦持ちの大きさ 412×924。環境変数 WIDTH で幅を変えられる:390・768・1280 など。D-223):
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
// 結果の 1 行ずつを、その場で標準エラー(進み具合)にも出す(D-413)。最後のまとめは今までどおり標準出力に出す
const results = [];
const push = results.push.bind(results);
results.push = (...lines) => {
  for (const line of lines) console.error(line);
  return push(...lines);
};
let failed = 0;
let finished = false;
// 途中で止まったとき(待ちの時間切れなど)も、そこまでの結果が分かるようにする
process.on("exit", (code) => {
  if (!finished) console.log(`# 途中で止まりました(終了コード ${code})。ここまでの結果\n${results.join("\n")}`);
});

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
print(json.dumps({"words": sorted(words), "keys": ["ratings", "potential", "growth_type", "archetype", "ease", "trajectory_bias", "hidden", "park", "home_run_multiplier"]}, ensure_ascii=False))
`));

const browser = await chromium.launch();
const WIDTH = Number(process.env.WIDTH || 412); // 画面の幅(D-223)。800 未満はスマホ扱い(タッチ・2倍の解像度)
const HEIGHT = WIDTH < 800 ? 924 : 900;
const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: WIDTH < 800 ? 2 : 1, isMobile: WIDTH < 800, hasTouch: WIDTH < 800, acceptDownloads: true });
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
// 説明ブロックの見張り(UI の整理②。D-306、D-310):今の画面に、1 行の注意書きより長い説明の文がないか。grab のたびに記録し、最後にまとめて確かめる
const blockFindings = [];
const seenScreens = new Set();
async function findBlocks() {
  const f = await page.evaluate(() => {
    const scr = [...document.querySelectorAll("main.screen")].find((m) => !m.hidden);
    if (!scr) return null;
    const skip = "table, #guide-body, .message, [role=alert], .offer-panel, .game-card, .log, #recent, .outlook-panel, .help-bar, #proc-autolog, details:not([open]), #player-info, #team-info, #stadium-info, #yearend-champions, #mine-card";
    const blocks = [...scr.querySelectorAll("p, dl, dd, .info, .notice, .card")].filter((e) => e.offsetParent && !e.closest(skip) && !e.classList.contains("message") && e.textContent.trim().length > 50).map((e) => e.textContent.trim().slice(0, 30));
    return { screen: scr.id, blocks };
  });
  if (!f) return;
  seenScreens.add(f.screen);
  for (const b of f.blocks) blockFindings.push(`${f.screen}:${b}…`);
}
const grab = async () => {
  texts.push(await page.evaluate(() => document.body.innerText));
  await findBlocks();
};
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
// 仕組みの選択肢(D-381):自動 FA はなし、ほかはありが最初から選ばれている。選ぶと説明バーに出る
const rulesUi = await page.$$eval("input[name=rules]", (xs) => xs.map((x) => [x.value, x.checked]));
check(JSON.stringify(rulesUi) === JSON.stringify([["auto_fa", false], ["active_draft", true], ["trade", true], ["foreign_development", true]]), `新規開始の「仕組みの選択肢」4 つ(自動 FA はなし、ほかはあり)`);
await page.check("#rule-auto_fa");
await page.waitForFunction(() => !document.querySelector("#new-help").hidden && document.querySelector("#new-help-name").textContent.includes("自動 FA"));
check(true, `選択肢を選ぶと説明バーに出る(「${await page.textContent("#new-help-text")}」)`);
await page.uncheck("#rule-auto_fa");
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
check((await page.$$("input[name=money-rule]")).length === 0, "新規開始にお金のルールの選択はない(球団ごとの資金。作り直し②。D-353)");
// 新規開始の説明バー(D-313):常に出る説明はなく、項目を選ぶとその説明が下のバーに出て、選び直すと入れ替わる
const helpText = () => page.textContent("#new-help");
check(!(await page.$$eval("#screen-new label.radio .muted", (x) => x.length)) && (await page.$$eval("#screen-new p", (ps) => ps.filter((p) => p.offsetParent && p.textContent.length > 60).length)) === 0, "新規開始の設定に、常に出る説明の文がない");
await page.focus("#team-4");
await page.waitForSelector("#new-help:not([hidden])");
const helpTeam = await helpText();
await page.check("input[name=scout-level][value=large]");
await page.waitForFunction(() => document.querySelector("#new-help-name").textContent === "ずれ:大");
const helpLarge = await helpText();
await page.check("input[name=scout-level][value=medium]");
await page.waitForFunction(() => document.querySelector("#new-help-name").textContent === "ずれ:中");
const helpMedium = await helpText();
const helpPy = JSON.parse(python(`import json\nfrom pennant.glossary import default_glossary\ng = default_glossary()\nprint(json.dumps([g.by_id(i)["meaning"] for i in ("setting.team_name", "setting.scout.large", "setting.scout.medium")], ensure_ascii=False))`));
check(helpTeam.includes(helpPy[0]) && helpLarge.includes(helpPy[1]) && helpMedium.includes(helpPy[2]), `項目を選ぶと、その説明(用語集の文)が下のバーに出て、選び直すと入れ替わる(「${helpMedium}」)`);
await page.$eval("#new-start", (b) => b.scrollIntoView({ block: "end" }));
const startVisible = await page.evaluate(() => { const b = document.querySelector("#new-start").getBoundingClientRect(); const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); const h = document.querySelector("#new-help").getBoundingClientRect(); return hit !== null && hit.closest("#new-start") !== null && h.bottom <= b.top + 1; });
check(startVisible, "説明バーは「この内容で始める」のボタンを隠さない");
let prerunSeen = false;
const prerunWatch = page.waitForFunction(() => document.querySelector("#trial-box").textContent.includes("リーグの歴史を作っています"), null, { timeout: 60000 }).then(() => (prerunSeen = true)).catch(() => {});
const trialStart = Date.now();
await page.click("#new-start");
// 試運転の進み具合(D-121)
await page.waitForSelector("#trial-box:not([hidden])");
await page.waitForFunction(() => /\d+ \/ 125 日/.test(document.querySelector("#trial-text").textContent));
check((await page.textContent("#trial-box")).includes("リーグの基準値を求めています"), `試運転の進み具合が出る(「${await page.textContent("#trial-text")}」)`);
await page.waitForSelector("#screen-progress:not([hidden])", { timeout: 120000 });
check(await page.isHidden("#new-help"), "設定の画面から離れると、説明バーは消える(D-313)");
const trialSeconds = (Date.now() - trialStart) / 1000;
results.push(`  事前運転(25 年)と試運転(1シーズン)を含めた新規開始の時間: ${trialSeconds.toFixed(1)} 秒`);
check(prerunSeen, "新規開始の間に、事前運転(リーグの歴史を作っています)の進み具合が出る");
const mineName = await page.textContent("#vs .vs-name"); // 得点板の左が自球団(D-400)
check(mineName === placeholder7, `選んだ8番目の球団が自球団になる(${mineName})`);
check((await page.textContent("#day-text")).startsWith("1シーズン目 0日目 / 125日"), `進行の画面:「${await page.textContent("#day-text")}」`);
check(await isDirty(), "新規開始のあとは「未保存」(閉じる前に警告が出る)");
check(!(await page.isHidden("#dirty")) && (await page.$eval("#save", (b) => b.classList.contains("unsaved"))), "未保存の印(保存のアイコンの点)が出る(D-401)");

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
// スタメンと打順(D-373):型を選ぶと AI が組み直す。自分で組むと「自分で組んだ」になる。最後は長打重視の AI に戻す(この後の結果を指紋 (d) と比べるため)
await page.click("#open-lineup");
await page.waitForFunction(() => !document.querySelector("#screen-lineup").hidden && document.querySelectorAll("#lineup-table tbody tr").length === 9);
check((await page.textContent("#lineup-source")).includes("長打重視") && (await page.inputValue("#lineup-style")) === "power", `スタメンと打順の画面:9 人の表と「${await page.textContent("#lineup-source")}」`);
const luSkills = await page.$$eval("#lineup-table tbody .skill", (els) => els.map((e) => e.textContent));
check((await page.$$eval("#lineup-table thead th", (ths) => ths.map((th) => th.textContent))).some((x) => x.includes("特殊能力")), `スタメンの表に特殊能力の列がある(${luSkills.join("・") || "なし"})`);
const luBefore = await page.$$eval("#lineup-table tbody tr", (trs) => trs.map((tr) => tr.querySelector("select[id^='lineup-player']").value));
await page.selectOption("#lineup-style", "slugger_second");
await page.waitForFunction(() => document.querySelector("#lineup-message").textContent.includes("二番強打者"));
check(true, "打順の型を「二番強打者」にすると、AI が組み直す");
const first2 = await page.inputValue("#lineup-player-1");
const second2 = await page.inputValue("#lineup-player-2");
await page.selectOption("#lineup-player-1", second2);
await page.selectOption("#lineup-player-2", first2);
await page.click("#lineup-save");
await page.waitForFunction(() => document.querySelector("#lineup-message").textContent.includes("このスタメンと打順で戦います") || document.querySelector("#lineup-message").className.includes("ng"));
check((await page.textContent("#lineup-source")).includes("自分で組んだ") && (await page.inputValue("#lineup-player-1")) === second2, `1・2 番を入れ替えて「このスタメンと打順にする」(${await page.textContent("#lineup-source")})`);
const dup = await page.inputValue("#lineup-player-3");
await page.selectOption("#lineup-player-4", dup);
await page.click("#lineup-save");
await page.waitForFunction(() => document.querySelector("#lineup-message").className.includes("ng"));
check((await page.textContent("#lineup-message")).includes("同じ選手"), `同じ選手を 2 つの打順に入れると、理由が出る(「${await page.textContent("#lineup-message")}」)`);
await page.selectOption("#lineup-style", "power");
await page.waitForFunction(() => document.querySelector("#lineup-message").textContent.includes("長打重視"));
const luAfter = await page.$$eval("#lineup-table tbody tr", (trs) => trs.map((tr) => tr.querySelector("select[id^='lineup-player']").value));
check(JSON.stringify(luAfter) === JSON.stringify(luBefore) && (await page.textContent("#lineup-source")).includes("AI"), "長打重視に戻すと、最初の AI の打順に戻る");
// 盗塁の作戦(D-429):選ぶと保存され、普通に戻す(この後の結果を Python で同じ新規開始を進めた結果と比べるため)
await page.selectOption("#lineup-steal", "aggressive");
await page.waitForFunction(() => document.querySelector("#lineup-message").textContent.includes("積極的"));
const stealPy = ["積極的", "普通", "慎重"];
check(JSON.stringify(await page.$$eval("#lineup-steal option", (os) => os.map((o) => o.textContent))) === JSON.stringify(stealPy), `スタメンの画面に盗塁の作戦(${stealPy.join("・")})があり、選ぶと知らせが出る(「${await page.textContent("#lineup-message")}」)`);
await page.selectOption("#lineup-steal", "normal");
await page.waitForFunction(() => document.querySelector("#lineup-message").textContent.includes("普通"));
await page.click("#screen-lineup .back");
// 一軍登録(D-425):一軍 29 人と二軍が、調子のマーク付きで出る。「外す」を押すと、同じ役割の二軍の選手だけ「上げる」が押せる。
// ここでは入れ替えない(この後の結果を Python で同じ新規開始を進めた結果と比べるため)。実際の入れ替えは 2シーズン目で確かめる
await page.waitForSelector("#open-roster:not([hidden])");
await page.click("#open-roster");
await page.waitForFunction(() => !document.querySelector("#screen-roster").hidden && document.querySelectorAll("#roster-body tbody tr").length > 29);
const rosterCounts = await page.$$eval("#roster-body h2", (hs) => hs.map((h) => h.textContent));
const condMarks = await page.$$eval("#roster-body .cond", (els) => els.map((e) => e.textContent.slice(0, 1)));
check(rosterCounts.length === 4 && rosterCounts[0].includes("6 人") && rosterCounts[1].includes("8 人") && rosterCounts[2].includes("15 人") && condMarks.length > 29 && condMarks.every((m) => "↑↗→↘↓".includes(m)), `一軍登録の画面:${rosterCounts.join("・")}。調子のマーク(${[...new Set(condMarks)].join("")})`);
check((await page.isChecked("#roster-auto")) && (await page.textContent("#roster-info")).includes("次の見直し"), `「AI に任せる」が最初からオン(${await page.textContent("#roster-info")})`);
await page.click("#roster-relievers tbody tr:first-child td:last-child button");
await page.waitForFunction(() => document.querySelector("#roster-message").textContent.includes("上げる"));
const farmRows = await page.$$eval("#roster-farm tbody tr", (trs) => trs.map((tr) => [tr.children[3].textContent, tr.querySelector("td:last-child button")?.disabled]));
const pitcherFarm = farmRows.filter((r) => r[1] !== undefined && /^(投手|先発|救援)/.test(r[0]));
const batterFarm = farmRows.filter((r) => r[1] !== undefined && !/^(投手|先発|救援)/.test(r[0]));
check(pitcherFarm.length > 0 && pitcherFarm.every((r) => r[1] === false) && batterFarm.every((r) => r[1] === true), `救援投手の「外す」を押すと、二軍の投手だけ「上げる」が押せる(投手 ${pitcherFarm.length} 人・野手 ${batterFarm.length} 人)`);
await page.click("#roster-relievers tbody tr:first-child td:last-child button");
await page.waitForFunction(() => document.querySelector("#roster-message").textContent === "");
check(true, "もう一度押すと、選ぶのをやめられる");
await page.click("#screen-roster .back");
// トレード(D-378):画面を開き、釣り合わない交換を持ちかけると断られて、何も変わらない
await page.waitForSelector("#open-trade:not([hidden])");
await page.click("#open-trade");
await page.waitForFunction(() => !document.querySelector("#screen-trade").hidden && document.querySelectorAll("#trade-mine tbody tr").length > 0 && document.querySelectorAll("#trade-theirs tbody tr").length > 0);
check((await page.textContent("#trade-window")).includes("日目まで"), `トレードの画面:「${await page.textContent("#trade-window")}」`);
const tradeRows = (sel) => page.$$eval(`${sel} tbody tr`, (trs) => trs.map((tr, i) => ({ i, name: tr.querySelector("button.link")?.textContent || "", v: Number((tr.children[3]?.textContent || "").replace(/[^0-9]/g, "")), locked: tr.querySelector("input").disabled })));
const mineBefore = (await tradeRows("#trade-mine")).map((r) => r.name);
const giveRow = (await tradeRows("#trade-mine")).filter((r) => !r.locked).sort((a, b) => a.v - b.v)[0];
const getRow = (await tradeRows("#trade-theirs")).filter((r) => !r.locked).sort((a, b) => b.v - a.v)[0];
await page.locator("#trade-mine tbody tr").nth(giveRow.i).locator("input").check();
await page.locator("#trade-theirs tbody tr").nth(getRow.i).locator("input").check();
await page.waitForFunction(() => !document.querySelector("#trade-propose").disabled, null, { timeout: 10000 }).catch(() => {});
check(!(await page.isDisabled("#trade-propose")), `出す選手(${giveRow.name})と欲しい選手(${getRow.name})を選ぶと、持ちかけられる`);
await page.click("#trade-propose");
await page.waitForFunction(() => document.querySelector("#trade-message").className.includes("ng"));
check(JSON.stringify((await tradeRows("#trade-mine")).map((r) => r.name)) === JSON.stringify(mineBefore), `釣り合わない交換は断られ、選手は変わらない(「${await page.textContent("#trade-message")}」)`);
await page.click("#screen-trade .back");
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

// 今の並び順(並び順の欄の指標と、押されている向きのボタン)。説明のブロックはないので、欄の表示で確かめる(D-310)
const sortState = () => page.evaluate(() => { const s = document.querySelector("#stats-sort"); const o = document.querySelector("#stats-order button[aria-pressed=true]"); return `${s.selectedOptions[0] ? s.selectedOptions[0].textContent : ""}(${o ? o.textContent : ""})`; });
async function waitStats(text, timeout = 30000) {
  await page.waitForFunction((t) => {
    const s = document.querySelector("#stats-sort");
    const o = document.querySelector("#stats-order button[aria-pressed=true]");
    const th = document.querySelector("#stats-table th.sorted");
    const name = t.split("(")[0];
    return document.querySelector("#stats-loading").hidden && th && th.textContent.replace(/^[▲▼]/, "").startsWith(name) && `${s.selectedOptions[0] ? s.selectedOptions[0].textContent : ""}(${o ? o.textContent : ""})`.includes(t);
  }, text, { timeout });
  return statsOnScreen();
}
// 表が計算本体の行(rows)と同じ人数・同じ先頭になるまで待つ(シーズンや種類を切り替えたあと)
async function waitRows(rows) {
  await page.waitForFunction(([n, top]) => { const m = document.querySelector("#stats-table .more"); const trs = document.querySelectorAll("#stats-table tbody tr"); const rest = m ? Number((m.textContent.match(/あと (\d+)/) || [0, 0])[1]) : 0; const first = trs[0] && trs[0].querySelector("td .link"); return document.querySelector("#stats-loading").hidden && trs.length + rest === n && (!top || (first && first.textContent === top)); }, [rows.length, rows.length ? rows[0][0] : null]);
}
// 用語集の解説(見出しの title と同じ文。D-311)
const termTitle = (label) => JSON.parse(python(`import json\nfrom pennant.glossary import default_glossary, term_text\nprint(json.dumps(term_text(default_glossary().find(${JSON.stringify(label)})), ensure_ascii=False))`));

// 画面の表と同じ形(名前、固定の列があればその値、各列の値)
// 個人成績はいつも 1 つのリーグ(D-431)。最初は自球団のリーグで、画面で選んだリーグと同じ値を渡す
let statsLeague = null;
const pyStats = (args) => pyGame(`t = g.stats(**a)\nkeys = ([t["extra_column"]["key"]] if t["extra_column"] else []) + [c["key"] for c in t["columns"]]\nprint(json.dumps([[r["name"]] + [r["values"][k] for k in keys] for r in t["rows"]], ensure_ascii=False))`, JSON.stringify({ league: statsLeague, ...args }));
const headsOnScreen = () => page.$$eval("#stats-table th", (ths) => ths.map((th) => th.textContent.replace(/^[▲▼]/, "")));

await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelector("#stats-league").options.length === 2);
statsLeague = Number(await page.inputValue("#stats-league"));
const myStatsLeague = statsLeague;
const otherLeague = 1 - myStatsLeague;
check(!(await page.$$eval("#stats-league option", (os) => os.map((o) => o.textContent))).includes("両リーグ"), `個人成績に両リーグをまとめた表はなく、最初は自球団のリーグ(D-431)`);
let screenRows = await waitStats("打率(高い順)");
check((await page.$eval("#stats-filter-box", (d) => !d.open)) && (await page.textContent("#stats-filter-text")).includes("規定到達者だけ"), `絞り込みは 1 つにまとめて閉じ、今の絞り込みを 1 行で出す(「${await page.textContent("#stats-filter-text")}」。D-401)`);
check((await page.textContent("#stats-leader .leader-title")).endsWith("打率 1位") && (await page.textContent("#stats-leader .leader-name .link")) === screenRows[0][0], `表の上に、並べている指標の 1 位の選手のカード(${screenRows[0][0]}。D-400)`);
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic" })), `個人成績(打者・基本・打率の高い順・規定到達者)が、計算本体の集計と同じ(${screenRows.length}人)`);
// 画面の測定(幅 390。D-310):個人成績の表の上端が、画面の上から 40% 以内
{
  const vp = page.viewportSize();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  const top = await page.$eval("#stats-table", (e) => e.getBoundingClientRect().top);
  check(top / 844 <= 0.4, `幅 390 で、個人成績の表の上端が画面の上から ${((top / 844) * 100).toFixed(0)}%(${top.toFixed(0)}px。40% 以内)`);
  await page.setViewportSize(vp);
  await page.waitForTimeout(300);
}
await page.$eval("#stats-filter-box", (d) => (d.open = true)); // 以下の確認では絞り込みを開いておく
check((await page.$eval("#screen-standings th:nth-child(6)", (th) => th.title)) === termTitle("勝率"), "順位表の見出し(勝率)にも、用語集の解説が付く");
let heads = await headsOnScreen();
check(heads.indexOf("OPS") === heads.indexOf("長打率") + 1, `基本の打者の表に、長打率の隣に OPS がある(列: ${heads.slice(1).join("・")})`);
await grab();
// 並び順の保持(D-131)と、固定の列・並び順の欄(D-132)
const topByAvg = screenRows[0][0];
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("wRC+")));
screenRows = await waitStats("打率(高い順)");
check(screenRows[0][0] === topByAvg, `「セイバー」に切り替えても並び順(打率の高い順)が保たれ、先頭の選手が同じ(${topByAvg})`);
heads = await headsOnScreen();
check(heads[1] === "打率" && heads.filter((h) => h === "打率").length === 1 && heads.includes("BABIP") && !heads.includes("OPS"), `名前の隣に打率の固定列が出て(重複なし)、BABIP の列が見える。OPS はセイバーにない(列: ${heads.slice(1).join("・")})`);
check(await page.$eval("#stats-table th:nth-child(2)", (th) => th.classList.contains("sticky2")), "固定列は、横にずらしても見えたまま(sticky)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber", sort: "avg" })), "固定列つきの表が、計算本体と同じ");
const sortOptions = await page.$$eval("#stats-sort option", (os) => os.map((o) => o.textContent));
check(sortOptions.length === 21 + 8 && ["OPS", "wOBA", "本塁打", "BABIP", "ミート", "弾道", "特殊能力"].every((x) => sortOptions.includes(x)), `並び順の欄に、基本・セイバーの全指標と能力(野手 7 項目と特殊能力。作り直し①・D-374)がある(${sortOptions.length}個)`);
await page.selectOption("#stats-sort", "babip");
screenRows = await waitStats("BABIP(高い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber", sort: "babip", order: "desc" })), "並び順の欄で BABIP を選ぶと並び替わる(計算本体と同じ)");
await page.click("#stats-order button[data-value=asc]");
screenRows = await waitStats("BABIP(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber", sort: "babip", order: "asc" })), "「低い順」を押すと逆の順になる");
await page.selectOption("#stats-league", String(otherLeague));
statsLeague = otherLeague;
await page.waitForFunction(() => document.querySelector("#stats-team").options.length === 7);
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("BABIP")));
await waitStats("BABIP(低い順)");
check((await page.inputValue("#stats-league")) === String(otherLeague) && (await page.$$eval("#stats-table tbody tr", (trs) => trs.length)) < 60, "「基本」に戻しても、並び順(BABIP の低い順。固定列)とリーグの絞り込みが保たれる");
await page.click("#stats-role button[data-value=pitcher]");
screenRows = await waitStats("防御率(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "basic" })) && (await page.inputValue("#stats-league")) === String(otherLeague), "打者から投手に切り替えると、投手の既定の並び順(防御率の低い順)に戻る(絞り込みは保つ)");
await page.selectOption("#stats-league", String(myStatsLeague));
statsLeague = myStatsLeague;
await page.click("#stats-role button[data-value=batter]");
await waitStats("打率(高い順)");
// 本塁打の列で並べ替え → もう一度で逆順
await page.click("#stats-table th button.sort >> text=本塁打");
screenRows = await waitStats("本塁打(多い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "desc" })), "見出しを押すと並べ替わる(本塁打の多い順)");
check((await page.$eval("#stats-table th.sorted", (th) => th.title)) === termTitle("本塁打") && !(await page.$("#stats-terms")), "列の見出しの解説(title)は用語集の文と同じで、表の下に用語の解説のブロックはない(D-306、D-311)");
await page.click("#stats-table th button.sort >> text=本塁打");
screenRows = await waitStats("本塁打(少ない順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc" })), "もう一度押すと逆の順になる");
// 規定到達者の絞り込みを外す・リーグとチームで絞り込む
await page.uncheck("#stats-qualified");
const all = pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc", qualified: false });
await waitRows(all);
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(all), `規定到達者の絞り込みを外すと、試合に出た全員(${all.length}人)`);
await page.selectOption("#stats-league", String(otherLeague));
statsLeague = otherLeague;
await page.waitForFunction(() => document.querySelector("#stats-team").options.length === 7);
const teamId = await page.$eval("#stats-team", (s) => s.options[2].value);
await page.selectOption("#stats-team", teamId);
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length < 40);
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc", qualified: false, team_id: teamId })), `リーグ・チームで絞り込める(${screenRows.length}人)`);
await page.selectOption("#stats-team", "");
await page.selectOption("#stats-league", String(myStatsLeague));
statsLeague = myStatsLeague;
await page.check("#stats-qualified");
await waitRows(pyStats({ role: "batter", kind: "basic", sort: "HR", order: "asc" }));
// 規定到達の判定が、実装⑤の定義(試合数 × 3.1、× 1.0)どおりか
const qual = pyGame(`
from pennant.records import qualifying_plate_appearances, qualifying_outs
rec = g.records.total
b = sorted(p for p, c in rec.batters.items() if c["PA"] >= (rec.teams[rec.batter_team[p]]["G"] * 31 + 5) // 10)
p = sorted(p for p, c in rec.pitchers.items() if c["OUTS"] >= rec.teams[rec.pitcher_team[p]]["G"] * 3)
print(json.dumps([b == sorted(g.select_players("batter")), p == sorted(g.select_players("pitcher")), len(b), len(p)]))`);
check(qual[0] && qual[1], `規定到達の判定が、規定打席(試合数×3.1)・規定投球回(試合数×1.0)どおり(打者 ${qual[2]}人・投手 ${qual[3]}人)`);
// 守備の表(D-431):ポジションを選んで並べる。捕手は盗塁阻止・捕逸も。上位 30 人で、続きは「もっと見る」
const pyFielding = (args) => pyGame(`t = g.fielding_stats(**a)\nprint(json.dumps([[r["name"]] + [r["values"][c["key"]] for c in t["columns"]] for r in t["rows"]], ensure_ascii=False))`, JSON.stringify({ league: statsLeague, ...args }));
await page.click("#stats-role button[data-value=fielder]");
await page.waitForFunction(() => !document.querySelector("#stats-position-row").hidden && document.querySelector("#stats-kind").hidden && [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("守備イニング")));
const fieldAll = pyFielding({ position: "all", qualified: false });
await page.uncheck("#stats-qualified");
await waitRows(fieldAll);
check((await page.$$eval("#stats-table tbody tr", (trs) => trs.length)) <= 30 && (await page.isVisible("#stats-table .more")), `守備の表は上位 30 人を出し、続きは「もっと見る」(全 ${fieldAll.length} 人)`);
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(fieldAll), `守備の表(すべて・守備の得点の高い順)が、計算本体と同じ(列: ${(await headsOnScreen()).slice(1).join("・")})`);
await page.check("#stats-qualified");
await page.selectOption("#stats-position", "C");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("盗塁阻止率")));
const fieldC = pyFielding({ position: "C" });
await waitRows(fieldC);
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(fieldC) && fieldC.length > 0, `捕手の表に 盗塁企図・盗塁阻止・盗塁阻止率・捕逸が出て、計算本体と同じ(規定到達 ${fieldC.length} 人)`);
await page.selectOption("#stats-position", "all");
await page.click("#stats-role button[data-value=batter]");
await page.waitForFunction(() => !document.querySelector("#stats-kind").hidden && document.querySelector("#stats-position-row").hidden);
await waitStats("打率(高い順)");
heads = await headsOnScreen();
check(heads.includes("盗塁"), `打撃の基本の表に盗塁がある(列: ${heads.slice(1).join("・")})`);
// 投手・セイバー。指標名の解説が、定義データと同じ
await page.click("#stats-role button[data-value=pitcher]");
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "") === "K%"));
screenRows = await waitStats("防御率(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "saber" })), `投手のセイバー(防御率の低い順。防御率は固定列)が、計算本体と同じ(${screenRows.length}人)`);
await page.click("#stats-table th button.sort >> text=K%");
await waitStats("K%(高い順)");
const kTitle = await page.$eval("#stats-table th.sorted", (th) => th.title);
check(kTitle === termTitle("K%") && kTitle.includes("投手は高いほど良い") && kTitle.includes("式:"), `指標の見出しの解説が、用語集の文と同じ(K%:「${kTitle}」)`);
await grab();
// 第2弾の指標(打者のセイバー:wRC+ の高い順)と注記
await page.click("#stats-role button[data-value=batter]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("wRC+")));
screenRows = await waitStats("wRC+(高い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "saber" })), `打者のセイバー(wOBA・wRC+・OPS+。wRC+ の高い順)が、計算本体と同じ(${screenRows.length}人)`);
const baseNote = await page.textContent("#stats-baseline");
check(await page.isVisible("#stats-baseline") && baseNote === "シーズン途中の値(ここまで)。", `表の近くに、1 行の注意書きだけが出る(「${baseNote}」。基準値の混ぜ方は用語集に。D-310)`);
await page.click("#stats-table th button.sort >> text=OPS+");
await waitStats("OPS+(高い順)");
check((await page.$eval("#stats-table th.sorted", (th) => th.title)).includes("球場補正"), "OPS+ の見出しの解説に、球場補正つきであることが出る(用語集)");
await page.click("#stats-role button[data-value=pitcher]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("FIP")));
await page.click("#stats-table th button.sort >> text=FIP");
screenRows = await waitStats("FIP(低い順)");
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "saber", sort: "fip", order: "asc" })), `投手のセイバーに FIP(低い順)が出て、計算本体と同じ(${screenRows.length}人)`);
await grab();

// WAR(第3弾③b。D-179):打者の表が計算本体と同じ。計算時間を測る。並び順の欄に WAR の列が入る
await page.click("#stats-role button[data-value=batter]");
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0 && [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("打率")));
results.push(`  (WAR の前の並び順: ${await sortState()})`);
const warStart = Date.now();
await page.click("#stats-kind button[data-value=war]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("WAR")), null, { timeout: 180000 });
screenRows = await waitStats("WAR(高い順)", 180000);
const warSeconds = (Date.now() - warStart) / 1000;
results.push(`  WAR の計算と表示にかかった時間(打者、初回): ${warSeconds.toFixed(1)} 秒`);
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "batter", kind: "war" })), `打者の WAR(WAR の高い順。打席・内訳つき)が、計算本体と同じ(${screenRows.length}人。${warSeconds.toFixed(1)} 秒)`);
heads = await headsOnScreen();
check(heads.slice(1).join("・") === "打席・WAR・打撃・走塁・守備・ポジション補正・控え水準", `打者の WAR の列(${heads.slice(1).join("・")})`);
check((await page.textContent("#stats-baseline")) === "シーズン途中の値(ここまで)。", "WAR の表の近くに「シーズン途中の値(ここまで)」の 1 行が出る");
check((await page.$$eval("#stats-table th", (ths) => ths.filter((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("控え水準")).map((th) => th.title)))[0] === termTitle("控え水準"), "WAR の用語(控え水準など)の解説は、見出しの title(用語集)で見られる");
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
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("WAR(失点版)")), null, { timeout: 60000 });
screenRows = await waitStats("WAR(失点版)(高い順)", 60000);
check(JSON.stringify(screenRows) === JSON.stringify(pyStats({ role: "pitcher", kind: "war" })), `投手の WAR(失点版の高い順。投球回・失点版・FIP 版)が、計算本体と同じ(${screenRows.length}人)`);
// 選手のページの WAR と、チームのページの WAR の合計
await page.click("#stats-table tbody tr:first-child td.sticky .link");
await page.waitForFunction(() => document.querySelector("#player-name").textContent.length > 0);
await page.click("#player-kind button[data-value=war]");
await page.waitForFunction(() => document.querySelectorAll("#player-season td").length === 3);
const warPid = pyGame(`print(json.dumps(g.stats("pitcher", "war", league=a["lg"])["rows"][0]["player_id"]))`, JSON.stringify({ lg: statsLeague }));
const pyWarPlayer = pyGame(`d = g.player(a["id"])["season"]["war"]
print(json.dumps([d["values"][c["key"]] for c in d["columns"]], ensure_ascii=False))`, JSON.stringify({ id: warPid }));
check(JSON.stringify(await page.$$eval("#player-season td", (tds) => tds.map((td) => td.textContent))) === JSON.stringify(pyWarPlayer), `選手のページの WAR(投球回・失点版・FIP 版)が、計算本体と同じ`);
const pyContract = pyGame(`c = g.player(a["id"])["player"]["contract"]
print(json.dumps([c["salary_text"], c["remaining"], c["history"][-1]["years"]], ensure_ascii=False))`, JSON.stringify({ id: warPid }));
const contractOnScreen = await page.$$eval("#player-contract td", (tds) => tds.map((td) => td.textContent));
check(!(await page.isHidden("#player-contract-box")) && contractOnScreen[0] === pyContract[0] && contractOnScreen[1] === `${pyContract[2]} 年` && contractOnScreen[2].startsWith(`${pyContract[1]} 年`), `選手のページに契約(年俸・契約年数・残り年数)が出て、計算本体と同じ(${contractOnScreen.join(" / ")})`);
const contractBoxText = await page.textContent("#player-contract-box");
check(!HIDDEN.keys.some((k) => contractBoxText.includes(k)) && !contractBoxText.includes("真の能力は使っていません"), "契約の箱に真の能力の項目名はなく、説明の注記もない(年俸の決まり方は用語集に)");
await page.click("#player-info .link");
await page.waitForFunction(() => document.querySelectorAll("#team-war td").length === 4);
const warTid = pyGame(`print(json.dumps(g.player(a["id"])["player"]["team_id"]))`, JSON.stringify({ id: warPid }));
const pyTeamWar = pyGame(`w = g.team(a["id"])["war"]
print(json.dumps([w["batters"], w["pitchers_ra"], w["pitchers_fip"], w["total_ra"]]))`, JSON.stringify({ id: warTid }));
check(JSON.stringify(await page.$$eval("#team-war td", (tds) => tds.map((td) => td.textContent))) === JSON.stringify(pyTeamWar), `チームのページの WAR の合計(野手・投手別)が、計算本体と同じ(合計 ${pyTeamWar[3]})`);
const pyBudget = pyGame(`b = g.budget_info(a["id"])
d = g.team(a["id"])
print(json.dumps([b["total_text"], b["cap_text"], b["tier_label"], [r["salary_text"] for r in d["salaries"][:3]], b["usage"]], ensure_ascii=False))`, JSON.stringify({ id: warTid }));
const budgetText = await page.textContent("#team-budget");
const salaryCells = await page.$$eval("#team-salaries tbody tr", (trs) => trs.slice(0, 3).map((tr) => tr.children[3].textContent));
check(budgetText.includes(`総年俸 ${pyBudget[0]}`) && budgetText.includes(`年俸に使える額 ${pyBudget[1]}(使用率 ${pyBudget[4]}%)`) && budgetText.includes("設備の維持費") && budgetText.includes("設備:練習設備") && budgetText.includes(`球団の規模:${pyBudget[2]}`) && !budgetText.includes("お金のルール") && !budgetText.includes("null"), `チームのページに総年俸・資金・使用率・球団の規模が出て、計算本体と同じ(「${budgetText.slice(0, 50)}…」。D-353)`);
check(JSON.stringify(salaryCells) === JSON.stringify(pyBudget[3]) && (await page.$$eval("#team-salaries thead th", (ths) => ths.map((t) => t.textContent))).join("|").includes("年俸(万円)|残り"), `チームのページの年俸の表(高い順。年俸・残りの列)が、計算本体と同じ(上位 ${salaryCells.join("・")})`);
await findBlocks();
await page.click("#screen-team .back");
await page.waitForFunction(() => !document.querySelector("#screen-player").hidden);
await page.click("#player-kind button[data-value=basic]"); // 後の確認は「基本」を前提にしている
await page.waitForFunction(() => document.querySelectorAll("#player-season td").length > 3);
await page.click("#screen-player .back");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0);
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("FIP")));
await page.click("#stats-table th button.sort >> text=FIP");
await waitStats("FIP(低い順)");

// 能力(作り直し①。D-335):いつでも見られる。並び順(FIP の低い順。固定列)を保ったまま出る
await page.click("#stats-kind button[data-value=ability]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("スタミナ")) && document.querySelectorAll("#stats-table tbody tr").length > 0);
const pyAbility = pyGame(`
t = g.ability_table("pitcher", sort="fip", order="asc")
keys = ([t["extra_column"]["key"]] if t["extra_column"] else []) + [c["key"] for c in t["columns"]]
print(json.dumps([[r["name"]] + [r["values"][k] for k in keys] for r in t["rows"]], ensure_ascii=False))`);
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyAbility), `「能力」の表が、並び順(FIP の低い順。固定列)を保ったまま出る(計算本体と同じ。${screenRows.length}人)`);
check(screenRows.every((r) => /^\d{3}km\/h$/.test(r[2]) && /^[SABCDEFG] \d{1,3}$/.test(r[3]) && /^[SABCDEFG] \d{1,3}$/.test(r[4])), "能力は 球速 km/h・ランクと 1〜100 で表示(D-347)");
check((await page.$$eval("#stats-sort optgroup", (gs) => gs.map((g) => g.label))).includes("能力"), "並び順の欄に能力の項目が出る");
await grab();
await page.selectOption("#stats-sort", "stamina");
await waitStats("スタミナ(高い順)");
check((await headsOnScreen())[1] !== "FIP", "並び順の欄で能力の項目を選ぶと、その項目で並び替わり、固定列は消える");
await page.selectOption("#stats-sort", "fip");
await waitStats("FIP(低い順)");

// 選手のページ
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => [...document.querySelectorAll("#stats-table th")].some((th) => th.textContent.replace(/^[▲▼]/, "").startsWith("登板")));
await waitStats("FIP(低い順)"); // 能力 → 基本 と切り替えても、FIP の低い順(固定列)が保たれる
const pitcherName = (await statsOnScreen())[0][0];
await page.click("#stats-table tbody tr:first-child .link");
await page.waitForFunction((n) => document.querySelector("#player-name").firstChild?.textContent === n, pitcherName);
await page.waitForSelector("#player-games table");
const pid = pyGame(`print(json.dumps(g.stats("pitcher", sort="fip", order="asc", league=a["lg"])["rows"][0]["player_id"]))`, JSON.stringify({ lg: statsLeague }));
const pyPlayer = pyGame(`d = g.player(a["id"])\nprint(json.dumps({"season": [d["season"]["tables"]["basic"]["values"][c["key"]] for c in d["season"]["tables"]["basic"]["columns"]], "games": [x["day"] for x in d["games"]]}, ensure_ascii=False))`, JSON.stringify({ id: pid }));
const seasonOnScreen = await page.$$eval("#player-season td", (tds) => tds.map((td) => td.textContent));
check(JSON.stringify(seasonOnScreen) === JSON.stringify(pyPlayer.season), `選手のページのシーズン通算が、計算本体と同じ(${pitcherName})`);
const gameDays = await page.$$eval("#player-games tbody td.sticky .link", (ls) => ls.map((l) => Number(l.textContent.replace("日目", ""))));
check(JSON.stringify(gameDays) === JSON.stringify(pyPlayer.games.slice(0, 20)), `試合ごとの成績が新しい順(${gameDays.length}試合)`);
const abilitiesPy = pyGame(`print(json.dumps([[x["label"], x["text"]] for x in g.player(a["id"])["player"]["abilities"]], ensure_ascii=False))`, JSON.stringify({ id: pid }));
const abilitiesOnScreen = await page.$$eval("#player-abilities .ability", (els) => els.map((e) => [e.querySelector(".ability-label").textContent, e.querySelector(".ability-value").textContent]));
check(JSON.stringify(abilitiesOnScreen) === JSON.stringify(abilitiesPy), `選手のページの上に能力が出る(${abilitiesPy.map((x) => x.join(" ")).join("・")})`);
const skillsPy = pyGame(`print(json.dumps([x["label"] for x in g.player(a["id"])["player"]["skills"]], ensure_ascii=False))`, JSON.stringify({ id: pid }));
const skillsOnScreen = await page.$$eval("#player-skills .skill", (els) => els.map((e) => e.textContent));
check(JSON.stringify(skillsOnScreen) === JSON.stringify(skillsPy) && (skillsPy.length || (await page.textContent("#player-skills")).includes("なし")), `選手のページに特殊能力が出る(${skillsPy.join("・") || "なし"})`);
await grab();

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
await findBlocks();
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
check((await page.locator("#stadium-answers").count()) === 0 && !/\b[01]\.\d{3}\b/.test(await page.textContent("#stadium-record")), "球場のページに真の倍率が出ない(答え合わせモードはなくした)");
// 本拠地とアウェイの比較の表と、推定(1シーズン目は「まだ推定できません」)(②b)
const cmpOnScreen = await page.$$eval("#stadium-compare tbody tr", (trs) => trs.map((tr) => [...tr.children].slice(1).map((td) => td.textContent)));
const cmpPy = pyGame(`d = g.stadium(a["id"])\nprint(json.dumps([[d["this_season"][k][c] for c in ("home", "away", "ratio")] for k in ("home_run", "babip", "runs")], ensure_ascii=False))`, JSON.stringify({ id: homeTeamId }));
check(JSON.stringify(cmpOnScreen) === JSON.stringify(cmpPy), `本拠地とアウェイの比較の表(本塁打/打席・BABIP・得点/打席)が、計算本体と同じ(本塁打の比 ${cmpPy[0][2]})`);
await findBlocks();
check((await page.textContent("#stadium-estimate")).includes("まだ推定できません"), "1シーズン目は、推定した球場補正の代わりに「まだ推定できません」と出る");
await page.click("#screen-stadium .back");
await page.click("#screen-game .back");

// 裏の計算から届いたメッセージに、隠し情報(成長タイプ・型の名前、伸びやすさなど)がない(D-338)
const offMessages = await page.evaluate(() => window.__messages.join("\n"));
const leaked = [...HIDDEN.words.filter((w) => offMessages.includes(w)), ...HIDDEN.keys.filter((k) => offMessages.includes(`"${k}"`))];
check(leaked.length === 0, `裏の計算から届いたメッセージに隠し情報がない${leaked.length ? `(見つかった: ${leaked.join("、")})` : ""}`);
check((await page.locator("input[name=answer-level]").count()) === 0, "メニューに答え合わせモードがない(D-338)");
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0);
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
// シーズン中の AI どうしのトレード(D-378)があるので、指紋 (d)(シーズンだけ)ではなく、同じ新規開始を Python で最後まで進めた結果と比べる
const [pyDigest] = python(`
import sys
from pennant import api
from pennant.fingerprint import _digest, season_record
g = api.Game.new(1, [None] * 12, int(sys.argv[1][1:]) - 1, season_seed=13, baselines="default")
g.advance(125)
print(_digest(season_record(g.state.season.result())))
`, digest[1]).trim().split(" ");
check(digest[2] === pyDigest, `最後の結果が、同じ新規開始を Python で進めた結果と同じ(${digest[2].slice(0, 16)}…。トレード ${digest[2] === expected.season ? "なし" : "あり"})`);
results.push(`  自球団(セーブデータの中): ${digest[1]}。指紋には入らない`);
await standingsOnScreen(0);
// シーズンの最後で、成績の画面を開く時間(集計は進めた日の分だけ足してあるので、すぐ開く)
let s0 = Date.now();
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelector("#stats-loading").hidden && document.querySelectorAll("#stats-table tbody tr").length > 0);
results.push(`  シーズンの最後で、個人成績を開くまで: ${((Date.now() - s0) / 1000).toFixed(2)} 秒`);
// 読み込み直したセーブデータ(125日目)では、最初の1回だけ集計を作る
await page.reload();
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
await page.setInputFiles("#open-file-start", last.path);
await page.waitForSelector("#screen-progress:not([hidden])");
s0 = Date.now();
await page.click("#tabs button[data-tab=stats]");
await page.waitForFunction(() => document.querySelector("#stats-loading").hidden && document.querySelectorAll("#stats-table tbody tr").length > 0);
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
  return pyProc(code, false, ...args);
}

// オフの手続き(①a。D-270〜D-273):画面で行った操作を順に記録し、Python 側で同じ順に再生して結果を比べる
const procOps = [];
function opsPy(ops = procOps) {
  return `r = None
for name, args in json.loads(${JSON.stringify(JSON.stringify(ops))}):
    r = getattr(g, name)(*args)`;
}
// 年度を確定して、記録した操作を再生してから code を実行する
function pyAfter(code, ...args) {
  return JSON.parse(python(`
import json, sys
from pennant import api
g = api.Game.load(open(sys.argv[1], "rb").read())
a = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
g.year_end()
${opsPy()}
${code}
`, last.path, ...args));
}

// market=true なら、記録した操作の後(市場の段階)で止めて code を実行する。false なら「全部おまかせ」で完了して 1 日進める
function pyProc(code, market, ...args) {
  return pyAfter(`${market ? "" : "g.offseason_auto()\nsummary = g.offseason_summary()\ng.advance(1)"}
${code}`, ...args);
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
check(JSON.stringify(champsOnScreen) === JSON.stringify(champsPy) && (await page.textContent("#yearend-note")) === "確定すると戻せません。", `確認の画面に、優勝チームと「戻せません」の説明が出る(${champsPy.join(" / ")})`);
await findBlocks();
check((await page.textContent("#yearend-dirty")).includes("保存済み"), "確認の画面に、保存の状態(保存済み)が出る");
const [beforeDl] = await Promise.all([page.waitForEvent("download"), page.click("#yearend-save")]);
await beforeDl.saveAs(join(work, `before-yearend-${beforeDl.suggestedFilename()}`));
await page.waitForFunction(() => document.querySelector("#yearend-dirty").textContent.includes("保存済み"));
check(!(await page.locator("#screen-yearend").isHidden()), "確認の画面の「先に保存する」で保存しても、確認の画面に留まる");
const ye0 = Date.now();
await page.click("#yearend-go");
// 操作する球団があるので、オフの手続きの画面(契約の段階)になる(①a。D-271・D-272)
page.on("dialog", (d) => d.accept().catch(() => {})); // これ以降の確認(自由契約・次の手続き・全部おまかせ・答え合わせモード)はすべて承諾する
await page.waitForFunction(() => !document.querySelector("#screen-procedure").hidden && document.querySelectorAll("#proc-body tbody tr").length > 0, null, { timeout: 120000 });
const yeSeconds = (Date.now() - ye0) / 1000;
const tabTexts = await page.$$eval("#proc-steps li", (ls) => ls.map((l) => l.textContent));
const tabBoxes = await page.$$eval("#proc-steps li", (ls) => ls.map((l) => { const r = l.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.height), l.scrollWidth <= l.clientWidth + 1]; }));
const overAtStart = pyAfter(`print(json.dumps(g.offseason_view()["renewal"]["over"]))`);
check((await page.textContent("#proc-title")) === "契約" && tabTexts.join("・") === "契約・FA・外国人・ドラフト・市場・トレード・現役・完了" && (await page.isDisabled("#proc-next")) === (overAtStart > 0), `年度を確定すると、オフの手続きの「契約」の段階になる。段階のタブは 8 つ(外国人・トレード・現役ドラフトを含む。D-376、D-378、D-380)。「次の手続きへ」は資金を超えている間だけ押せない(超過 ${Number(overAtStart).toLocaleString()} 万円。${yeSeconds.toFixed(1)} 秒。D-271、D-361)`);
check(tabBoxes.every((b) => b[0] === tabBoxes[0][0] && b[1] === tabBoxes[0][1] && b[1] < 48 && b[2]), `段階のタブは 1 行で、折り返さない(高さ ${tabBoxes[0][1]}px)`);
// 設備(D-383):契約の段階の「設備」の欄で段階を変えると、年俸に使える額が変わる(Python と同じ)。元に戻す
await page.waitForSelector("#facility-box");
const facBefore = await page.textContent("#facility-box summary");
const facPy0 = pyAfter(`print(json.dumps([g.facilities_view()["mine"]["levels"]["practice"], g.budget_info(g.state.my_team_id)["cap_text"]], ensure_ascii=False))`);
check(facBefore.includes(facPy0[1]), `設備の欄:「${facBefore}」(練習設備 段階 ${facPy0[0]})`);
await page.click("#facility-box summary");
const facNew = facPy0[0] === 5 ? 4 : facPy0[0] + 1;
await page.selectOption("#facility-practice", String(facNew));
await page.waitForFunction((t) => document.querySelector("#facility-box summary") && document.querySelector("#facility-box summary").textContent !== t, facBefore, { timeout: 30000 });
procOps.push(["set_facility", ["practice", facNew]]);
const facPy1 = pyAfter(`print(json.dumps([g.budget_info(g.state.my_team_id)["cap_text"], g.budget_info(g.state.my_team_id)["upkeep_text"]], ensure_ascii=False))`);
check((await page.textContent("#facility-box summary")).includes(facPy1[0]) && (await page.textContent("#facility-box summary")).includes(facPy1[1]), `練習設備を段階 ${facNew} にすると、維持費 ${facPy1[1]}・年俸に使える額 ${facPy1[0]} になる`);
await page.selectOption("#facility-practice", String(facPy0[0]));
await page.waitForFunction((t) => document.querySelector("#facility-box summary").textContent.includes(t), facPy0[1], { timeout: 30000 });
procOps.push(["set_facility", ["practice", facPy0[0]]]);
await page.click("#facility-box summary"); // 閉じておく
// 画面の測定(幅 390。D-270):表の上端が画面の上から 40% 以内、操作の行は最大 2 行、説明ブロックがない。契約・FA・ドラフト・市場で測る(①b)
async function measureDecision(label) {
  await findBlocks();
  const vp = page.viewportSize();
  // 並べ替えた列の ▲▼ は、どの幅でもその見出しの中にある(広い画面で固定をやめた列も)
  const arrow = await page.evaluate(() => {
    const a = document.querySelector("#proc-body th.sorted .arrow");
    if (!a) return null;
    const r = a.getBoundingClientRect();
    const t = a.closest("th").getBoundingClientRect();
    return r.left >= t.left - 1 && r.right <= t.right + 1 && r.top >= t.top - 1 && r.bottom <= t.bottom + 1;
  });
  check(arrow === true, `幅 ${vp.width} で、${label}の表の並べ替えた列の ▲▼ が見出しの中にある`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  const layout = await page.evaluate(() => {
    const scr = document.querySelector("#screen-procedure");
    const tableTop = scr.querySelector("#proc-body .table-wrap").getBoundingClientRect().top;
    const controls = [...scr.querySelectorAll("button, select, input")].filter((e) => e.offsetParent && !e.classList.contains("back") && !e.closest("#proc-autolog") && e.getBoundingClientRect().bottom <= tableTop);
    const rows = [];
    for (const e of controls) {
      const t = e.getBoundingClientRect().top;
      if (!rows.some((x) => Math.abs(x - t) < 12)) rows.push(t);
    }
    const blocks = [...scr.querySelectorAll("p, dl, .info, .contract-box")].filter((e) => e.offsetParent && e.getBoundingClientRect().bottom <= tableTop && e.id !== "proc-message" && e.textContent.trim().length > 0);
    const lis = [...document.querySelectorAll("#proc-steps li")].map((l) => Math.round(l.getBoundingClientRect().top));
    return { tableTop, ratio: tableTop / window.innerHeight, rows: rows.length, blocks: blocks.map((b) => b.id || b.className), tabsOneLine: new Set(lis).size === 1 };
  });
  check(layout.ratio <= 0.4, `幅 390 で、${label}の表の上端が画面の上から ${(layout.ratio * 100).toFixed(0)}%(${layout.tableTop.toFixed(0)}px。40% 以内。D-270)`);
  check(layout.rows <= 2 && layout.blocks.length === 0 && layout.tabsOneLine, `${label}:表の上の操作は ${layout.rows} 行(最大 2 行)、説明ブロックがない${layout.blocks.length ? `(${layout.blocks.join("・")})` : ""}、段階のタブは折り返さない`);
  await page.setViewportSize(vp);
  await page.waitForTimeout(300);
}
await measureDecision("契約");
// 契約の表(D-272・D-284・D-358):自球団の全選手。列は 名前・WAR・状態・新しい年俸・増減・ポジション・年齢・今の契約・出場。初期は WAR の低い順
const rosterHeads = async () => page.$$eval("#proc-body thead th", (ths) => ths.map((t) => t.textContent.replace(/^[▲▼]/, "")));
const rosterCells = async (labels) => page.$$eval("#proc-body tbody tr:not(.detail)", (trs, labels) => { const heads = [...document.querySelectorAll("#proc-body thead th")].map((t) => t.textContent.replace(/^[▲▼]/, "")); return trs.map((tr) => [tr.querySelector("td .link").textContent, ...labels.map((l) => tr.children[heads.indexOf(l)].textContent)]); }, labels);
const contractPy = (args) => pyAfter(`t = g.contract_table(**a)
print(json.dumps([[r["name"]] + [r["values"][c["key"]] for c in t["columns"]] for r in t["rows"]] + [[t["sort"]["label"], t["order"], [c["label"] for c in t["columns"]]]], ensure_ascii=False))`, JSON.stringify(args));
let cHead = await rosterHeads();
let cPy = contractPy({});
let cRows = await rosterCells(cPy.at(-1)[2]);
check(cHead.join("|") === "選手|WAR|状態|新しい年俸|増減|ポジション|年齢|今の契約|出場" && cRows.length === cPy.length - 1 && cRows.every((r, i) => r.join("|") === cPy[i].join("|")) && cPy.at(-1)[1] === "asc", `契約の表に自球団の全選手(${cRows.length} 人)が WAR の低い順に出て、計算本体と同じ(列:${cHead.join("・")})`);
const statusPy = () => pyAfter(`v = g.offseason_view()["renewal"]
print(json.dumps([v["roster_counts"], v["status_labels"], v["players"], v["over"], v["can_next"], v["cap"] - v["team_total"]], ensure_ascii=False))`);
let rnPy = statusPy();
const wantInfo = () => `${rnPy[2]} 人:` + ["accepted", "declared", "released", "contracted"].filter((k) => rnPy[0][k]).map((k) => `${rnPy[1][k]} ${rnPy[0][k]}`).join("・");
check((await page.textContent("#proc-info")).startsWith(wantInfo()) && !(await page.textContent("#proc-info")).includes("未提示"), `更改は段階の始めに査定で済み、状態バーの内訳が計算本体と同じ(「${await page.textContent("#proc-info")}」。D-358)`);
check((await page.textContent("#proc-budget")).startsWith("総年俸") && (await page.textContent("#proc-budget")).includes("資金の") && !(await page.$("#renew-auto")), `状態バーに総年俸と資金の使用率が出て、「自動案で更改」のボタンはない(「${await page.textContent("#proc-budget")}」)`);
check(!(await page.textContent("#proc-body")).includes("志望"), "契約の画面に志望の重みは出ない(D-245、D-338)");
// 状態の絞り込み「更改済」:更改した選手だけ。選手を押すと、更改の結果(前の年俸 → 新しい年俸)と「自由契約にする」だけのパネル
await page.selectOption("#contract-status", "accepted");
await page.waitForFunction(() => document.querySelector("#contract-status").value === "accepted" && [...document.querySelectorAll("#proc-body tbody tr:not(.detail)")].every((tr) => tr.textContent.includes("更改済")));
check((await page.$$("#proc-body tbody tr:not(.detail)")).length === rnPy[0].accepted, `状態で「更改済」に絞り込むと、更改した選手だけが出る(${rnPy[0].accepted} 人)`);
const [keptName, keptPid, keptOld, keptNew] = pyAfter(`print(json.dumps(next([r["name"], r["player_id"], r["renewal"]["old_text"], r["renewal"]["new_text"]] for r in g.contract_table("all", status="accepted")["rows"] if r["can_release"]), ensure_ascii=False))`); // 最低人数を割らない選手
await page.click(`#proc-body tbody tr .name-cell .link:text-is("${keptName}")`);
await page.waitForSelector(`#offer-panel[data-player-id="${keptPid}"] #offer-release`); // 前に開いていたパネルが残っている間に読まないよう、押した選手のパネルを待つ
const panelText = await page.textContent("#offer-panel");
check(!(await page.$("#offer-send")) && !(await page.$("#offer-salary")) && panelText.includes(`更改:${keptOld} → ${keptNew}`), `更改した選手のパネルは、更改の結果(「${panelText.slice(0, 40)}…」)と「自由契約にする」だけ(提示はない)`);
const beforeKept = await page.textContent("#proc-info");
await page.click("#offer-release");
await page.waitForFunction((b) => document.querySelector("#proc-info").textContent !== b, beforeKept, { timeout: 30000 });
procOps.push(["offseason_contract_release", [keptPid]]);
const keptPy = pyAfter(`print(json.dumps([any(p.id == a["id"] for p in g.state.procedure.market), next(p for p in g.state.procedure.market if p.id == a["id"]).contract]))`, JSON.stringify({ id: keptPid }));
check(keptPy[0] && keptPy[1] === null && (await page.textContent("#proc-info")).includes("自由契約"), "更改済の選手を自由契約にすると、市場へ出て契約は消える(状態バーに「自由契約」が数えられる)");
// 資金を超えていれば、年俸の高い選手から自由契約にして解消する(超えている間は「次の手続きへ」が押せない。D-361)
rnPy = statusPy();
check((await page.isDisabled("#proc-next")) === !rnPy[4], `「次の手続きへ」は資金を超えている間だけ押せない(超過 ${Number(rnPy[3]).toLocaleString()} 万円)`);
for (let i = 0; i < 40 && (rnPy[3] > 0 || rnPy[5] < 20000 || rnPy[2] > 61); i++) { // 超過を解消し、あとのドラフト・FA・市場の分の資金(2 億円)と枠(FA 1・ドラフト 6・市場 1)も空ける
  const [pid, name] = pyAfter(`print(json.dumps(next([r["player_id"], r["name"]] for r in g.contract_table("all", sort="contract", order="desc")["rows"] if r["can_release"] and r["in_team"]), ensure_ascii=False))`);
  await page.selectOption("#contract-status", "all");
  await page.waitForFunction(() => document.querySelector("#contract-status").value === "all");
  await page.click(`#proc-body tbody tr .name-cell .link:text-is("${name}")`);
  await page.waitForSelector(`#offer-panel[data-player-id="${pid}"] #offer-release`);
  const before = await page.textContent("#proc-info");
  await page.click("#offer-release");
  await page.waitForFunction((b) => document.querySelector("#proc-info").textContent !== b, before, { timeout: 30000 });
  procOps.push(["offseason_contract_release", [pid]]);
  rnPy = statusPy();
}
check(rnPy[3] === 0 && !(await page.isDisabled("#proc-next")), `資金の中に収めると「次の手続きへ」が押せる(資金の残り ${Number(rnPy[5]).toLocaleString()} 万円)`);
// ポジション「投手」:種類(基本・セイバー・WAR)を選べる。見出しのタップで並べ替え
await page.selectOption("#contract-status", "all");
await page.selectOption("#contract-group", "pitcher");
await page.waitForSelector("#contract-kind");
await page.selectOption("#contract-kind", "saber");
await page.waitForFunction(() => document.querySelector("#contract-kind").value === "saber" && document.querySelector("#proc-body thead").textContent.includes("FIP"));
cPy = contractPy({ group: "pitcher", kind: "saber" });
cRows = await rosterCells(cPy.at(-1)[2]);
cHead = await rosterHeads();
check(cHead.slice(0, 2).join("|") === "選手|WAR(失点版)" && cHead.includes("FIP") && cRows.length === cPy.length - 1 && cRows.every((r, i) => r.join("|") === cPy[i].join("|")), `「投手」に絞ると種類を選べる。セイバーで FIP などの列が足され、計算本体と同じ(${cRows.length} 人)`);
await page.click("#proc-body thead th button:has-text('年齢')");
await page.waitForFunction(() => { const heads = [...document.querySelectorAll("#proc-body thead th")].map((t) => t.textContent); const i = heads.findIndex((h) => h.replace(/^[▲▼]/, "").startsWith("年齢")); const ages = [...document.querySelectorAll("#proc-body tbody tr:not(.detail)")].map((tr) => parseInt(tr.children[i].textContent)); return heads[i].includes("▲") && ages.every((x, k) => k === 0 || x >= ages[k - 1]); });
check(true, "見出しの「年齢」をタップすると、年齢の低い順に並ぶ");
// 画面を行き来しても、絞り込みと並び順は保たれる
await page.click("#screen-procedure .back");
await page.click("#open-offseason");
await page.waitForFunction(() => !document.querySelector("#screen-procedure").hidden && document.querySelector("#contract-group") && document.querySelector("#contract-group").value === "pitcher");
check((await page.inputValue("#contract-kind")) === "saber" && (await rosterHeads()).some((h) => h.startsWith("年齢")) && (await page.$eval("#proc-body thead th.sorted", (th) => th.textContent.replace(/^[▲▼]/, ""))).startsWith("年齢"), "進行の画面に戻って開き直しても、絞り込み・種類・並び順は保たれる");
// 全員が決まると「次の手続きへ」が押せる。AI 球団の自由契約は、契約の段階の終わりに行われる(D-272)
await page.waitForFunction(() => !document.querySelector("#proc-next").disabled);
const aiBefore = pyAfter(`print(json.dumps([g.state.procedure.ai_release_done, len([x for x in g.state.procedure.released if x["team_id"] != g.state.my_team_id])]))`);
check(aiBefore[0] === false, "契約の段階の途中では、AI 球団の自由契約はまだ行われていない");
await page.click("#proc-next");
await page.waitForFunction(() => document.querySelector("#proc-title").textContent !== "契約", null, { timeout: 60000 });
procOps.push(["offseason_next", []]);
const faStart = pyAfter(`v = g.offseason_view()
print(json.dumps([v["stage"], v["fa"]["counts"] if v.get("fa") else None, g.state.procedure.ai_release_done, len([x for x in g.state.procedure.released if x["team_id"] != g.state.my_team_id])], ensure_ascii=False))`);
check(faStart[2] === true && faStart[3] > aiBefore[1], `「次の手続きへ」で、AI 球団の自由契約(${faStart[3] - aiBefore[1]} 人)を行って次の段階へ`);
// 判断の画面の表(①b):計算本体の decision_table と同じ行・列か(画面は並べ替えた列を名前のすぐ右に固定する)
const decisionPy = (args, n = 12) => pyAfter(`t = g.decision_table(**a)
print(json.dumps([[c["label"] for c in t["columns"]], t["sort"]["label"], [[r["name"]] + [r["values"][c["key"]] for c in t["columns"]] for r in t["rows"][:${n}]]], ensure_ascii=False))`, JSON.stringify(args));
const decisionUi = async (n = 12) => page.$$eval("#proc-body tbody tr:not(.detail)", (trs, n) => { const heads = [...document.querySelectorAll("#proc-body thead th")].map((t) => t.textContent.replace(/^[▲▼]/, "")); return trs.slice(0, n).map((tr) => Object.fromEntries(heads.map((h, i) => [h, i === 0 ? tr.querySelector("td .link").textContent : tr.children[i].textContent]))); }, n);
async function sameAsPy(args, label) {
  const [cols, sortLabel, rows] = decisionPy(args);
  const heads = await rosterHeads();
  const ui = await decisionUi();
  const expectHeads = ["選手", sortLabel, ...cols.filter((c) => c !== sortLabel)];
  const same = JSON.stringify(heads.slice(0, expectHeads.length)) === JSON.stringify(expectHeads) && ui.length === rows.length && ui.every((r, i) => r["選手"] === rows[i][0] && cols.every((c, j) => r[c] === rows[i][j + 1]));
  check(same, `${label}:表の行と列が計算本体と同じ(列:${heads.join("・")}。並べ替えた「${sortLabel}」は名前のすぐ右)`);
  return { heads, ui };
}
if (faStart[0] === "fa") {
  // FA(D-260・D-296〜D-298):状態バーに「ラウンド 1/3」。表は 名前・WAR・年齢・ポジション・加入後の序列・算定年俸・出場・前の所属・状態
  check((await page.textContent("#proc-title")) === "FA" && (await page.textContent("#proc-info")).startsWith(`ラウンド 1/3:宣言 ${faStart[1].declared}`), `FA の段階:状態バーに「ラウンド 1/3」と宣言した人数(${faStart[1].declared} 人)が出て、計算本体と同じ`);
  await page.waitForSelector("#proc-body tbody tr");
  await measureDecision("FA");
  const fa0 = await sameAsPy({ stage: "fa" }, "FA");
  check(["WAR", "総合", "年齢", "ポジション", "加入後の序列", "算定年俸", "出場", "前の所属", "状態"].every((h, i) => fa0.heads[i + 1] === h) && !fa0.heads.includes("真の総合"), "FA の列は 名前・WAR・総合(見せる能力)・年齢・ポジション・加入後の序列・算定年俸・出場・前の所属・状態");
  check(fa0.ui.every((r) => /^\d+ 番手( ●)?$/.test(r["加入後の序列"])), "加入後の序列は「N 番手」(一軍の枠の目安に入るなら ●)");
  // 自球団の状況のパネル(状態バーを押すと開く。D-298)
  await page.click("#proc-bar .bar-text");
  await page.waitForSelector("#outlook-panel");
  const outlookPy = pyAfter(`o = g.team_outlook()\nprint(json.dumps([o["space"], [[r["label"], r["count"], r["target"], r["thin"]] for r in o["positions"]]], ensure_ascii=False))`);
  const outlookUi = await page.$$eval("#outlook-panel tbody tr", (trs) => trs.map((tr) => [tr.children[0].textContent, tr.children[1].textContent]));
  check(outlookUi.length === outlookPy[1].length && outlookUi.every((r, i) => r[0] === outlookPy[1][i][0] && r[1].startsWith(`${outlookPy[1][i][1]}/${outlookPy[1][i][2]}`) && r[1].includes("◆") === outlookPy[1][i][3]) && (await page.textContent("#outlook-panel")).includes(`空き枠 ${outlookPy[0]}`), `状態バーを押すと、自球団の状況(ポジション別の人数/目安・手薄の印・主な選手・空き枠 ${outlookPy[0]})が開き、計算本体と同じ`);
  await page.click("#proc-bar .bar-text");
  await page.waitForFunction(() => !document.querySelector("#outlook-panel"));
  // 絞り込み 1 本(投手)と種類(セイバー)
  await page.selectOption("#fa-group", "pitcher");
  await page.waitForFunction(() => document.querySelector("#fa-kind"));
  await page.selectOption("#fa-kind", "saber");
  await page.waitForFunction(() => document.querySelector("#proc-body thead") && document.querySelector("#proc-body thead").textContent.includes("FIP") || document.querySelector("#fa-empty"));
  if (await page.$("#proc-body tbody tr")) await sameAsPy({ stage: "fa", group: "pitcher", kind: "saber" }, "FA の投手・セイバー");
  await page.selectOption("#fa-group", "all");
  await page.waitForFunction(() => !document.querySelector("#fa-kind") && document.querySelector("#proc-body tbody tr"));
  // 資金の残りで出せる選手(算定年俸が資金の残り以下)を選ぶ(作り直し②。資金を超える提示はできない)
  const [faName] = pyAfter(`b = g.budget_info(g.state.my_team_id)
room = b["cap"] - b["total"]
t = g.decision_table("fa")
print(json.dumps(next(([r["name"]] for r in sorted(t["rows"], key=lambda r: r["deal"]["calc_salary"]) if r["deal"]["calc_salary"] <= room // 3), [min(t["rows"], key=lambda r: r["deal"]["calc_salary"])["name"]]), ensure_ascii=False))`); // あとのドラフト・市場の分の資金を残す
  await page.click(`#proc-body tbody tr .link:text-is("${faName}")`);
  await page.waitForSelector("#fa-panel");
  const faPid = await page.getAttribute("#fa-panel", "data-player-id");
  check(Boolean(await page.$("#offer-plus10")) && !(await page.textContent("#fa-panel")).includes("志望"), "FA の提示のパネル:年俸と ±% のボタンが出る。志望の重みは出ない");
  await page.selectOption("#fa-years", "2");
  const faSalary = Number(await page.inputValue("#offer-salary"));
  await page.click("#fa-send");
  await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("提示 1"), null, { timeout: 30000 });
  procOps.push(["offseason_fa_offer", [faPid, 2, faSalary]]);
  await page.click("#fa-close");
  await page.waitForFunction(() => document.querySelector("#proc-message").textContent.includes("ラウンド 1 の結果"), null, { timeout: 60000 });
  procOps.push(["offseason_fa_close", []]);
  const faLastUi = await page.textContent("#proc-message");
  const faLastPy = pyAfter(`print(json.dumps([[x["name"], x["team_name"], x["years"], x["salary_text"]] for x in r["last_round"]["signed"]], ensure_ascii=False))`);
  check(faLastPy.every((x) => faLastUi.includes(`${x[0]} → ${x[1]}(${x[2]} 年・${x[3]})`)) && (faLastPy.length > 0 || faLastUi.includes("ありません")) && (await page.textContent("#proc-info")).startsWith("ラウンド 2/3"), `「ラウンド 1 を締める」(状態バー)の結果(契約 ${faLastPy.length} 人)が計算本体と同じ。状態バーはラウンド 2/3`);
  await page.click("#proc-next");
  await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "外国人", null, { timeout: 60000 });
  procOps.push(["offseason_next", []]);
  const faMine = pyAfter(`print(json.dumps([x for x in g.state.procedure.fa_log if x["team_id"] == g.state.my_team_id and x["round"] > 1]))`);
  check(faMine.length === 0, `FA の「次の手続きへ」では、自球団は 2 ラウンド目以降の提示をしない(AI の代行をしない。D-271)${faMine.length ? JSON.stringify(faMine.slice(0, 2)) : ""}`);
}
// 外国人市場(D-376):候補の能力はスカウトの見立て(D-418)。年俸は内部の評価の段階の表。獲得の希望を入れて締めると、前年の順位が低い球団から 1 人ずつ
check((await page.textContent("#proc-title")) === "外国人" && (await page.textContent("#proc-info")).startsWith("外国人市場 24 人"), `外国人の段階:状態バーに「${await page.textContent("#proc-info")}」`);
await page.waitForSelector("#proc-body tbody tr");
await measureDecision("外国人市場");
const fo0 = await sameAsPy({ stage: "foreign" }, "外国人市場");
check(["見立て", "年齢", "ポジション", "加入後の序列", "年俸", "状態"].every((h) => fo0.heads.includes(h)) && !fo0.heads.includes("評価") && !fo0.heads.includes("スカウトの一言") && (await page.textContent("#proc-info")).includes("一軍 4 まで"), `外国人市場の列は見立ての総合(評価と一言はない。D-418。列:${fo0.heads.join("・")})。状態バーに一軍は 4 人まで`);
const [foName] = pyAfter(`t = g.decision_table("foreign")
print(json.dumps([min(t["rows"], key=lambda r: (r["deal"]["calc_salary"], r["player_id"]))["name"]], ensure_ascii=False))`); // 資金の残りで出せるよう、年俸の最も安い候補
await page.click(`#proc-body tbody tr:not(.detail) .name-cell .link:text-is("${foName}")`);
await page.waitForSelector("#foreign-panel");
const foPid = await page.getAttribute("#foreign-panel", "data-player-id");
await page.click("#foreign-send");
await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("希望 1") || document.querySelector("#proc-message").className.includes("ng"), null, { timeout: 30000 });
check((await page.textContent("#proc-info")).includes("希望 1"), `外国人の獲得の希望が通る(${await page.textContent("#proc-message")}${await page.textContent("#proc-info")})`);
procOps.push(["offseason_foreign_offer", [foPid]]);
await page.click("#foreign-close");
await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("終了"), null, { timeout: 60000 });
procOps.push(["offseason_foreign_close", []]);
const foPy = pyAfter(`print(json.dumps([[x["name"], x["team_name"]] for x in r["last_foreign"]["signed"]], ensure_ascii=False))`);
check((await page.textContent("#proc-message")).startsWith(`外国人市場の結果:契約 ${foPy.length} 人`) && foPy.length > 0, `「外国人市場を締める」で、前年の順位が低い球団から獲得し、結果(契約 ${foPy.length} 人)が計算本体と同じ`);
await page.click("#proc-next");
await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "ドラフト", null, { timeout: 60000 });
procOps.push(["offseason_next", []]);
// ドラフト(D-299、D-368、D-369):1 巡目は入札(状態バーに「入札・あなたの番」)。表(並べ替えは見出し・絞り込み 1 本)→ 名前を押して入札 → 抽選の結果 →「この段階をおまかせ」(確認つき)
check((await page.textContent("#proc-title")) === "ドラフト" && (await page.textContent("#proc-info")).startsWith("1/10 巡・入札・あなたの番"), `ドラフトの段階:状態バーに「1/10 巡・入札・あなたの番」(「${await page.textContent("#proc-info")}」)`);
await page.waitForSelector("#proc-body tbody tr");
await measureDecision("ドラフト");
check(Boolean(await page.$("#draft-pass")) && !(await page.$("#draft-advance")), "1 巡目の入札の間は、状態バーに「パス」(「自分の番まで」はない)");
const dr0 = await sameAsPy({ stage: "draft" }, "ドラフト(1 巡目の入札)");
check(JSON.stringify(dr0.heads) === JSON.stringify(["選手", "見立て", "年齢", "出身", "ポジション", "加入後の序列"]), `ドラフトの列は 名前・見立て・年齢・出身・ポジション・加入後の序列(評価と一言はない。D-418。列:${dr0.heads.join("・")})`);
check(!(await page.$("#proc-body select[aria-label='並び順']")) && (await page.inputValue("#draft-show")) === "open", "並べ替えのドロップダウンはなく、絞り込みの既定は「未指名だけ」");
const thinPy = pyAfter(`t = g.decision_table("draft")\nprint(json.dumps(sorted(t["thin"])))`);
const thinUi = await page.$$eval("#proc-body tbody .thin", (s) => s.length);
check(thinPy.length === 0 || thinUi > 0, `手薄なポジション(${thinPy.join("・") || "なし"})に ◆ の印`);
await page.click("#proc-body thead th button:has-text('年齢')");
await page.waitForFunction(() => document.querySelector("#proc-body thead th.sticky2") && document.querySelector("#proc-body thead th.sticky2").textContent.replace(/^[▲▼]/, "").startsWith("年齢"));
await sameAsPy({ stage: "draft", sort: "age", order: "asc" }, "ドラフトを年齢の見出しで並べ替え(年齢が名前の右に固定)");
await page.click("#proc-body thead th button:has-text('見立て')");
await page.waitForFunction(() => document.querySelector("#proc-body thead th.sticky2").textContent.replace(/^[▲▼]/, "").startsWith("見立て"));
await page.selectOption("#draft-group", "catcher");
await page.waitForFunction(() => [...document.querySelectorAll("#proc-body tbody tr:not(.detail)")].every((tr) => tr.textContent.includes("捕手")) && [...document.querySelectorAll("#proc-body thead th")].some((th) => th.textContent.includes("ミート")));
const drC = await sameAsPy({ stage: "draft", group: "catcher", sort: "ability", order: "desc" }, "ドラフト(捕手。見立ての能力の各項目)");
check(["見立て", "弾道", "ミート", "パワー", "走力", "肩力", "守備力", "捕球", "特殊能力"].every((h) => drC.heads.includes(h)), `ポジションの絞り込み 1 本で捕手だけになり、見立ての能力の各項目と特殊能力の列が出る(D-418。列:${drC.heads.join("・")})`);
await page.selectOption("#draft-group", "all");
await page.waitForFunction(() => document.querySelectorAll("#proc-body tbody tr:not(.detail)").length > 50);
await page.click("#proc-body .name-cell .link >> nth=0");
await page.waitForSelector("#draft-panel");
check(!(await page.textContent("#draft-panel")).includes("±") && (await page.textContent("#draft-panel")).includes("スカウトの見立て") && (await page.$$eval("#draft-panel .ability", (a) => a.length)) >= 4 && Boolean(await page.$("#draft-panel .skills")) && (await page.textContent("#draft-pick")) === "1 巡目に入札する", "名前を押すと、見立ての能力と特殊能力(ふれ幅はない)と「1 巡目に入札する」が出る");
const pk0 = Date.now();
let bids = 0;
for (;;) { // 外れたら、残りの候補から入札し直す(外れ 1 位。D-369)
  // 入札のパネルは、外れた後の描き直しで入れ替わることがある(広い画面)。描き直しが落ち着くまで待ち、押せなければ開き直す
  let pickId = null;
  for (let tries = 0; ; tries += 1) {
    await page.waitForTimeout(300);
    if (!(await page.$("#draft-panel"))) await page.click("#proc-body .name-cell .link >> nth=0");
    await page.waitForSelector("#draft-panel");
    pickId = await page.getAttribute("#draft-panel", "data-player-id");
    try {
      await page.click("#draft-pick", { timeout: 5000 });
      break;
    } catch (err) {
      if (tries >= 3) throw err;
    }
  }
  await page.waitForFunction(() => document.querySelector("#proc-message").textContent.startsWith("1 巡目の入札の結果") || document.querySelector("#proc-message").className.includes("ng"), null, { timeout: 60000 });
  procOps.push(["offseason_pick", [pickId]]);
  bids += 1;
  if ((await page.textContent("#proc-info")).startsWith("2/10 巡") || bids > 12) break;
  check((await page.textContent("#proc-message")).includes("外れたので"), `抽選に外れたら、もう一度入札する(${await page.textContent("#proc-message")})`);
  await page.click("#proc-body .name-cell .link >> nth=0");
  await page.waitForSelector("#draft-panel");
}
const lotPy = pyAfter(`print(json.dumps([[x["name"], x["winner_name"], len(x["teams"])] for x in g.offseason_view()["lottery"]], ensure_ascii=False))`);
await page.click("#draft-lottery summary");
const lotUi = await page.$$eval("#draft-lottery tbody tr", (trs) => trs.map((tr) => tr.querySelector("td").textContent));
check((await page.textContent("#proc-info")).startsWith("2/10 巡") && (await page.textContent("#proc-history")).includes("ドラフト 1 巡"), `入札(${bids} 回)と抽選で 1 巡目が決まり、次の自分の番(2 巡目)まで AI が進む(${((Date.now() - pk0) / 1000).toFixed(1)} 秒)。履歴に 1 巡目の指名が出る`);
const lotBids = lotPy.reduce((s, x) => s + x[2], 0);
check(JSON.stringify(lotUi) === JSON.stringify(lotPy.map((x) => x[0])) && lotBids >= 1, `1 巡目の入札の結果(${lotPy.length} 人・入札 ${lotBids} 件・抽選 ${lotPy.filter((x) => x[2] > 1).length} 回)が計算本体と同じ${JSON.stringify(lotUi) === JSON.stringify(lotPy.map((x) => x[0])) ? "" : `(画面 ${lotUi.length} 行)`}`);
await page.click("#draft-lottery summary");
await page.selectOption("#draft-show", "all");
await page.waitForFunction(() => document.querySelector("#proc-body thead").textContent.includes("状態"));
check((await page.textContent("#proc-body")).includes("指名("), "「指名済みも」で、指名された候補も(状態つきで)見られる");
await page.selectOption("#draft-show", "open");
await page.waitForFunction(() => !document.querySelector("#proc-body thead").textContent.includes("状態"));
await page.click("#proc-stage-auto");
await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "市場" && document.querySelector("#auto-log"), null, { timeout: 60000 });
procOps.push(["offseason_stage_auto", []]);
const autoLogPy = pyAfter(`print(json.dumps([r["auto_log"]["stage_label"], [x["text"] for x in r["auto_log"]["items"]]], ensure_ascii=False))`);
await page.click("#auto-log summary");
const autoLogUi = await page.$$eval("#auto-log li", (ls) => ls.map((l) => l.textContent));
check((await page.textContent("#auto-log summary")).includes(`おまかせの結果(${autoLogPy[0]})`) && JSON.stringify(autoLogUi) === JSON.stringify(autoLogPy[1]) && autoLogPy[1].length > 0 && autoLogPy[1].every((t) => t.startsWith("ドラフト") || t.startsWith("育成ドラフト")), `「この段階をおまかせ」(確認つき。D-284)でドラフトと育成ドラフトの残りを AI の方針で進め、市場の入口で止まる。AI が自球団の分として指名した選手の一覧(${autoLogPy[1].length} 人)が計算本体と同じ`);
await page.click("#auto-log summary");
// 市場(D-370):年俸は自動。獲得の希望を入れ、締めると前年の順位が低い球団から 1 人ずつ。表は 名前・WAR・総合・年齢・ポジション・加入後の序列・算定年俸・出場・前の所属・状態
await measureDecision("市場");
const mk0 = await sameAsPy({ stage: "market" }, "市場");
check(["WAR", "総合", "年齢", "ポジション", "加入後の序列", "算定年俸", "出場", "前の所属", "状態"].every((h, i) => mk0.heads[i + 1] === h), `市場の列(能力の総合が見られる。列:${mk0.heads.join("・")})`);
await page.selectOption("#market-group", "pitcher");
await page.waitForFunction(() => document.querySelector("#market-kind") && document.querySelector("#proc-body thead").textContent.includes("WAR(失点版)"));
const mkP = await sameAsPy({ stage: "market", group: "pitcher", kind: "war" }, "市場の投手(成績の種類の初期値は WAR。D-240)");
await page.selectOption("#market-group", "all");
await page.waitForFunction(() => !document.querySelector("#market-kind"));
await page.click("#proc-body thead th button:has-text('前の所属')");
await page.waitForFunction(() => document.querySelector("#proc-body thead th.sticky2").textContent.replace(/^[▲▼]/, "").startsWith("前の所属"));
const mkF = await decisionUi(400);
check(mkF.length > 0 && !mkF.some((r) => r["前の所属"] === "候補") && mkF.some((r) => r["WAR"] !== "—" && /^[SABCDEFG] \d+$/.test(r["総合"])), "指名されなかった候補は市場にいない(D-398)。手放された選手は能力の総合と成績が見られる");
const [mkName] = pyAfter(`t = g.decision_table("market")
print(json.dumps([min(t["rows"], key=lambda r: (r["deal"]["calc_salary"], r["player_id"]))["name"]], ensure_ascii=False))`); // 資金の残りで出せるよう、算定年俸の最も安い選手
await page.click(`#proc-body tbody tr:not(.detail) .name-cell .link:text-is("${mkName}")`);
await page.waitForSelector("#market-panel");
const mkPid = await page.getAttribute("#market-panel", "data-player-id");
check(Boolean(await page.$("#market-send")) && !(await page.$("#offer-salary")) && (await page.textContent("#market-panel")).includes("自動"), "市場のパネル:年俸は自動(入力欄はない)と「獲得を希望する」");
await page.click("#market-send");
await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("希望 1") || document.querySelector("#proc-message").className.includes("ng"), null, { timeout: 30000 });
check((await page.textContent("#proc-info")).includes("希望 1"), `獲得の希望が通る(${await page.textContent("#proc-message")}。${await page.textContent("#proc-info")})`);
procOps.push(["offseason_market_offer", [mkPid]]);
await page.click("#market-close");
await page.waitForFunction(() => document.querySelector("#proc-info").textContent.includes("終了"), null, { timeout: 60000 });
procOps.push(["offseason_market_close", []]);
const mkPy = pyAfter(`print(json.dumps([[x["name"], x["team_name"]] for x in r["last_market"]["signed"]] + [len(g.state.procedure.market_log)], ensure_ascii=False))`);
const mkMsg = await page.textContent("#proc-message");
check(mkMsg.startsWith(`市場の結果:契約 ${mkPy.length - 1} 人`) && (await page.textContent("#market-results summary")).includes(`契約 ${mkPy.length - 1} 人`), `「市場を締める」(状態バー)で、AI 球団の希望(全 ${mkPy[mkPy.length - 1]} 件)と合わせて順番に獲得し、結果(契約 ${mkPy.length - 1} 人)が計算本体と同じ`);
// 育成選手(D-375):市場の段階の下に自球団の育成選手の一覧(締めたあとは上げられない)
const devPy = pyAfter(`d = g.offseason_view()["development"]
print(json.dumps([d["count"], d["max"], [p["name"] for p in d["players"]]], ensure_ascii=False))`);
check((await page.textContent("#development-box summary")).startsWith(`育成選手(${devPy[0]}/${devPy[1]} 人`) && (await page.locator("#development-box button:has-text(\"支配下に上げる\")").count()) === 0, `市場の段階に自球団の育成選手(${devPy[0]} 人)が出る。市場を締めたあとは「支配下に上げる」はない(「${await page.textContent("#development-box summary").catch(() => "なし")}」)`);
// 現役ドラフト(D-380):市場 → トレード → 現役ドラフト。出す候補は AI のおすすめが入っていて、出すと指名が進む
await page.click("#proc-next");
await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "トレード", null, { timeout: 60000 });
procOps.push(["offseason_next", []]);
await page.click("#proc-next");
await page.waitForFunction(() => document.querySelector("#proc-title").textContent === "現役ドラフト" && document.querySelector("#active-offer"), null, { timeout: 60000 });
procOps.push(["offseason_next", []]);
const adPy = pyAfter(`a = g.offseason_view()["active_draft"]
print(json.dumps([[p["name"] for p in a["eligible"]], a["recommended"], [p["name"] for p in a["eligible"] if p["player_id"] in a["recommended"]]], ensure_ascii=False))`);
const adUi = await page.$$eval("#active-eligible tbody tr", (trs) => trs.map((tr) => [tr.querySelector("button.link").textContent, tr.querySelector("input").checked]));
check(adPy[1].length === 2 && JSON.stringify(adUi.map((x) => x[0])) === JSON.stringify(adPy[0]) && JSON.stringify(adUi.filter((x) => x[1]).map((x) => x[0])) === JSON.stringify(adPy[2]), `現役ドラフト:出せる選手 ${adPy[0].length} 人の表と、AI のおすすめ 2 人(${adPy[2].join("・")})に最初から印`);
await page.click("#active-offer");
await page.waitForFunction(() => document.querySelector("#active-picks") || document.querySelector("#active-choices") || document.querySelector("#proc-message").className.includes("ng"), null, { timeout: 60000 });
procOps.push(["active_draft_offer", [adPy[1]]]);
if (await page.$("#active-choices")) {
  const [adPick] = pyAfter(`print(json.dumps([g.offseason_view()["active_draft"]["recommended_pick"]]))`);
  await page.click("#active-choices tr.selected .active-pick");
  await page.waitForFunction(() => !document.querySelector("#active-choices") || document.querySelector("#proc-message").className.includes("ng"), null, { timeout: 60000 });
  procOps.push(["active_draft_pick", [adPick]]);
}
const adDone = pyAfter(`a = g.offseason_view()["active_draft"]
print(json.dumps([[x["name"] for x in a["picks"]], a["finished"], sum(1 for x in a["picks"] if x["team_id"] == g.state.my_team_id), sum(1 for x in a["picks"] if x["from_team"] == g.state.my_team_id)], ensure_ascii=False))`);
const adPicksUi = await page.$$eval("#active-picks li", (ls) => ls.length);
check(adDone[1] && adPicksUi === adDone[0].length && adDone[0].length >= 10 && adDone[2] === 1 && adDone[3] === 1 && (await page.textContent("#proc-info")).includes("終了"), `現役ドラフトの指名が最後まで進み、自球団は 1 人出て 1 人入る(指名 ${adDone[0].length} 人)`);
// 「全部おまかせ」はメニューの中(確認つき。D-271)
check(await page.isHidden("#proc-auto"), "手続きの画面には「全部おまかせ」はない(メニューの中)");
await page.click("#menu");
await page.waitForSelector("#proc-auto:not([hidden])");
check(await page.isVisible("#proc-auto"), "メニューに「全部おまかせ」がある(手続き中だけ)");
const au0 = Date.now();
await page.click("#proc-auto");
await page.waitForFunction(() => !document.querySelector("#screen-offseason").hidden && document.querySelectorAll("#offseason-retired tbody tr").length > 0, null, { timeout: 120000 });
check(true, `「おまかせ」で残りを自動で進め、オフの結果の画面になる(${((Date.now() - au0) / 1000).toFixed(1)} 秒)`);
const offPy = pyYear2(`print(json.dumps([summary["counts"]["retired"], summary["counts"]["rookies"], summary["counts"]["players"], [r["name"] for r in summary["retired"]], [r["name"] for r in summary["rookies"]]], ensure_ascii=False))`);
const retiredOnScreen = await page.$$eval("#offseason-retired tbody tr td .link", (as) => as.map((a) => a.textContent));
const rookiesOnScreen = await page.$$eval("#offseason-rookies tbody tr td .link", (as) => as.map((a) => a.textContent));
const countsText = await page.textContent("#offseason-counts");
check(JSON.stringify(retiredOnScreen) === JSON.stringify(offPy[3]) && JSON.stringify(rookiesOnScreen) === JSON.stringify(offPy[4]) && countsText.includes(`引退 ${offPy[0]}人`) && countsText.includes(`新人 ${offPy[1]}人`) && countsText.includes(`${offPy[2]}人`), `オフの結果(引退 ${offPy[0]}人・入団 ${offPy[1]}人・選手 ${offPy[2]}人)が、計算本体で同じ操作をした結果と同じ`);
const changesPy = pyYear2(`x = g.offseason_changes(None)\nprint(json.dumps([len(x["players"]), x["players"][0]["mean_change"]]))`);
const changeRows = await page.$$eval("#offseason-changes tbody tr", (trs) => trs.length);
check(changesPy[0] > 700 && changeRows > 0 && /^[+-]\d+\.\d$/.test(changesPy[1]), `オフの結果に、残った選手(${changesPy[0]}人)の能力の増減が出る(作り直し①。先頭 ${changesPy[1]})`);
// 入団した新人の選手ページに、能力が出る(プロのスカウト評価は見せない。D-352)
await page.click("#offseason-rookies tbody tr td .link >> nth=0");
await page.waitForFunction(() => !document.querySelector("#screen-player").hidden && document.querySelectorAll("#player-abilities .ability").length > 0);
check((await page.locator("#player-scouting-box").count()) === 0, "入団した選手のページに能力が出て、スカウト評価の箱はない");
await page.click("#screen-player .back");
check(await isDirty(), "年度を確定したあとは「未保存」になる");
await grab();
await page.click("#screen-offseason .back");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("2シーズン目 0日目"));
check(await page.isHidden("#year-end-box") && (await page.textContent("#progress-message")).includes("2シーズン目が始まりました"), `確定のあと、進行の画面は2シーズン目の0日目(「${await page.textContent("#progress-message")}」)`);
await page.click(".adv[data-days='1']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("2シーズン目 1日目"));
check(true, "2シーズン目を1日進められる");
// 記録室(D-422、D-423):進行の画面の「補強」で、補強の記録(ドラフト)を開く。計算本体と同じ行。ドラフト以外の入団は混ざらない
await page.click("#open-review");
await page.waitForFunction(() => !document.querySelector("#screen-records").hidden && document.querySelectorAll("#records-body tbody tr").length > 0);
const TEAM_SEL = '#records-controls select[aria-label="球団の選択"]';
const YEAR_SEL = '#records-controls select[aria-label="入団した年度の選択"]';
const reviewPy = pyYear2(`v = g.record_entries("draft")\nprint(json.dumps([v["team_name"], v["year"], v["years"], [[r["name"], r["route_label"], r["entry_text"], r["status_label"], r["war"]] for r in v["rows"]]], ensure_ascii=False))`);
const reviewRows = async () => page.$$eval("#records-body tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td").textContent, tr.children[1].textContent, tr.children[4].textContent, tr.children[6].textContent, tr.children[8].textContent]));
let reviewOnScreen = await reviewRows();
const reviewHead = await page.$$eval("#records-body thead th", (ths) => ths.map((th) => th.textContent));
check((await page.getAttribute("#tabs button[data-tab=records]", "aria-selected")) === "true" && (await page.getAttribute("#records-section button[data-value=entries]", "aria-pressed")) === "true" && (await page.getAttribute("#records-kind button[data-value=draft]", "aria-pressed")) === "true", "「補強」で、記録室(下のタブ「記録」)の補強の記録・ドラフトが開く");
check(JSON.stringify(reviewOnScreen) === JSON.stringify(reviewPy[3]) && (await page.inputValue(YEAR_SEL)) === String(reviewPy[1]) && (await page.textContent(`${TEAM_SEL} option:checked`)).startsWith(reviewPy[0]), `補強の記録(ドラフト):自球団(${reviewPy[0]})の最新の年度(${reviewPy[1]}シーズン目に入団)の ${reviewOnScreen.length} 人が、計算本体と同じ(経路・入団時の見立て・今の所属・WAR 累計)`);
check(reviewHead.length === 9 && !reviewHead.some((h) => h.includes("真の")), `補強の記録の列は 9 つ(${reviewHead.join("・")})`);
check(reviewOnScreen.every((r) => /^ドラフト \d+ 巡(\(抽選\))?$|^育成ドラフト \d+ 巡$/.test(r[1]) && /^([SA-G] \d+|—)$/.test(r[2])), "ドラフトの欄はドラフト・育成ドラフトの入団だけ(外国人・市場は混ざらない。D-422)、入団時の見立ては「ランク 数字」(D-420)");
await grab();
// 入団時の見立て(D-420):ドラフトで入った選手のページに、見立てと今の数字が並ぶ
const draftRow = await page.$$eval("#records-body tbody tr", (trs) => trs.findIndex((tr) => tr.children[1].textContent.startsWith("ドラフト") && tr.querySelector("td .link")));
if (draftRow >= 0) {
  await page.click(`#records-body tbody tr >> nth=${draftRow} >> td .link`);
  await page.waitForFunction(() => !document.querySelector("#screen-player").hidden && !document.querySelector("#player-entry-box").hidden, null, { timeout: 30000 });
  const entryHead = await page.textContent("#player-entry summary");
  await page.click("#player-entry summary");
  const entryCols = await page.$$eval("#player-entry thead th", (ths) => ths.map((th) => th.textContent));
  check(entryHead.startsWith("入団時の見立て") && entryHead.includes("ドラフト") && JSON.stringify(entryCols) === JSON.stringify(["能力", "見立て", "今の能力", "差"]), `ドラフトで入った選手のページに「${entryHead}」(見立て・今・差の表)`);
  await page.click("#screen-player .back");
  await page.waitForFunction(() => !document.querySelector("#screen-records").hidden);
} else check(false, "補強の記録にドラフトで入った選手がいない");
// 別の球団を選ぶと、その球団の入団者になる
await page.selectOption(TEAM_SEL, "T05");
const reviewT05Py = pyYear2(`v = g.record_entries("draft", "T05")\nprint(json.dumps([[r["name"], r["route_label"], r["entry_text"], r["status_label"], r["war"]] for r in v["rows"]], ensure_ascii=False))`);
await page.waitForFunction(([n, top]) => document.querySelectorAll("#records-body tbody tr").length === n && document.querySelector("#records-body tbody tr td").textContent === top, [reviewT05Py.length, reviewT05Py[0][0]], { timeout: 60000 });
reviewOnScreen = await reviewRows();
check(JSON.stringify(reviewOnScreen) === JSON.stringify(reviewT05Py), `球団を切り替えると、その球団の入団者(${reviewOnScreen.length} 人)が出る(観戦でも見られる形。計算本体と同じ)`);
// 外国人・出入り
await page.click("#records-kind button[data-value=foreign]");
const foEntPy = pyYear2(`v = g.record_entries("foreign", "T05")\nprint(json.dumps([[r["name"], r["route_label"]] for r in v["rows"]], ensure_ascii=False))`);
await page.waitForFunction((n) => document.querySelector("#records-kind button[data-value=foreign]").getAttribute("aria-pressed") === "true" && (n ? document.querySelectorAll("#records-body tbody tr").length === n && [...document.querySelectorAll("#records-body tbody tr")].every((tr) => tr.children[1].textContent === "外国人市場") : Boolean(document.querySelector("#records-body .info"))), foEntPy.length, { timeout: 30000 });
const foUi = await page.$$eval("#records-body tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td").textContent, tr.children[1].textContent]));
check(JSON.stringify(foUi) === JSON.stringify(foEntPy) && foUi.every((x) => x[1] === "外国人市場"), `外国人の欄は外国人市場の入団だけ(${foUi.length} 人。計算本体と同じ)`);
await page.click("#records-kind button[data-value=moves]");
await page.waitForFunction(() => document.querySelector("#records-kind button[data-value=moves]").getAttribute("aria-pressed") === "true" && document.querySelector('#records-controls select[aria-label="年の選択"]'));
await page.waitForFunction(() => document.querySelectorAll("#records-body tbody tr").length > 0, null, { timeout: 30000 });
const movesPy = pyYear2(`v = g.record_moves()\nprint(json.dumps([[r["name"], r["label"], r["from_team_name"], r["to_team_name"]] for r in v["rows"]], ensure_ascii=False))`);
const movesUi = await page.$$eval("#records-body tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td").textContent, tr.children[1].textContent, tr.children[2].textContent, tr.children[3].textContent]));
check(JSON.stringify(movesUi) === JSON.stringify(movesPy) && movesUi.some((r) => r[1] === "引退"), `出入り(最後に終えたオフの年):${movesUi.length} 件が計算本体と同じ(種類・前の所属・新しい所属。引退も含む)`);
// 歴代の順位と優勝・タイトル・通算記録
await page.click("#records-section button[data-value=standings]");
await page.waitForFunction(() => document.querySelectorAll("#records-body h2").length >= 3);
const stPy = pyYear2(`v = g.record_standings()\nprint(json.dumps([[r["team_name"] for r in lg["rows"]] for lg in v["leagues"]] + [[c["team_name"] + str(c["count"]) for c in v["champions"] if c["count"]]], ensure_ascii=False))`);
const stUi = await page.$$eval("#records-body .table-wrap table", (ts) => ts.map((t) => [...t.querySelectorAll("tbody tr")].map((tr) => tr.querySelector("td .link") ? tr.querySelector("td .link").textContent + (tr.children[1].textContent.endsWith("回") && tr.children[1].textContent !== "0 回" ? tr.children[1].textContent.replace(" 回", "") : "") : "")));
check(JSON.stringify(stUi.slice(0, 2)) === JSON.stringify(stPy.slice(0, 2)) && JSON.stringify(stUi[2].filter((x) => /\d$/.test(x))) === JSON.stringify(stPy[2]), `歴代の順位と優勝:1シーズン目の順位表と、球団ごとの優勝回数が計算本体と同じ`);
await page.click("#records-section button[data-value=titles]");
await page.waitForFunction(() => document.querySelectorAll("#records-body tbody tr").length === 20);
const tiPy = pyYear2(`v = g.record_titles()\nprint(json.dumps([[t["label"], t["value_text"]] for lg in v["leagues"] for t in lg["titles"]], ensure_ascii=False))`);
const tiUi = await page.$$eval("#records-body tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td").textContent, tr.children[2].textContent]));
check(JSON.stringify(tiUi) === JSON.stringify(tiPy), `年ごとのタイトル:2 リーグ × 11 のタイトル(盗塁王を含む)と記録が計算本体と同じ(${tiUi.slice(0, 2).map((x) => x.join(" ")).join("・")} …)`);
await page.click("#records-section button[data-value=leaders]");
await page.waitForFunction(() => document.querySelector('#records-controls select[aria-label="記録の選択"]') && document.querySelectorAll("#records-body tbody tr").length >= 20);
await page.selectOption('#records-controls select[aria-label="打者・投手の選択"]', "pitcher");
await page.waitForFunction(() => document.querySelector('#records-controls select[aria-label="記録の選択"] option:checked').textContent === "勝利");
const ldPy = pyYear2(`v = g.record_leaders("pitcher", "W")\nprint(json.dumps([[r["name"], r["value_text"]] for r in v["rows"]], ensure_ascii=False))`);
const ldUi = await page.$$eval("#records-body tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td .link").textContent, tr.children[1].textContent]));
check(JSON.stringify(ldUi) === JSON.stringify(ldPy), `通算記録(投手・勝利):上位 ${ldUi.length} 人が計算本体と同じ(1 位 ${ldUi[0] ? ldUi[0].join(" ") : ""})`);
await grab();
// 成績:シーズンの選択(今シーズン・1シーズン目・通算)
await page.click("#tabs button[data-tab=stats]");
await page.click("#stats-kind button[data-value=basic]");
await page.waitForFunction(() => document.querySelectorAll("#stats-table tbody tr").length > 0 || document.querySelector("#stats-table").textContent.includes("条件に合う選手"));
await page.$eval("#stats-filter-box", (d) => (d.open = true)); // 絞り込みを開く(D-401)
const seasonOptions = await page.$$eval("#stats-season option", (os) => os.map((o) => o.textContent));
check(seasonOptions.length === 3 && seasonOptions[1] === "1シーズン目" && seasonOptions[2] === "通算" && (await page.isVisible("#stats-season")), `成績の画面に、シーズンの選択(${seasonOptions.join("・")})が出る`);
const pyStats2 = (args) => pyYear2(`t = g.stats(**a)\nkeys = ([t["extra_column"]["key"]] if t["extra_column"] else []) + [c["key"] for c in t["columns"]]\nprint(json.dumps([[r["name"]] + [r["values"][k] for k in keys] for r in t["rows"]], ensure_ascii=False))`, JSON.stringify({ league: myStatsLeague, ...args }));
await page.selectOption("#stats-season", "1");
await waitRows(pyStats2({ role: "batter", kind: "basic", season: "1" }));
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats2({ role: "batter", kind: "basic", season: "1" })), `1シーズン目を選ぶと、確定した1シーズン目の成績が出る(${screenRows.length}人。計算本体と同じ)`);
await page.click("#stats-kind button[data-value=war]");
await waitRows(pyStats2({ role: "batter", kind: "war", season: "1" }));
check(await page.isHidden("#stats-baseline"), "確定したシーズンには、注意書きが出ない");
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats2({ role: "batter", kind: "war", season: "1" })), `1シーズン目の WAR も、確定した値(${screenRows.length}人)`);
await page.selectOption("#stats-season", "career");
await waitRows(pyStats2({ role: "batter", kind: "war", season: "career" }));
screenRows = await statsOnScreen();
check(JSON.stringify(screenRows) === JSON.stringify(pyStats2({ role: "batter", kind: "war", season: "career" })), `通算の WAR(各シーズンの合計)が、計算本体と同じ(${screenRows.length}人)`);
await page.click("#stats-kind button[data-value=saber]");
// 「基本」で使っていた並び順(打率)は、切り替えても保たれる(D-131)ので、名前の隣に打率の固定列が出る
await waitRows(pyStats2({ role: "batter", kind: "saber", season: "career", sort: "avg" }));
await waitStats("打率");
check((await page.textContent("#stats-baseline")) === "通算の指標は参考値(シーズンごとの値の加重平均)。", "通算のセイバーには「参考値」の 1 行が出る");
screenRows = await statsOnScreen();
const careerSaber = pyStats2({ role: "batter", kind: "saber", season: "career", sort: "avg" });
const careerDiff = screenRows.findIndex((r, i) => JSON.stringify(r) !== JSON.stringify(careerSaber[i]));
check(careerDiff < 0 && screenRows.length === careerSaber.length, `通算のセイバー(元の数の合計から今の基準値で計算。打率の並び順を保ったまま)が、計算本体と同じ(${screenRows.length}人)${careerDiff >= 0 ? `(違い: ${JSON.stringify(screenRows[careerDiff])} / ${JSON.stringify(careerSaber[careerDiff])})` : ""}`);
await page.selectOption("#stats-season", "current");
await page.waitForFunction(() => document.querySelector("#stats-season").value === "current" && document.querySelector("#stats-baseline").textContent === "シーズン途中の値(ここまで)。");
check(true, "2シーズン目の今シーズンには「シーズン途中の値(ここまで)」の 1 行が出る(球場補正の推定のことは用語集に)");
// 選手のページ:年度別の成績
await page.selectOption("#stats-season", "1");
await page.click("#stats-kind button[data-value=basic]");
await waitRows(pyStats2({ role: "batter", kind: "basic", season: "1" }));
await waitStats("打率");
await page.click("#stats-table tbody tr:first-child td .link");
await page.waitForFunction(() => !document.querySelector("#screen-player").hidden && document.querySelectorAll("#player-history tbody tr").length > 0);
const topId = pyYear2(`print(json.dumps(g.stats("batter", "basic", season="1", league=a["lg"])["rows"][0]["player_id"]))`, JSON.stringify({ lg: myStatsLeague }));
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
check(/\d\.\d{3}/.test(await page.textContent("#stadium-estimate")), "2シーズン目の球場のページに、前のシーズンの結果から推定した球場補正が出る");
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
// 一軍登録(D-425):2シーズン目に、救援投手を二軍の投手と入れ替える。外した選手は「あと 10 日」になり、上げた選手は一軍に入る
await page.click("#open-roster");
await page.waitForFunction(() => !document.querySelector("#screen-roster").hidden && document.querySelectorAll("#roster-body tbody tr").length > 29);
const relieverOut = await page.textContent("#roster-relievers tbody tr:first-child td .link");
await page.click("#roster-relievers tbody tr:first-child td:last-child button");
await page.waitForFunction(() => document.querySelector("#roster-message").textContent.includes("上げる"));
const upIndex = await page.$$eval("#roster-farm tbody tr", (trs) => trs.findIndex((tr) => { const b = tr.querySelector("td:last-child button"); return b && !b.disabled; }));
const pitcherUp = await page.textContent(`#roster-farm tbody tr:nth-child(${upIndex + 1}) td .link`);
await page.click(`#roster-farm tbody tr:nth-child(${upIndex + 1}) td:last-child button`);
await page.waitForFunction(() => document.querySelector("#roster-message").className.includes("ok") || document.querySelector("#roster-message").className.includes("ng"));
const relieversNow = await page.$$eval("#roster-relievers tbody td .link", (as) => as.map((a) => a.textContent));
const farmNow = await page.$$eval("#roster-farm tbody tr", (trs) => trs.map((tr) => [tr.querySelector("td .link").textContent, tr.querySelector("td:last-child").textContent]));
check(relieversNow.includes(pitcherUp) && !relieversNow.includes(relieverOut) && farmNow.some((r) => r[0] === relieverOut && r[1].includes("あと 10 日")) && (await page.textContent("#roster-recent")).includes(pitcherUp), `${relieverOut} を下げて ${pitcherUp} を上げると入れ替わり、下げた選手は「あと 10 日」(「${await page.textContent("#roster-message")}」)`);
await page.click("#roster-auto");
await page.waitForFunction(() => document.querySelector("#roster-message").textContent.includes("やめました"));
check(!(await page.isChecked("#roster-auto")) && (await page.textContent("#roster-info")).includes("変わりません"), "「AI に任せる」を外すと、一軍は手で入れ替えるまで変わらない");
await page.click("#screen-roster .back");
// 1シーズン目の終わりのセーブデータに戻して、以降の確認を続ける
await page.reload();
await page.waitForSelector("#go-new:not([disabled])", { timeout: 300000 });
await page.setInputFiles("#open-file-start", last.path);
await page.waitForSelector("#screen-progress:not([hidden])");

// 用語集のページ(UI の整理②。D-307〜D-311):メニューの下の方から開く。分類と検索。項目は計算本体の用語集のデータと同じ
await page.click("#menu");
const menuOrder = await page.$$eval("#screen-settings h2", (hs) => hs.filter((h) => h.offsetParent).map((h) => h.textContent));
check(menuOrder.at(-1) === "用語集" && (await page.isVisible("#open-guide")), `メニューの下の方に「用語集」がある(見出しの順:${menuOrder.join("・")})`);
await findBlocks();
await page.click("#open-guide");
await page.waitForSelector("#guide-body .guide-item");
const glossPy = JSON.parse(python(`
import json
from pennant.api import glossary_view
v = glossary_view()
print(json.dumps([[c["label"] for c in v["categories"]], [[t["name"], t["meaning"], t.get("formula", ""), t.get("better", "")] for c in v["categories"] for t in v["terms"] if t["category"] == c["key"]]], ensure_ascii=False))`));
const glossOnScreen = await page.$$eval("#guide-body .guide-item", (items) => items.map((i) => [i.dataset.name, i.querySelector(".description").textContent, (i.querySelector(".formula")?.textContent || "").replace(/^式:/, ""), i.querySelector(".better")?.textContent || ""]));
check(JSON.stringify(glossOnScreen) === JSON.stringify(glossPy[1]), `用語集の項目(${glossOnScreen.length} 個:名前・意味・式・良い向き)が、用語集のデータと同じ`);
const catOptions = await page.$$eval("#guide-category option", (os) => os.map((o) => o.textContent));
check(JSON.stringify(catOptions.slice(1)) === JSON.stringify(glossPy[0]) && JSON.stringify(await page.$$eval("#guide-body h2", (hs) => hs.map((h) => h.textContent))) === JSON.stringify(glossPy[0]), `分類(${glossPy[0].join("・")})で分けて並ぶ`);
check((await page.textContent("#guide-body")).includes("式:(四球の重み × 四球"), "指標の式が日本語で出る(wOBA)");
const glossNames = async () => page.$$eval("#guide-body .guide-item", (items) => items.map((i) => i.dataset.name));
await page.fill("#guide-search", "wRC");
await page.waitForFunction(() => document.querySelectorAll("#guide-body .guide-item").length < 10);
const hitWrc = await glossNames();
await page.fill("#guide-search", "年俸");
await page.waitForFunction(() => [...document.querySelectorAll("#guide-body .guide-item")].some((i) => i.dataset.name === "算定年俸"));
const hitSalary = await glossNames();
check(hitWrc.includes("wRC+") && hitSalary.includes("年俸") && hitSalary.includes("算定年俸") && hitSalary.length < glossPy[1].length, `検索は、略語(wRC → ${hitWrc.join("・")})でも日本語(年俸 → ${hitSalary.length} 項目)でも見つかる`);
await page.fill("#guide-search", "存在しない言葉です");
await page.waitForSelector("#guide-empty");
await page.fill("#guide-search", "");
await page.selectOption("#guide-category", "money");
await page.waitForFunction(() => document.querySelectorAll("#guide-body h2").length === 1);
const moneyHead = await page.$$eval("#guide-body h2", (hs) => hs.map((h) => h.textContent));
const moneyNames = await glossNames();
check(moneyHead.join() === "契約とお金" && moneyNames.includes("資金") && moneyNames.includes("査定") && moneyNames.includes("FA 権") && !moneyNames.some((n) => n.startsWith("お金のルール")), `分類で絞り込める(${moneyHead.join()}:${moneyNames.length} 項目。ゲームのルールも項目にある)`);
await page.selectOption("#guide-category", "all");
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
// リーグ名・球場名の入力(D-409)と、自球団の資金力(D-410)。重なったリーグ名は理由を出して止める
const lg0 = await page.getAttribute("#league-name-0", "placeholder");
await page.fill("#league-name-1", lg0);
await page.waitForFunction(() => document.querySelector("#league-name-1-error").textContent.includes("同じ名前"));
check(true, "リーグ名が重なると、欄の下に理由が出る(D-409)");
await page.fill("#league-name-1", "");
await page.fill("#league-name-0", "テスト連盟");
await page.fill("#stadium-name-0", "テスト球場");
await page.check("input[name=my-team][value='0']");
await page.check("input[name=my-tier][value=huge]");
const fastStart = Date.now();
await page.click("#new-start");
await page.waitForSelector("#screen-progress:not([hidden])");
const fastSeconds = (Date.now() - fastStart) / 1000;
await page.click("#tabs button[data-tab=standings]");
check((await page.textContent("#league-switch button:first-child")) === "テスト連盟", "入力したリーグ名が順位表に出る(D-409)");
await page.click("#tabs button[data-tab=myteam]");
await page.waitForFunction(() => document.querySelector("#team-info").textContent.includes("テスト球場") && document.querySelector("#team-budget").textContent.includes("超大量"));
check(true, "入力した球場名と、選んだ資金力(超大量)が自球団のページに出る(D-409、D-410)");
await page.click("#tabs button[data-tab=progress]");
await page.click(".adv[data-days='1']");
await page.waitForFunction(() => document.querySelector("#day-text").textContent.startsWith("1シーズン目 1日目"));
await page.click("#tabs button[data-tab=stats]");
await page.click("#stats-kind button[data-value=saber]");
await page.waitForFunction(() => document.querySelector("#stats-baseline").textContent === "シーズン途中の値(ここまで)。");
check(fastSeconds < trialSeconds, `「既定値を使う(速い)」なら、すぐ始まる(${fastSeconds.toFixed(1)} 秒。事前運転 25 年を含む。試運転ありは ${trialSeconds.toFixed(1)} 秒)`);
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
check(blockFindings.length === 0 && seenScreens.size >= 12, `見た画面(${seenScreens.size} 個:${[...seenScreens].map((x) => x.replace("screen-", "")).join("・")})に、説明ブロックがない(1 行の注意書きだけ。D-306、D-310)${blockFindings.length ? `(残っている: ${[...new Set(blockFindings)].join(" / ")})` : ""}`);
await browser.close();

console.log(`# ブラウザでの通しの確認(${WIDTH}×${HEIGHT})`);
console.log(results.join("\n"));
console.log(`\n## 通信した先(${paths.length} 件)`);
console.log(paths.map((p) => `- ${p}`).join("\n"));
console.log(`\n${failed ? `× ${failed} 件の問題があります` : "○ すべて確認できました"}`);
finished = true;
process.exit(failed ? 1 : 0);
