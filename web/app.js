// 画面の動き。計算は worker.js(裏の Python)に任せ、ここは表示だけを行う。
// ブラウザの保存領域(localStorage・sessionStorage・IndexedDB・Cookie)には何も書かない。

const DAY_GAMES = 12; // 「1日分」として測る試合数(本物の日程の2日分。1日は6試合)
const SEED = 1;
const LOCAL_PYODIDE = new URLSearchParams(location.search).get("pyodide") === "local"; // 試験用(このページに置いた Pyodide を使う)

const $ = (id) => document.getElementById(id);
let worker = null;
let nextId = 1;
const pending = new Map();
let workerResources = [];
let exportedDigest = null;

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
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      msg.ok ? p.resolve(msg.value) : p.reject(new Error(msg.error));
    };
    w.onerror = (e) => reject(new Error(e.message || "Worker を起動できませんでした"));
  });
}

function call(w, cmd, args) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    w.postMessage({ id, cmd, args });
  });
}

async function bootWorker() {
  const w = await createWorker();
  const info = await call(w, "boot", { local: LOCAL_PYODIDE });
  return { w, info };
}

async function ensureWorker() {
  if (worker) return worker;
  status("Python を起動しています…");
  const { w } = await bootWorker();
  worker = w;
  await call(worker, "setup", { seed: SEED });
  await call(worker, "games", { n: 1 });
  status("");
  return worker;
}

// ---- 表示 ----

function status(text, cls = "") {
  const el = $("status");
  el.textContent = text;
  el.className = "status " + cls;
}
const sec = (s) => `${s.toFixed(2)} 秒`;
const mb = (b) => (b == null ? "測れません" : `${(b / 1024 / 1024).toFixed(1)} MB`);
const kb = (b) => `${(b / 1024).toFixed(1)} KB`;
const yieldToScreen = () => new Promise((r) => setTimeout(r, 0));

function deviceInfo() {
  const n = navigator;
  const lines = [`ブラウザの情報: ${n.userAgent}`];
  if (n.userAgentData) {
    const brands = n.userAgentData.brands.map((b) => `${b.brand} ${b.version}`).join(", ");
    lines.push(`ブラウザ: ${brands} / OS: ${n.userAgentData.platform} / モバイル: ${n.userAgentData.mobile ? "はい" : "いいえ"}`);
  }
  lines.push(`CPU の数(論理): ${n.hardwareConcurrency ?? "不明"} / 端末のメモリ(目安): ${n.deviceMemory ? n.deviceMemory + " GB" : "不明"}`);
  lines.push(`画面: ${screen.width}×${screen.height}(拡大率 ${devicePixelRatio})`);
  return lines;
}

async function storageState() {
  let idb = "確認できません";
  if (indexedDB && indexedDB.databases) {
    try {
      idb = `${(await indexedDB.databases()).length} 個`;
    } catch (e) {
      idb = "確認できません";
    }
  }
  let local = "確認できません";
  let session = "確認できません";
  try { local = `${localStorage.length} 件`; } catch (e) { /* 使えない環境 */ }
  try { session = `${sessionStorage.length} 件`; } catch (e) { /* 使えない環境 */ }
  return [
    `localStorage: ${local} / sessionStorage: ${session} / IndexedDB: ${idb} / Cookie: ${document.cookie ? "あり" : "なし"}`,
  ];
}

async function networkState() {
  if (worker) {
    try { workerResources = await call(worker, "resources"); } catch (e) { /* 起動前 */ }
  }
  const main = performance.getEntriesByType("resource").map((e) => ({ url: e.name.split("?")[0], transferSize: e.transferSize }));
  const all = [{ url: location.origin + location.pathname, transferSize: null }, ...main, ...workerResources];
  const seen = new Map();
  for (const r of all) if (!seen.has(r.url)) seen.set(r.url, r);
  const hosts = [...new Set([...seen.keys()].map((u) => new URL(u).host))];
  const lines = [`通信した先(ホスト): ${hosts.join(", ")}`];
  for (const r of seen.values()) {
    const cached = r.transferSize === 0 ? " (キャッシュから)" : ""; // URL のあとに空白を入れる(GitHub で URL の一部と見なされないように)
    lines.push(`  - ${r.url}${cached}`);
  }
  return lines;
}

