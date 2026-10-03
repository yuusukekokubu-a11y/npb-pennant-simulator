// 裏で Python(Pyodide)を動かす部分(Web Worker)。画面(app.js)からの依頼を受けて、
// 操作の関数(pennant.api)を bridge.py 経由で呼び、結果を返す。計算を裏で行うので、計算中も画面は固まらない。
// 通信するのは、このページ自身(pennant.zip・bridge.py)と、Pyodide の配布元だけ。
// ブラウザの保存領域(localStorage・IndexedDB など)は使わない。

const PYODIDE_VERSION = "314.0.7";
const PYODIDE_CDN = `https://cdn.jsdelivr.net/npm/pyodide@${PYODIDE_VERSION}/`;

let pyodide = null;
let bridge = null;

function progress(step, text) {
  self.postMessage({ progress: { step, total: 3, text } });
}

function parse(text) {
  // bridge.py は {"ok": true, "value": …} か {"ok": false, "message": …, "problems": […]} の文字を返す
  return JSON.parse(text);
}

const handlers = {
  async boot({ local }) {
    const base = local ? new URL("pyodide/", import.meta.url).href : PYODIDE_CDN;
    progress(1, "Python 一式をダウンロードしています(初回は約12MB)…");
    const { loadPyodide } = await import(base + "pyodide.mjs");
    pyodide = await loadPyodide({ indexURL: base, stdout: () => {}, stderr: () => {} });
    progress(2, "シミュレーションの本体を読み込んでいます…");
    const zip = await (await fetch(new URL("pennant.zip", import.meta.url))).arrayBuffer();
    pyodide.unpackArchive(zip, "zip", { extractDir: "/home/pyodide" });
    const src = await (await fetch(new URL("bridge.py", import.meta.url))).text();
    pyodide.FS.writeFile("/home/pyodide/bridge.py", src);
    progress(3, "準備しています…");
    pyodide.runPython("import sys\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
    bridge = pyodide.pyimport("bridge");
    return { ok: true, value: null };
  },

  preview({ seed }) {
    return parse(bridge.preview(seed));
  },

  check({ seed, names }) {
    return parse(bridge.check(seed, JSON.stringify(names)));
  },

  newGame({ seed, seasonSeed, names, myTeamIndex, baselines, scoutLevel }) {
    // 試運転の進み具合は、計算の途中でも画面に知らせる(D-121)
    const progress = (day, total) => self.postMessage({ trial: { day, total } });
    const prerun = (year, total) => self.postMessage({ prerun: { year, total } });
    return parse(bridge.new_game(seed, seasonSeed, JSON.stringify(names), myTeamIndex, baselines || "trial", progress, prerun, scoutLevel || "medium"));
  },

  // オフの手続き(F3-1):自由契約・ドラフト・市場の操作
  offseason({ name, args }) {
    return parse(bridge.offseason(name, JSON.stringify(args || {})));
  },

  load({ bytes }) {
    const py = pyodide.toPy(bytes);
    try {
      return parse(bridge.load(py));
    } finally {
      py.destroy();
    }
  },

  save({ today }) {
    const data = bridge.save(today);
    const bytes = data.toJs();
    data.destroy();
    const info = parse(bridge.save_info());
    info.value.bytes = bytes;
    return info;
  },

  advance({ days }) {
    return parse(bridge.advance(days));
  },

  view() {
    return parse(bridge.view());
  },

  // 年度の確定(画面で確認したあとに呼ぶ)
  yearEnd() {
    return parse(bridge.year_end());
  },

  // 見る画面(公開用の情報だけ)
  query({ name, args }) {
    return parse(bridge.query(name, JSON.stringify(args || {})));
  },

  // 答え合わせ(答え合わせモードがオンのときだけ、画面が呼ぶ)
  answer({ name, args }) {
    return parse(bridge.answer(name, JSON.stringify(args || {})));
  },
};

self.onmessage = async (event) => {
  const { id, cmd, args } = event.data;
  try {
    const value = await handlers[cmd](args || {});
    self.postMessage({ id, ok: true, value });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String((err && err.message) || err) });
  }
};
self.postMessage({ ready: true });
