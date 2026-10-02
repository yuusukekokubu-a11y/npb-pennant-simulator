// 裏で Python(Pyodide)を動かす部分(Web Worker)。画面(app.js)からの依頼を受けて計算し、結果を返す。
// 計算を裏で行うので、計算中も画面は固まらない。
// 通信するのは、このページ自身(pennant.zip・bench.py)と、Pyodide の配布元だけ。
// ブラウザの保存領域(localStorage・IndexedDB など)は使わない。

const PYODIDE_VERSION = "314.0.7";
const PYODIDE_CDN = `https://cdn.jsdelivr.net/npm/pyodide@${PYODIDE_VERSION}/`;

let pyodide = null;
let benchModule = null;
let bench = null;

function toJs(value) {
  // Python の値を JavaScript の値にする(使い終わった Python 側の参照は解放する)
  if (value && typeof value.toJs === "function") {
    const js = value.toJs({ dict_converter: Object.fromEntries });
    value.destroy();
    return js;
  }
  return value;
}

function wasmHeapBytes() {
  try {
    return pyodide._module.HEAP8.buffer.byteLength;
  } catch (e) {
    return null;
  }
}

const handlers = {
  async boot({ local }) {
    const base = local ? new URL("pyodide/", import.meta.url).href : PYODIDE_CDN;
    const t0 = performance.now();
    const { loadPyodide } = await import(base + "pyodide.mjs");
    pyodide = await loadPyodide({ indexURL: base, stdout: () => {}, stderr: () => {} });
    const t1 = performance.now();
    const zip = await (await fetch(new URL("pennant.zip", import.meta.url))).arrayBuffer();
    pyodide.unpackArchive(zip, "zip", { extractDir: "/home/pyodide" });
    const src = await (await fetch(new URL("bench.py", import.meta.url))).text();
    pyodide.FS.writeFile("/home/pyodide/bench.py", src);
    pyodide.runPython("import sys\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
    benchModule = pyodide.pyimport("bench");
    const t2 = performance.now();
    return {
      seconds: (t2 - t0) / 1000,
      pyodideSeconds: (t1 - t0) / 1000,
      codeSeconds: (t2 - t1) / 1000,
      pythonVersion: benchModule.python_version(),
      pyodideVersion: pyodide.version,
      pyodideSource: local ? "このページ(試験用)" : "cdn.jsdelivr.net",
    };
  },

  setup({ seed }) {
    if (bench) bench.destroy();
    bench = benchModule.Bench(seed);
    return { seconds: bench.setup_seconds };
  },

  games({ n }) {
    return { seconds: bench.play_games(n), totalGames: bench.games_played() };
  },

  day() {
    const t0 = performance.now();
    const played = bench.play_day();
    return { played, seconds: (performance.now() - t0) / 1000, totalGames: bench.games_played() };
  },

  stats() {
    const t0 = performance.now();
    const logBytes = bench.log_memory_bytes();
    return {
      plateAppearances: bench.plate_appearances(),
      logMemoryBytes: logBytes,
      measureSeconds: (performance.now() - t0) / 1000,
      wasmHeapBytes: wasmHeapBytes(),
    };
  },

  exportSeason() {
    const t0 = performance.now();
    const data = bench.export_log();
    const size = data.length;
    data.destroy();
    return { bytes: size, seconds: (performance.now() - t0) / 1000 };
  },

  exportGame() {
    // 1試合目の打席ログを、ファイルの中身(gzip 圧縮の JSON Lines)にする
    const data = bench.export_first_game();
    const bytes = data.toJs();
    const digest = benchModule.log_digest(data);
    data.destroy();
    return { bytes, digest };
  },

  importFile({ bytes }) {
    const info = benchModule.decode_log(pyodide.toPy(bytes));
    return toJs(info);
  },

  header({ name }) {
    // テスト用の球団名は、この呼び出しの中だけで使う(Python 側にも残さない)
    return bench.sample_header(name);
  },

  fingerprint() {
    return toJs(benchModule.fingerprint_report());
  },

  resources() {
    return performance.getEntriesByType("resource").map((e) => ({
      url: e.name.split("?")[0],
      transferSize: e.transferSize,
    }));
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
