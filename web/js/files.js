// 保存と読み込み(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { call, ready } from "./backend.js";
import { current, renderCurrent, showTab } from "./screens.js";
import { enterGame, renderProgress, setDirty } from "./progress.js";

// ---- 保存と読み込み ----

function todayText() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export async function save() {
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
    if (current().name === "yearend") renderCurrent(); // 確認の画面からの保存は、その画面に留まる(未保存の注意を更新)
    else if (current().name !== "progress") showTab("progress");
  } catch (err) {
    $("progress-message").className = "message ng";
    $("progress-message").textContent = `保存できませんでした:${err.message}`;
  } finally {
    renderProgress();
  }
}

export async function openFile(event, messageId) {
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
      if (state.view) showTab("progress"); // 上の「開く」から開いたときは、進行の画面に説明を出す
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