// ---- 1. 測定 ----

async function measure() {
  $("measure").disabled = true;
  $("copy").disabled = true;
  $("result").value = "";
  const out = ["# ブラウザでの実行の技術検証:測定の結果", `測定した日時: ${new Date().toISOString()}`, ""];
  const progress = $("progress");
  try {
    // Python の起動(1回目と2回目)
    status("Python を起動しています(1回目)…");
    if (worker) { worker.terminate(); worker = null; }
    const first = await bootWorker();
    const firstRes = await call(first.w, "resources");
    first.w.terminate();
    status("Python を起動しています(2回目)…");
    const second = await bootWorker();
    worker = second.w;
    const wasm = firstRes.find((r) => r.url.endsWith("pyodide.asm.wasm"));
    const cold = wasm && wasm.transferSize > 0;
    out.push("## Python の起動");
    out.push(`- Python ${second.info.pythonVersion}(Pyodide ${second.info.pyodideVersion}。配布元: ${second.info.pyodideSource})`);
    out.push(`- 1回目: ${sec(first.info.seconds)}(Pyodide ${sec(first.info.pyodideSeconds)} + 計算本体の読み込み ${sec(first.info.codeSeconds)})${cold ? "。配布元からダウンロードした" : "。ブラウザのキャッシュから読んだ"}`);
    out.push(`- 2回目: ${sec(second.info.seconds)}(Pyodide ${sec(second.info.pyodideSeconds)} + 計算本体の読み込み ${sec(second.info.codeSeconds)})`);

    // 結果の指紋(PC の python scripts/fingerprint.py と見比べる。D-089)
    status("結果の指紋を計算しています…");
    const fp = await call(worker, "fingerprint");
    out.push("", fp.text, `- 指紋の計算にかかった時間: ${sec(fp.seconds)}`);

    // リーグの生成と日程の作成
    status("架空のリーグと日程を作っています…");
    const setup = await call(worker, "setup", { seed: SEED });
    const seasonGames = setup.totalGames;
    out.push("", "## 計算");
    out.push(`- 架空のリーグの生成(12球団・840人)と日程の作成: ${sec(setup.seconds)}`);

    // 1シーズン(本物の日程。1日ずつ進め、合間に画面を更新する)
    progress.max = seasonGames;
    progress.value = 0;
    progress.hidden = false;
    let total = 0;
    const daySeconds = [];
    const t0 = performance.now();
    let over = false;
    while (!over) {
      const r = await call(worker, "day");
      total = r.totalGames;
      over = r.isOver;
      daySeconds.push(r.seconds);
      progress.value = total;
      status(`1シーズンを進めています… ${total} / ${seasonGames} 試合(${daySeconds.length}日目)`);
      await yieldToScreen();
    }
    const seasonSeconds = (performance.now() - t0) / 1000;
    const avgDay = daySeconds.reduce((a, b) => a + b, 0) / daySeconds.length;
    const maxDay = Math.max(...daySeconds);
    const twoDays = daySeconds[1] + daySeconds[2]; // 2日目と3日目(12試合)。1日目は準備の時間を含むので外す
    out.push(`- 最初の1日(6試合。準備の時間を含む): ${sec(daySeconds[0])}`);
    out.push(`- 1日分(12試合。本物の日程の2日分): ${sec(twoDays)}(1試合あたり ${(twoDays / DAY_GAMES * 1000).toFixed(0)} ミリ秒)`);
    out.push(`- 1シーズン(本物の日程。${total}試合・${daySeconds.length}日): ${sec(seasonSeconds)}(1日=6試合あたり 平均 ${sec(avgDay)}、最大 ${sec(maxDay)})`);
    const day = { seconds: twoDays };

    // 打席ログの大きさ
    status("打席ログの大きさを測っています…");
    const stats = await call(worker, "stats");
    out.push("", "## メモリ");
    out.push(`- 打席の数: ${stats.plateAppearances.toLocaleString()}(${total}試合分を保持)`);
    out.push(`- 打席ログのメモリ上の大きさ(Python の見積もり): ${mb(stats.logMemoryBytes)}(測る時間 ${sec(stats.measureSeconds)})`);
    out.push(`- Python 全体が使っているメモリ(WebAssembly のメモリ): ${mb(stats.wasmHeapBytes)}`);
    if (performance.memory) out.push(`- 画面側の JavaScript のメモリ: ${mb(performance.memory.usedJSHeapSize)}`);

    status("打席ログを圧縮しています…");
    const exp = await call(worker, "exportSeason");
    out.push("", "## 書き出し");
    out.push(`- 打席ログを圧縮したファイル(gzip の JSON Lines): ${mb(exp.bytes)}(${kb(exp.bytes)}。作る時間 ${sec(exp.seconds)})`);

    // 判定(目安:1日分が約1秒以内、1シーズンが数分以内、メモリ不足で止まらない)
    out.push("", "## 判定(目安)");
    out.push(`- 1日分(12試合)が約1秒以内: ${day.seconds <= 1 ? "○" : "×"}(${sec(day.seconds)})`);
    out.push(`- 1シーズンが数分(5分)以内: ${seasonSeconds <= 300 ? "○" : "×"}(${sec(seasonSeconds)})`);
    out.push("- メモリ不足で止まらない: ○(最後まで完了)");

    out.push("", "## 端末", ...deviceInfo());
    out.push("", "## 通信と保存", ...(await networkState()), ...(await storageState()));
    status("測定が終わりました。", "ok");
  } catch (err) {
    out.push("", `## エラーで止まりました`, String(err && err.message ? err.message : err));
    out.push("", "## 端末", ...deviceInfo());
    status("エラーで止まりました。結果の欄を見てください。", "ng");
  } finally {
    progress.hidden = true;
    $("result").value = out.join("\n");
    $("measure").disabled = false;
    $("copy").disabled = false;
  }
}

