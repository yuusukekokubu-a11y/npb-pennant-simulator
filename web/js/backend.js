// 裏の Python(worker.js)とのやり取り・見る画面の問い合わせの使い回し・待ち時間の表示(web/app.js から分けた。保守②。D-291)

import { $, LOCAL_PYODIDE, WAIT_SHOW_MS, state } from "./core.js";
import { prerunProgress, trialProgress } from "./newgame.js";
import { applyStaticTitles, setGlossary } from "./glossary.js";

let worker = null;
export let ready = false;
let nextId = 1;
const pending = new Map();

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
      if (msg.prerun) {
        prerunProgress(msg.prerun);
        return;
      }
      if (msg.trial) {
        trialProgress(msg.trial);
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

export function call(cmd, args) {
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

export async function boot() {
  try {
    worker = await createWorker();
    await call("boot", { local: LOCAL_PYODIDE });
    ready = true;
    const g = await call("query", { name: "glossary", args: {} }); // 用語集(説明の文の 1 か所。D-311)
    if (!g.ok) throw new Error(g.message);
    setGlossary(g.value);
    applyStaticTitles();
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

// ---- 見る画面の問い合わせ(結果は、日が進むまで使い回す。D-114) ----

function stamp() {
  return state.view ? `${state.view.status.year}:${state.view.status.games_played}` : "";
}

async function cached(kind, name, args) {
  if (state.cacheStamp !== stamp()) {
    state.cache.clear();
    state.cacheStamp = stamp();
  }
  const key = `${kind}|${name}|${JSON.stringify(args)}`;
  if (state.cache.has(key)) return state.cache.get(key);
  const r = await call(kind, { name, args });
  if (!r.ok) throw new Error(r.message);
  state.cache.set(key, r.value);
  return r.value;
}

export const query = (name, args = {}) => cached("query", name, args);

export function answer(name, args = {}) {
  // 答え合わせモードがオフのときは、答え合わせ用の入口を呼ばない(D-108)
  if (state.answerLevel === 0) return Promise.reject(new Error("答え合わせモードがオフです"));
  return cached("answer", name, { ...args, level: state.answerLevel });
}

export function clearAnswers() {
  for (const key of [...state.cache.keys()]) if (key.startsWith("answer|")) state.cache.delete(key);
}

// 待ち時間の表示:0.6 秒を超えたら出し、出したら最低 0.6 秒は続ける(D-118)
export function delayedShow(show, hide) {
  let shownAt = null;
  const timer = setTimeout(() => {
    shownAt = performance.now();
    show();
  }, WAIT_SHOW_MS);
  return async () => {
    clearTimeout(timer);
    if (shownAt === null) return;
    const rest = shownAt + WAIT_SHOW_MS - performance.now();
    if (rest > 0) await new Promise((r) => setTimeout(r, rest));
    hide();
  };
}

export function withLoading(id, promise) {
  const done = delayedShow(() => ($(id).hidden = false), () => ($(id).hidden = true));
  return promise.finally(done);
}
