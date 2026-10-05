// 共通の土台:定数・画面の状態(state)・要素の取り出し($)・広い画面の印(web/app.js から分けた。保守②。D-291)

export const LOCAL_PYODIDE = new URLSearchParams(location.search).get("pyodide") === "local"; // 試験用(このページに置いた Pyodide を使う)
export const MAX_SEED = 4294967295;
export const WAIT_SHOW_MS = 600; // 待ち時間の表示は、これを超えたときだけ出し、出したらこれ以上は続ける(D-118)
export const STATS_PAGE = 50; // 個人成績の表で、一度に出す人数(仮置き。DESIGN 9章)
export const GAMES_PAGE = 20; // 選手の試合ごとの成績で、一度に出す試合数(仮置き。DESIGN 9章)
const WIDE_MIN_WIDTH = 900; // この幅(px)以上を「広い画面」とし、fluid の印を付けた表は横スクロールなしで全列を出す(仮置き。D-223。DESIGN 9章)
// 下のタブ。増やすときは、ここに足して、同じ名前の画面(screen-…)を index.html に作る
export const TABS = [
  { id: "progress", label: "進行" },
  { id: "standings", label: "順位表" },
  { id: "stats", label: "成績" },
  { id: "games", label: "試合" },
];
// タブの上に重ねて開くページ(「戻る」で前の画面へ)
const PAGES = ["player", "team", "game", "settings", "guide", "stadium", "yearend", "offseason", "procedure", "review"];
export const SCREENS = ["start", "new", ...TABS.map((t) => t.id), ...PAGES];

export const $ = (id) => document.getElementById(id);

// オフの手続きの画面の状態の初期値(新規開始・読み込みのたびに作り直す)
export function freshProc() {
  return { selected: new Set(), open: null, renewalOpen: null, phase: null, stage: null, lastOffer: null, faOpen: null, lastRound: null, lastMarket: null, autoLog: null, outlookOpen: false, decision: {}, contract: { group: "all", kind: "war", status: "all", season: "current", sortBy: { key: null, order: null }, shown: null } };
}

export const state = {
  view: null, // 今のゲームの表示用の情報(status・standings・recent・last_day)
  dirty: false, // 未保存の変更があるか
  running: false, // 進めている途中か
  stopRequested: false,
  league: 0, // 順位表で見ているリーグ
  tab: "progress",
  pages: [], // 開いているページ({ name, args })。空ならタブの画面
  newGame: null, // 新規開始の画面の情報(シードと初期名)
  answerLevel: 0, // 答え合わせモード(0:オフ、1:今の能力、2:潜在能力なども)。保存しない
  cache: new Map(), // 見る画面の結果(日が進むまで使い回す)
  cacheStamp: null,
  stats: { role: "batter", kind: "basic", qualified: true, league: "", team: "", season: "current", shown: STATS_PAGE }, // season は "current"・シーズン番号・"career"(F2)
  // 並び順(打者/投手ごとに保つ。基本・セイバー・能力を切り替えても保つ。D-131)。key が null なら、その表の既定
  sortBy: { batter: { key: null, order: null }, pitcher: { key: null, order: null } },
  shownSort: { batter: null, pitcher: null }, // 今の表で実際に使った並び順(既定を解決したもの)
  abilityKeys: { batter: [], pitcher: [] }, // 答え合わせモードがオンのときだけ入る、能力の項目の名前
  warKeys: { batter: [], pitcher: [] }, // WAR の表の列の名前(WAR の表でだけ並び順に使える。D-179)
  playerKind: "basic",
  gamesDay: null,
  proc: freshProc(), // オフの手続きの画面の状態(F3-1。契約の画面の絞り込み・並び順・開いた行は D-272)
  review: { team: null, year: null }, // ドラフトの振り返りの選択(D-216)。null なら計算本体の初期値(自球団・最新の年度)
  token: 0, // 表示の作り直しの番号(古い結果を捨てるため)
};

// 広い画面かどうか(D-223):幅が WIDE_MIN_WIDTH 以上なら <html> に wide の印を付け、fluid の表を横スクロールなしにする(CSS)
const wideQuery = window.matchMedia(`(min-width: ${WIDE_MIN_WIDTH}px)`);
function applyWide() {
  document.documentElement.classList.toggle("wide", wideQuery.matches);
}
applyWide();
wideQuery.addEventListener("change", applyWide);
