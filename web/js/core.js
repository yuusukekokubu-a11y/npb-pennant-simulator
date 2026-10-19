// 共通の土台:定数・画面の状態(state)・要素の取り出し($)・広い画面の印(web/app.js から分けた。保守②。D-291)

export const LOCAL_PYODIDE = new URLSearchParams(location.search).get("pyodide") === "local"; // 試験用(このページに置いた Pyodide を使う)
export const MAX_SEED = 4294967295;
export const WAIT_SHOW_MS = 600; // 待ち時間の表示は、これを超えたときだけ出し、出したらこれ以上は続ける(D-118)
export const STATS_PAGE = 30; // 個人成績の表で、一度に出す人数(並べた上位 30 人。続きは「もっと見る」。D-431)
export const GAMES_PAGE = 20; // 選手の試合ごとの成績で、一度に出す試合数(仮置き。DESIGN 9章)
const WIDE_MIN_WIDTH = 900; // この幅(px)以上を「広い画面」とし、fluid の印を付けた表は横スクロールなしで全列を出す(仮置き。D-223。DESIGN 9章)
// 下のタブ(小さな絵つき。D-401)。増やすときは、ここに足して、同じ名前の画面(screen-…)を index.html に作る。
// screen があるタブは、その画面を開く(自球団のタブは、自球団のチームのページ。観戦のみのときは出さない)
const ICON = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
export const TABS = [
  { id: "progress", label: "進行", icon: ICON('<circle cx="12" cy="12" r="9"/><path d="M10 8l6 4-6 4z" fill="currentColor"/>') },
  { id: "standings", label: "順位", icon: ICON('<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8 20h8"/>') },
  { id: "stats", label: "成績", icon: ICON('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>') },
  { id: "games", label: "試合", icon: ICON('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>') },
  { id: "records", label: "記録", icon: ICON('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11M9 8h6"/>') }, // 記録室(D-422)
  { id: "myteam", label: "自球団", icon: ICON('<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>'), screen: "team" },
];
export const tabScreen = (id) => (TABS.find((t) => t.id === id) || {}).screen || id;
// タブの上に重ねて開くページ(「戻る」で前の画面へ)
const PAGES = ["player", "team", "game", "settings", "guide", "stadium", "yearend", "offseason", "procedure", "lineup", "roster", "trade"];
export const SCREENS = ["start", "new", ...TABS.filter((t) => !t.screen).map((t) => t.id), ...PAGES];
// 広い画面の 2 列(D-402):左に残すタブの画面と、右に並べるページ
export const DUAL_BASES = ["standings", "stats", "games", "team"];
export const DUAL_PAGES = ["player", "team", "game", "stadium"];

export const $ = (id) => document.getElementById(id);

// オフの手続きの画面の状態の初期値(新規開始・読み込みのたびに作り直す)
export function freshProc() {
  return { selected: new Set(), open: null, renewalOpen: null, phase: null, stage: null, faOpen: null, lastRound: null, lastMarket: null, autoLog: null, outlookOpen: false, decision: {}, contract: { group: "all", kind: "war", status: "all", season: "current", sortBy: { key: null, order: null }, shown: null } };
}

export const state = {
  view: null, // 今のゲームの表示用の情報(status・standings・recent・last_day)
  dirty: false, // 未保存の変更があるか
  running: false, // 進めている途中か
  stopRequested: false,
  league: 0, // 順位表で見ているリーグ
  tab: "progress",
  pages: [], // 開いているページ({ name, args })。空ならタブの画面
  newGame: null, // 新規開始の画面の情報(シードと初期名、用意したリーグか)
  template: null, // 用意したリーグの名前と紹介(D-462)
  templateSeconds: null, // 用意したリーグで始めるのにかかった秒(通し確認で測る)
  cache: new Map(), // 見る画面の結果(日が進むまで使い回す)
  cacheStamp: null,
  stats: { role: "batter", kind: "basic", qualified: true, league: "", team: "", season: "current", shown: STATS_PAGE, position: "all" }, // league は "" のとき自球団のリーグ(両リーグの表はない。D-431) // season は "current"・シーズン番号・"career"(F2)
  // 並び順(打者/投手ごとに保つ。基本・セイバー・能力を切り替えても保つ。D-131)。key が null なら、その表の既定
  sortBy: { batter: { key: null, order: null }, fielder: { key: null, order: null }, pitcher: { key: null, order: null } },
  shownSort: { batter: null, fielder: null, pitcher: null }, // 今の表で実際に使った並び順(既定を解決したもの)
  abilityKeys: { batter: [], fielder: [], pitcher: [] }, // 能力の表の列の名前(作り直し①:能力はいつでも見える。D-335)
  warKeys: { batter: [], fielder: [], pitcher: [] }, // WAR の表の列の名前(WAR の表でだけ並び順に使える。D-179)
  playerKind: "basic",
  gamesDay: null,
  gamesPrev: null, // 試合のタブの前・次の試合のある日(休みを飛ばす。D-435)
  gamesNext: null,
  proc: freshProc(), // オフの手続きの画面の状態(F3-1。契約の画面の絞り込み・並び順・開いた行は D-272)
  records: { section: "standings", year: null, role: "batter", leaderKey: null, kind: "draft", team: null, moveYear: null, moveTeam: null }, // 記録室の選択(D-422、D-423)。null なら計算本体の初期値
  glossary: null, // 用語集のデータ(起動のときに受け取る。D-311)
  token: 0, // 表示の作り直しの番号(古い結果を捨てるため)
};

// 広い画面かどうか(D-223):幅が WIDE_MIN_WIDTH 以上なら <html> に wide の印を付け、fluid の表を横スクロールなしにする(CSS)
const wideQuery = window.matchMedia(`(min-width: ${WIDE_MIN_WIDTH}px)`);
const wideListeners = [];
export const isWide = () => wideQuery.matches;
export const onWideChange = (f) => wideListeners.push(f);
function applyWide() {
  document.documentElement.classList.toggle("wide", wideQuery.matches);
  for (const f of wideListeners) f();
}
applyWide();
wideQuery.addEventListener("change", applyWide);

// 球団のマークの色(D-400):球団の番号(T01〜T12)ごとの 12 色。画面だけの飾りで、保存しない
const TEAM_COLORS = ["#2f9e44", "#e8590c", "#1971c2", "#c2255c", "#0b7285", "#5f3dc4", "#d6336c", "#4263eb", "#862e9c", "#e67700", "#087f5b", "#a61e4d"];
export function teamColor(teamId) {
  const n = Number(String(teamId || "").replace(/\D/g, ""));
  return n ? TEAM_COLORS[(n - 1) % TEAM_COLORS.length] : "#5b6788";
}