async function copyResult() {
  const text = $("result").value;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    $("result").select();
    document.execCommand("copy");
  }
  $("copied").textContent = "コピーしました";
  setTimeout(() => ($("copied").textContent = ""), 2000);
}

// ---- 2. ファイルの往復 ----

async function exportGame() {
  try {
    const w = await ensureWorker();
    const { bytes, digest } = await call(w, "exportGame");
    exportedDigest = digest;
    const blob = new Blob([bytes], { type: "application/gzip" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "pennant-game-log.jsonl.gz";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    $("file-result").textContent = `書き出しました(${kb(bytes.length)})。次に「ファイルを選んで読み込む」で、このファイルを選んでください。`;
  } catch (err) {
    $("file-result").textContent = `書き出せませんでした:${err.message}`;
  }
}

async function importFile(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  try {
    const w = await ensureWorker();
    const bytes = new Uint8Array(await file.arrayBuffer());
    const info = await call(w, "importFile", { bytes });
    const same = exportedDigest ? (info.sha256 === exportedDigest ? "○ 書き出した中身と一致" : "× 書き出した中身と違う") : "(このページで書き出したファイルではないため、比べていません)";
    $("file-result").textContent = `読み込めました:${info.games} 試合、${info.plate_appearances} 打席、得点の合計 ${info.runs}\n一致の確認:${same}`;
  } catch (err) {
    $("file-result").textContent = `読み込めませんでした:${err.message}`;
  }
}

// ---- 3. テスト用の球団名 ----

async function header() {
  try {
    const w = await ensureWorker();
    $("header-result").textContent = await call(w, "header", { name: $("team-name").value });
  } catch (err) {
    $("header-result").textContent = `表示できませんでした:${err.message}`;
  }
}

// ---- 4. 通信と保存 ----

async function check() {
  $("check-result").textContent = [...(await networkState()), ...(await storageState())].join("\n");
}

$("measure").addEventListener("click", measure);
$("copy").addEventListener("click", copyResult);
$("export").addEventListener("click", exportGame);
$("file").addEventListener("change", importFile);
$("header").addEventListener("click", header);
$("check").addEventListener("click", check);
