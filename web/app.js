// 画面の動き(最小のブラウザ画面①②。D-107、D-114)。計算は worker.js(裏の Python)に任せ、ここは表示と操作だけを行う。
// ブラウザの保存領域(localStorage・sessionStorage・IndexedDB・Cookie)には何も書かない。自動保存もしない。
// 選手の非公開の情報は、答え合わせモードがオンのときだけ、答え合わせ用の入口(answer)から受け取る(D-108、D-114)。
// オフのときは、その入口を呼ばない。答え合わせモードの設定は、どこにも保存しない(開き直すとオフ)。

const LOCAL_PYODIDE = new URLSearchParams(location.search).get("pyodide") === "local"; // 試験用(このページに置いた Pyodide を使う)
const MAX_SEED = 4294967295;
const WAIT_SHOW_MS = 600; // 待ち時間の表示は、これを超えたときだけ出し、出したらこれ以上は続ける(D-118)
const STATS_PAGE = 50; // 個人成績の表で、一度に出す人数(仮置き。DESIGN 9章)
const GAMES_PAGE = 20; // 選手の試合ごとの成績で、一度に出す試合数(仮置き。DESIGN 9章)
const WIDE_MIN_WIDTH = 900; // この幅(px)以上を「広い画面」とし、fluid の印を付けた表は横スクロールなしで全列を出す(仮置き。D-223。DESIGN 9章)
// 下のタブ。増やすときは、ここに足して、同じ名前の画面(screen-…)を index.html に作る
const TABS = [
  { id: "progress", label: "進行" },
  { id: "standings", label: "順位表" },
  { id: "stats", label: "成績" },
  { id: "games", label: "試合" },
];
// タブの上に重ねて開くページ(「戻る」で前の画面へ)
const PAGES = ["player", "team", "game", "settings", "guide", "stadium", "yearend", "offseason", "procedure", "review"];
const SCREENS = ["start", "new", ...TABS.map((t) => t.id), ...PAGES];

const $ = (id) => document.getElementById(id);
let worker = null;
let ready = false;
let nextId = 1;
const pending = new Map();

// オフの手続きの画面の状態の初期値(新規開始・読み込みのたびに作り直す)
function freshProc() {
  return { selected: new Set(), sort: "overall", position: "", open: null, renewalOpen: null, phase: null, stage: null, lastOffer: null, faOpen: null, faFilter: "open", lastRound: null, autoLog: null, contract: { group: "all", kind: "war", status: "all", season: "current", sortBy: { key: null, order: null }, shown: null } };
}

const state = {
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
  // 自由契約・市場の表の状態(D-222)。手続きの画面を行き来しても保つ。sortBy / shownSort は個人成績と同じ仕組み(D-131)
  rosterTable: { role: "batter", kinds: { renewal: "war", release: "basic", fa: "war", market: "war" }, season: "current", group: "", sortBy: { batter: { key: null, order: null }, pitcher: { key: null, order: null } }, shownSort: { batter: null, pitcher: null } }, // 成績の種類は段階ごと(市場の初期値は WAR。D-240)
  token: 0, // 表示の作り直しの番号(古い結果を捨てるため)
};

// 広い画面かどうか(D-223):幅が WIDE_MIN_WIDTH 以上なら <html> に wide の印を付け、fluid の表を横スクロールなしにする(CSS)
const wideQuery = window.matchMedia(`(min-width: ${WIDE_MIN_WIDTH}px)`);
function applyWide() {
  document.documentElement.classList.toggle("wide", wideQuery.matches);
}
applyWide();
wideQuery.addEventListener("change", applyWide);

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

function call(cmd, args) {
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

async function boot() {
  try {
    worker = await createWorker();
    await call("boot", { local: LOCAL_PYODIDE });
    ready = true;
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

const query = (name, args = {}) => cached("query", name, args);

function answer(name, args = {}) {
  // 答え合わせモードがオフのときは、答え合わせ用の入口を呼ばない(D-108)
  if (state.answerLevel === 0) return Promise.reject(new Error("答え合わせモードがオフです"));
  return cached("answer", name, { ...args, level: state.answerLevel });
}

function clearAnswers() {
  for (const key of [...state.cache.keys()]) if (key.startsWith("answer|")) state.cache.delete(key);
}

// 待ち時間の表示:0.6 秒を超えたら出し、出したら最低 0.6 秒は続ける(D-118)
function delayedShow(show, hide) {
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

function withLoading(id, promise) {
  const done = delayedShow(() => ($(id).hidden = false), () => ($(id).hidden = true));
  return promise.finally(done);
}

// ---- 画面の切り替え ----

function showScreen(name) {
  for (const id of SCREENS) $(`screen-${id}`).hidden = id !== name;
  const inGame = !["start", "new"].includes(name);
  $("topbar").hidden = !inGame;
  $("tabs").hidden = !inGame;
  if (TABS.some((t) => t.id === name)) state.tab = name;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
  window.scrollTo(0, 0);
}

function current() {
  return state.pages.length ? state.pages[state.pages.length - 1] : { name: state.tab, args: {} };
}

function showTab(name) {
  state.pages = [];
  showScreen(name);
  renderCurrent();
}

function openPage(name, args = {}) {
  state.pages.push({ name, args });
  showScreen(name);
  renderCurrent();
}

function back() {
  state.pages.pop();
  showScreen(current().name);
  renderCurrent();
}

function renderCurrent() {
  if (!state.view) return;
  const { name, args } = current();
  const token = ++state.token;
  const job = {
    progress: () => renderProgress(),
    standings: () => renderStandings(),
    stats: () => renderStats(token),
    games: () => renderGames(token),
    player: () => renderPlayer(args, token),
    team: () => renderTeam(args, token),
    game: () => renderGame(args, token),
    settings: () => renderSettings(),
    guide: () => renderGuide(token),
    stadium: () => renderStadium(args, token),
    yearend: () => renderYearEnd(token),
    offseason: () => renderOffseason(args, token),
    procedure: () => renderProcedure(token),
    review: () => renderReview(args, token),
  }[name];
  Promise.resolve(job && job()).catch((err) => showError(name, err));
}

function showError(name, err) {
  const box = { stats: "stats-table", games: "games-list", player: "player-season", team: "team-record", game: "game-log", yearend: "yearend-message", offseason: "offseason-retired", procedure: "proc-message", review: "review-table" }[name];
  if (box) $(box).replaceChildren(el("p", { className: "message ng" }, `表示できませんでした:${err.message}`));
}

function buildTabs() {
  for (const t of TABS) {
    const b = document.createElement("button");
    b.textContent = t.label;
    b.dataset.tab = t.id;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => showTab(t.id));
    $("tabs").appendChild(b);
  }
}

// ---- 部品 ----

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k in e) e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

function link(text, onclick, className = "link") {
  return el("button", { className, type: "button", onclick }, text);
}

const playerLink = (name, id) => link(name, () => openPage("player", { id }));
const teamLink = (name, id) => link(name, () => openPage("team", { id }));

function setPressed(groupId, value) {
  for (const b of $(groupId).children) b.setAttribute("aria-pressed", String(b.dataset.value === value));
}

// 表を作る。columns は { key, label, description }、rows の値は row.values[key]。
// first は左端(固定)の列の中身を作る関数。onSort があれば、見出しを押して並べ替えられる。
// extra は、名前の隣に固定して出す列(並び順の指標が表にないとき。D-131)
// fluid は、広い画面で横スクロールなしに全列を出す表(新しく作る表だけ。D-223)。detail(row) が要素を返せば、その行の下に 1 行足す
function table({ firstLabel, columns, rows, first, sort, order, onSort, rowClass, limit, more, extra, fluid, detail }) {
  const headCell = (c, className) => {
    // ▲▼ は別の要素にして、列の右の余白に置く(文字の右端を数値の右端にそろえる。D-267)
    const arrow = sort === c.key ? el("span", { className: "arrow" }, order === "desc" ? " ▼" : " ▲") : null;
    const text = arrow ? [c.label, arrow] : [c.label];
    const label = onSort ? el("button", { className: "sort", type: "button", onclick: () => onSort(c.key) }, ...text) : el("span", {}, ...text);
    return el("th", { scope: "col", className: `${className} ${sort === c.key ? "sorted" : ""}`.trim(), title: c.description || "" }, label);
  };
  const nameClass = extra ? "sticky name-fixed" : "sticky";
  const head = el("tr", {}, el("th", { className: nameClass, scope: "col" }, firstLabel));
  if (extra) head.append(headCell(extra, "sticky2"));
  for (const c of columns) head.append(headCell(c, ""));
  const body = el("tbody");
  const shown = limit ? rows.slice(0, limit) : rows;
  for (const r of shown) {
    const tr = el("tr", { className: rowClass ? rowClass(r) : "" }, el("td", { className: `${nameClass} name-cell` }, ...first(r)));
    if (extra) tr.append(el("td", { className: sort === extra.key ? "sticky2 sorted" : "sticky2" }, r.values[extra.key] ?? ""));
    for (const c of columns) tr.append(el("td", sort === c.key ? { className: "sorted" } : {}, r.values[c.key] ?? ""));
    body.append(tr);
    const d = detail ? detail(r) : null;
    if (d) body.append(el("tr", { className: "detail" }, el("td", { colSpan: columns.length + 1 + (extra ? 1 : 0) }, d)));
  }
  const wrap = el("div", {}, el("div", { className: fluid ? "table-wrap fluid" : "table-wrap" }, el("table", {}, el("thead", {}, head), body)));
  if (limit && rows.length > limit && more) {
    wrap.append(el("button", { className: "secondary more", type: "button", onclick: more }, `もっと見る(あと ${rows.length - limit} 件)`));
  }
  return wrap;
}

function terms(listId, columns) {
  $(listId).replaceChildren(...columns.flatMap((c) => [el("dt", {}, c.label), el("dd", {}, c.description)]));
}

function sortWord(col, order) {
  if (col.type === "count") return order === "desc" ? "多い順" : "少ない順";
  return order === "desc" ? "高い順" : "低い順";
}

// 試合の結果のカード(押すと試合のページへ)
function gameCard(g) {
  const tags = g.tags.length ? el("span", { className: "tags" }, g.tags.join("・")) : null;
  return el(
    "button",
    { className: "game-card" + (g.is_mine ? " mine" : ""), type: "button", onclick: () => openPage("game", { game_no: g.game_no }), dataset: { gameNo: g.game_no } },
    el("span", {}, g.away.name),
    el("span", { className: "score" }, String(g.away.runs)),
    el("span", {}, g.home.name),
    el("span", { className: "score" }, String(g.home.runs)),
    tags,
  );
}

// ---- 進行・順位表 ----

function setDirty(value) {
  state.dirty = value;
  $("dirty").hidden = !value;
}

function recordText(m) {
  const gb = m.games_behind === "-" ? (m.rank === 1 ? "首位" : "首位と同率") : `首位と ${m.games_behind} ゲーム差`;
  return `${m.league_name} ${m.rank}位 / ${m.wins}勝 ${m.losses}敗 ${m.ties}分 / 勝率 ${m.pct} / ${gb}`;
}

function seasonWord(s) {
  return `${s.year}シーズン目`;
}

function renderProgress() {
  const s = state.view.status;
  $("day-text").textContent = `${seasonWord(s)} ` + (s.is_over ? `全${s.total_days}日 終了` : `${s.day}日目 / ${s.total_days}日`);
  $("topbar-day").textContent = `${seasonWord(s)} ` + (s.is_over ? "シーズン終了" : `${s.day}日目 / ${s.total_days}日`);
  $("year-end-box").hidden = !s.can_year_end;
  $("year-end").disabled = state.running;
  const off = s.offseason;
  $("offseason-box").hidden = !off;
  if (off) $("offseason-box-text").textContent = `${off.year}シーズン目のオフの手続きが進行中です(今の段階:${off.phase_label})。手続きを終えると、${off.year + 1}シーズン目が始まります。`;
  $("games-text").textContent = `${s.games_played} / ${s.total_games} 試合`;
  const m = s.my_team;
  $("mine-card").hidden = !m;
  if (m) {
    $("mine-name").textContent = m.name;
    $("mine-record").textContent = recordText(m);
  }
  const last = state.view.last_day;
  $("last-day-title").textContent = last.games.length ? `直近の日の試合(${last.day}日目)` : "直近の日の試合";
  $("last-day").replaceChildren(...last.games.map(gameCard));
  $("last-day-empty").hidden = last.games.length > 0;
  const ul = $("recent");
  ul.replaceChildren();
  for (const g of state.view.recent) {
    const mark = el("span", { className: "mark " + (g.outcome === "勝" ? "win" : g.outcome === "負" ? "loss" : "") }, g.outcome === "勝" ? "○" : g.outcome === "負" ? "●" : "△");
    const extra = [g.innings > 9 ? `延長${g.innings}回` : "", g.walkoff ? (g.outcome === "勝" ? "サヨナラ勝ち" : "サヨナラ負け") : ""].filter(Boolean).join("・");
    const text = el("span", {}, `${g.day}日目 ${g.home ? "(ホーム)" : "(ビジター)"} 対 ${g.opponent} ${g.score}${extra ? `(${extra})` : ""}`);
    ul.appendChild(el("li", {}, mark, text));
  }
  $("recent-empty").hidden = state.view.recent.length > 0;
  for (const b of document.querySelectorAll(".adv")) b.disabled = state.running || s.is_over;
  $("save").disabled = state.running;
  $("open-file-top").disabled = state.running || !ready;
}

function renderStandings() {
  const table = state.view.standings;
  const sw = $("league-switch");
  if (sw.children.length !== table.leagues.length) {
    sw.replaceChildren();
    for (const lg of table.leagues) {
      sw.appendChild(
        el("button", { type: "button", onclick: () => { state.league = lg.index; renderStandings(); } }, lg.name),
      );
    }
  }
  [...sw.children].forEach((b, i) => {
    b.textContent = table.leagues[i].name;
    b.setAttribute("aria-pressed", String(i === state.league));
  });
  const lg = table.leagues[state.league];
  $("standings-day").textContent = `${table.day}日目まで(全${state.view.status.total_days}日)`;
  const body = $("standings-body");
  body.replaceChildren();
  for (const r of lg.rows) {
    const name = el("td", { className: "sticky" }, el("span", { className: "rank" }, String(r.rank)), teamLink(r.name, r.team_id));
    if (r.is_mine) name.append(el("span", { className: "you", "aria-label": "自球団" }, "★"));
    const tr = el("tr", { className: r.is_mine ? "mine" : "" }, name);
    for (const v of [r.games, r.wins, r.losses, r.ties, r.pct, r.games_behind]) tr.append(el("td", {}, String(v)));
    body.appendChild(tr);
  }
}

function enterGame(view, dirty) {
  state.view = view;
  state.cache.clear();
  state.pages = [];
  state.gamesDay = null;
  state.stats.league = "";
  state.stats.team = "";
  setDirty(dirty);
  const mine = view.status.my_team;
  if (mine) state.league = view.standings.leagues.findIndex((lg) => lg.rows.some((r) => r.is_mine));
  if (state.league < 0) state.league = 0;
  $("progress-message").textContent = "";
  buildStatsFilters();
  showScreen("progress");
  renderCurrent();
}

// ---- 成績(個人成績。D-114〜D-116) ----

function buildStatsFilters() {
  const seasons = state.view.status.seasons || [{ key: "current", label: "今シーズン" }];
  $("stats-season").replaceChildren(...seasons.map((x) => el("option", { value: x.key }, x.label)));
  if (!seasons.some((x) => x.key === state.stats.season)) state.stats.season = "current";
  $("stats-season").value = state.stats.season;
  $("stats-season").hidden = seasons.length < 2; // 1シーズン目は選ぶものがないので出さない
  const leagues = state.view.standings.leagues;
  $("stats-league").replaceChildren(el("option", { value: "" }, "両リーグ"), ...leagues.map((lg) => el("option", { value: String(lg.index) }, lg.name)));
  $("stats-league").value = state.stats.league;
  const teams = leagues.filter((lg) => state.stats.league === "" || String(lg.index) === state.stats.league).flatMap((lg) => lg.rows);
  $("stats-team").replaceChildren(el("option", { value: "" }, "すべてのチーム"), ...teams.map((r) => el("option", { value: r.team_id }, r.name)));
  if (!teams.some((r) => r.team_id === state.stats.team)) state.stats.team = "";
  $("stats-team").value = state.stats.team;
  $("stats-qualified").checked = state.stats.qualified;
}

function statsArgs() {
  const s = state.stats;
  return {
    role: s.role,
    qualified: s.qualified,
    league: s.league === "" ? null : Number(s.league),
    team_id: s.team || null,
    season: s.season === "current" ? null : s.season,
  };
}

function currentSort() {
  return state.sortBy[state.stats.role];
}

function isAbilityKey(key) {
  return state.abilityKeys[state.stats.role].includes(key);
}

function isWarKey(key) {
  return state.warKeys[state.stats.role].includes(key);
}

// 並び順の選択欄(D-132):基本・セイバーの全指標。答え合わせモードがオンなら、能力の項目も
async function buildSortSelect(sortKey, warColumns) {
  const role = state.stats.role;
  const keys = await query("sortable_keys", { role });
  const groups = { basic: [], saber: [], count: [] };
  for (const k of keys) (groups[k.type === "count" ? "count" : k.category] || groups.count).push(k);
  const options = [
    el("optgroup", { label: "基本" }, ...groups.basic.map((k) => el("option", { value: k.key }, k.label))),
    el("optgroup", { label: "セイバー" }, ...groups.saber.map((k) => el("option", { value: k.key }, k.label))),
    el("optgroup", { label: "元の数" }, ...groups.count.map((k) => el("option", { value: k.key }, k.label))),
  ];
  if (warColumns) {
    state.warKeys[role] = warColumns.map((c) => c.key);
    options.unshift(el("optgroup", { label: "WAR(WAR の表でだけ)" }, ...warColumns.map((c) => el("option", { value: c.key }, c.label))));
  }
  if (state.answerLevel > 0) {
    const cols = await answer("ability_columns", { role });
    state.abilityKeys[role] = cols.map((c) => c.key);
    options.push(el("optgroup", { label: "能力(答え合わせモード)" }, ...cols.map((c) => el("option", { value: c.key }, c.label))));
  } else {
    state.abilityKeys[role] = [];
  }
  $("stats-sort").replaceChildren(...options);
  $("stats-sort").value = sortKey;
}

// 「既定」のままの並び順を、実際に使った指標に固定する(基本・セイバー・能力を切り替えても保つため。D-131)
function commitSort() {
  const s = currentSort();
  const shown = state.shownSort[state.stats.role];
  if (s.key === null && shown) Object.assign(s, shown);
}

function showSortState(sortCol, order) {
  state.shownSort[state.stats.role] = { key: sortCol.key, order };
  $("stats-sort").value = sortCol.key;
  const counts = sortCol.type === "count";
  for (const b of $("stats-order").children) {
    b.textContent = b.dataset.value === "desc" ? (counts ? "多い順" : "高い順") : counts ? "少ない順" : "低い順";
    b.setAttribute("aria-pressed", String(b.dataset.value === order));
  }
  $("stats-info").replaceChildren(el("strong", {}, `並び順:${sortCol.label}(${sortWord(sortCol, order)})`), sortCol.description);
}

async function renderStats(token) {
  const s = state.stats;
  setPressed("stats-role", s.role);
  setPressed("stats-kind", s.kind);
  const note = {
    basic: "基本:打率・防御率など、昔からよく使われる成績です。",
    saber: "セイバー:セイバーメトリクス(統計で選手の実力を測る考え方)の指標です。運に左右されにくく、実力が出やすい数を集めています。",
    war: "WAR:控え水準の選手に比べて、何勝分多く勝ちに貢献したか(打撃・走塁・守備・ポジション補正をまとめた値)。用語の解説は表の下にあります。",
    ability: "能力:選手の本当の実力の数値です(答え合わせモードのときだけ)。",
  }[s.kind];
  $("stats-kind-note").textContent = note;
  if (s.kind === "ability") {
    $("stats-baseline").hidden = true;
    return renderAbilityTable(token);
  }
  const sort = currentSort();
  // 能力の項目で並べていたときは、成績の表ではその表の既定に戻す(能力の値は、公開用の関数では扱わない)。
  // WAR の列は WAR の表でだけ、WAR 以外の列は WAR 以外の表でだけ使える
  const usable = sort.key && !isAbilityKey(sort.key) && (s.kind === "war" ? isWarKey(sort.key) : !isWarKey(sort.key));
  const key = usable ? sort.key : null;
  const args = { ...statsArgs(), kind: s.kind, sort: key, order: key ? sort.order : null };
  const data = await withLoading("stats-loading", query("stats", args));
  if (token !== state.token) return;
  await buildSortSelect(data.sort.key, s.kind === "war" ? data.columns : null);
  if (token !== state.token) return;
  showSortState(data.sort, data.order);
  const first = (r) => [el("span", { className: "rank" }, String(r.rank)), playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)];
  $("stats-table").replaceChildren(
    data.rows.length
      ? table({
          firstLabel: "順位・選手",
          columns: data.columns,
          rows: data.rows,
          first,
          sort: data.sort.key,
          order: data.order,
          onSort: (key) => sortStats(key),
          rowClass: (r) => (r.is_mine ? "mine" : ""),
          limit: s.shown,
          more: () => { s.shown += STATS_PAGE; renderCurrent(); },
          extra: data.extra_column,
        })
      : el("p", { className: "muted" }, s.qualified ? "条件に合う選手がいません(規定到達者がまだいないときは、「規定到達者だけ」を外してください)。" : "条件に合う選手がいません。"),
  );
  $("stats-baseline").hidden = !data.baseline_note;
  $("stats-baseline").textContent = data.baseline_note || "";
  const extraNote = data.extra_column ? `並び順に使っている「${data.extra_column.label}」はこの表にない指標なので、名前の隣に固定して出しています。` : "";
  const seasonNote = data.season && data.season !== "current" ? `${data.season_label}の成績です。` : "";
  $("stats-rule").textContent = `${seasonNote}${data.qualify_rule}。${s.qualified ? "今は、規定に届いた選手だけを出しています。" : "今は、試合に出た全員を出しています。"} ${extraNote}表は横にずらせます。列の見出しを押すと並べ替え、もう一度押すと逆の順になります。上の「並び順」の欄からも選べます。`;
  terms("stats-terms-list", (data.extra_column ? [data.extra_column, ...data.columns] : data.columns).concat(data.terms || []));
  $("stats-terms").open = Boolean(data.terms); // WAR の表は、用語の解説を表の下に開いて出す(D-179)
}

// 見出しを押したときの並び順の決め方(表の部品で共通。D-131):今の並びと同じ列なら逆順に、違う列ならその指標の「よい」向き(能力の項目は高い順)から
function sortToggle(sortBy, shown, key) {
  if (shown && shown.key === key) {
    sortBy.key = key;
    sortBy.order = shown.order === "desc" ? "asc" : "desc";
  } else {
    sortBy.key = key;
    sortBy.order = null;
  }
}

function sortStats(key) {
  sortToggle(currentSort(), state.shownSort[state.stats.role], key);
  state.stats.shown = STATS_PAGE;
  renderCurrent();
}

async function renderAbilityTable(token) {
  const s = state.stats;
  if (state.answerLevel === 0) {
    await buildSortSelect(currentSort().key);
    if (token !== state.token) return;
    $("stats-info").replaceChildren(el("strong", {}, "答え合わせモードをオンにすると見られます"), "上の「メニュー」から、答え合わせモードをオンにしてください。");
    $("stats-table").replaceChildren();
    $("stats-rule").textContent = "";
    $("stats-terms-list").replaceChildren();
    return;
  }
  const sort = currentSort();
  const data = await withLoading("stats-loading", answer("ability_table", { ...statsArgs(), sort: sort.key, order: sort.order }));
  if (token !== state.token || state.answerLevel === 0) return;
  await buildSortSelect(data.sort.key);
  if (token !== state.token || state.answerLevel === 0) return;
  showSortState(data.sort, data.order);
  $("stats-table").replaceChildren(
    table({
      firstLabel: "順位・選手",
      columns: data.columns,
      rows: data.rows,
      first: (r) => [el("span", { className: "rank" }, String(r.rank)), playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)],
      sort: data.sort.key,
      order: data.order,
      onSort: (key) => sortStats(key),
      rowClass: (r) => (r.is_mine ? "mine" : ""),
      limit: s.shown,
      more: () => { s.shown += STATS_PAGE; renderCurrent(); },
      extra: data.extra_column,
    }),
  );
  const extraNote = data.extra_column ? `並び順に使っている「${data.extra_column.label}」は成績の指標なので、名前の隣に固定して出しています。` : "";
  $("stats-rule").textContent = `${data.note}。「+」は 80 より上、「-」は 20 より下の値を、端にそろえて表示しています。${extraNote}`;
  terms("stats-terms-list", data.extra_column ? [data.extra_column, ...data.columns] : data.columns);
}

// ---- 試合(日付ごとの一覧) ----

async function renderGames(token) {
  const last = state.view.status.day;
  const sel = $("games-day");
  if (sel.options.length !== last) {
    sel.replaceChildren(...Array.from({ length: last }, (_, i) => el("option", { value: String(i + 1) }, `${i + 1}日目`)));
  }
  if (state.gamesDay === null || state.gamesDay > last) state.gamesDay = last;
  $("games-empty").hidden = last > 0;
  $("games-prev").disabled = state.gamesDay <= 1;
  $("games-next").disabled = state.gamesDay >= last;
  if (last === 0) {
    $("games-list").replaceChildren();
    return;
  }
  sel.value = String(state.gamesDay);
  const data = await query("games_on", { day: state.gamesDay });
  if (token !== state.token) return;
  $("games-list").replaceChildren(...data.games.map(gameCard));
}

// ---- 選手のページ ----

function kvTable(rows) {
  // rows: [{ label, description, values: [..] }]。見出しを押すと解説を出す
  const body = el("tbody");
  for (const r of rows) {
    const desc = el("span", { className: "desc", hidden: true }, r.description || "");
    const head = r.description ? el("th", { scope: "row" }, link(r.label, () => (desc.hidden = !desc.hidden)), desc) : el("th", { scope: "row" }, r.label);
    body.append(el("tr", {}, head, ...r.values.map((v) => el("td", {}, v))));
  }
  return el("div", { className: "table-wrap" }, el("table", { className: "kv" }, body));
}

async function renderPlayer(args, token) {
  const data = await query("player", { player_id: args.id });
  if (token !== state.token) return;
  const p = data.player;
  $("player-name").textContent = p.name;
  $("player-info").replaceChildren(
    teamLink(p.team_name, p.team_id),
    p.is_mine ? " ★" : "",
    ` / ${p.position_label} / ${p.age}歳${p.hand ? ` / ${p.hand}` : ""}${p.retired ? "(引退)" : ""}`,
  );
  renderPlayerHistory(data);
  renderPlayerContract(data);
  await renderPlayerScouting(data, token);
  setPressed("player-kind", state.playerKind);
  const box = $("player-season");
  $("player-baseline").hidden = true;
  if (state.playerKind === "ability") {
    if (state.answerLevel === 0) {
      box.replaceChildren(el("p", { className: "info" }, "答え合わせモードをオンにすると見られます(上の「メニュー」から)。"));
    } else {
      const a = await answer("player_answers", { player_id: args.id });
      if (token !== state.token || state.answerLevel === 0) return;
      const rows = a.items.map((i) => ({ label: i.label, description: i.description, values: a.level === 2 ? [i.current, i.potential] : [i.current] }));
      const parts = [el("p", { className: "muted small" }, a.level === 2 ? "左:今の能力 / 右:潜在能力(伸びきったときの能力)。見出しを押すと解説が出ます。" : "今の能力。見出しを押すと解説が出ます。"), kvTable(rows)];
      if (a.level === 2) parts.push(el("p", {}, `成長タイプ:${a.growth_type} / 生成時の型:${a.archetype}`));
      parts.push(el("p", { className: "muted small" }, `${a.note}。「+」は 80 より上、「-」は 20 より下。`));
      box.replaceChildren(...parts);
    }
  } else if (!data.season) {
    box.replaceChildren(el("p", { className: "muted" }, "まだ試合に出ていません。"));
  } else if (state.playerKind === "war") {
    const w = data.season.war;
    const rows = w.columns.map((c) => ({ label: c.label, description: c.description, values: [w.values[c.key]] }));
    const dl = el("dl", { className: "terms" }, ...w.terms.flatMap((c) => [el("dt", {}, c.label), el("dd", {}, c.description)]));
    box.replaceChildren(kvTable(rows), el("p", { className: "muted small" }, `${w.note} 見出しを押すと解説が出ます。`), el("details", { open: true }, el("summary", {}, "WAR の用語の解説"), dl));
  } else {
    const t = data.season.tables[state.playerKind];
    $("player-baseline").hidden = !(state.playerKind === "saber" && data.season.baseline_note);
    $("player-baseline").textContent = data.season.baseline_note || "";
    const rows = t.columns.map((c) => ({ label: c.label, description: c.description, values: [t.values[c.key]] }));
    box.replaceChildren(
      kvTable(rows),
      el("p", { className: "muted small" }, `${data.season.qualified ? "規定に届いています" : "規定に届いていません"}(${data.season.qualify_rule})。見出しを押すと解説が出ます。`),
    );
  }
  const gbox = $("player-games");
  if (!data.games.length) {
    gbox.replaceChildren(el("p", { className: "muted" }, "まだ試合に出ていません。"));
    return;
  }
  const cols = [{ key: "result", label: "結果" }, ...data.game_columns];
  const rows = data.games.map((g) => ({
    ...g,
    values: { ...g.values, result: `${g.outcome === "勝" ? "○" : g.outcome === "負" ? "●" : "△"}${g.score}${g.decision ? ` ${g.decision}` : ""}` },
  }));
  const limit = args.gamesShown || GAMES_PAGE;
  gbox.replaceChildren(
    table({
      firstLabel: "日・相手",
      columns: cols,
      rows,
      first: (g) => [link(`${g.day}日目`, () => openPage("game", { game_no: g.game_no })), el("span", { className: "sub" }, `${g.home ? "対" : "@"} ${g.opponent}`)],
      limit,
      more: () => { args.gamesShown = limit + GAMES_PAGE; renderCurrent(); },
    }),
    el("p", { className: "muted small" }, "「対」はホーム、「@」はビジター(相手の本拠地)の試合。結果の ○ は勝ち、● は負け、△ は引き分け。勝・敗・S(セーブ)・H(ホールド)は、その試合の投手の記録。日付を押すと、その試合のページを開きます。"),
  );
}

// 入団時のスカウト評価(F3-1。D-199、D-206)。答え合わせモードがオンなら、真の能力を並べる
// 契約(F3-2a。D-232):年俸・契約年数・残り年数・年俸の履歴(公開情報。他球団の選手も)
// 志望の重み(答え合わせモードがオンのときだけ。F3-2b。D-245)
async function renderPlayerPreference(playerId) {
  const token = state.token;
  const a = await answer("player_answers", { player_id: playerId });
  if (token !== state.token || state.answerLevel === 0 || !a.preference || !a.preference.length) return;
  $("player-contract").append(el("p", { className: "small answer", id: "player-preference" }, `志望(答え合わせ):${a.preference.map((x) => `${x.label} ${x.text}${x.active ? "" : "(効かない)"}`).join("・")}`));
}

function renderPlayerContract(data) {
  const c = data.player.contract;
  const box = $("player-contract-box");
  box.hidden = !c;
  if (!c) return;
  const years = c.history.length ? c.history[c.history.length - 1].years : null;
  $("player-contract").replaceChildren(
    kvTable([
      { label: "年俸", description: "架空の「万円」。見込みの WAR(直近 3 シーズンの WAR の加重平均。履歴がなければスカウト評価)から算定した値", values: [c.salary_text] },
      { label: "契約年数", description: "今の契約の年数(年齢と見込みの WAR で決まる)", values: [years ? `${years} 年` : "-"] },
      { label: "残り年数", description: "今シーズンを含めた残り。0 なら今シーズンの終わりで満了", values: [`${c.remaining} 年(${c.until}シーズン目まで)`] },
      { label: "FA 権", description: "一軍に登録されたシーズン(シーズンの最初に選ばれる一軍 29 人に入ったシーズン)の数。7 シーズンで FA 権。宣言すると 0 から数え直す", values: [c.fa_text] },
    ]),
    el("details", { id: "player-contract-history" }, el("summary", {}, "年俸と更改の履歴"), table({ firstLabel: "シーズン", columns: [{ key: "salary", label: "年俸" }, { key: "years", label: "年数" }, { key: "reason", label: "きっかけ" }, { key: "offers", label: "提示", description: "更改で受けるまでに提示された回数" }], rows: [...c.history].reverse().map((h) => ({ ...h, values: { salary: h.salary_text, years: `${h.years} 年`, reason: h.reason_label, offers: h.offers ? `${h.offers} 回` : "-" } })), first: (h) => [el("span", {}, `${h.year}シーズン目〜`)] })),
  );
  if (state.answerLevel > 0) renderPlayerPreference(data.player.id);
  $("player-contract-note").textContent = "年俸は見える情報(成績とスカウト評価)だけで決まり、真の能力は使っていません。契約が満了すると、次のオフの契約更改で、算定し直した年俸で提示されます(選手は志望で受けるか断るかを決めます)。";
}

async function renderPlayerScouting(data, token) {
  const sc = data.player.scouting;
  const box = $("player-scouting-box");
  if (!sc) {
    box.hidden = true;
    $("player-scouting").replaceChildren();
    return;
  }
  box.hidden = false;
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("scouting_answers", { player_id: data.player.id });
    if (token !== state.token) return;
    if (state.answerLevel === 0) truth = null;
  }
  const byKey = truth && truth.available ? Object.fromEntries(truth.items.map((i) => [i.key, i])) : null;
  const rows = [{ label: "総合", values: [sc.overall_text, byKey ? "" : ""].slice(0, byKey ? 2 : 1) }];
  for (const i of sc.items) rows.push({ label: i.label, values: byKey ? [i.text, `${byKey[i.key]?.current ?? ""}${byKey[i.key]?.potential ? ` / ${byKey[i.key].potential}` : ""}`] : [i.text] });
  rows.push({ label: "天井", values: byKey ? [sc.ceiling, ""] : [sc.ceiling] });
  $("player-scouting").replaceChildren(
    el("p", { className: "muted small" }, `${sc.year ? `${sc.year}シーズン目の入団時に、` : "リーグの歴史(ゲーム開始前)の入団時に、"}${sc.team_name} のスカウトがつけた評価(推定値 ± ふれ幅。天井は S〜D${sc.method === 1 ? "。旧方式の評価" : ""})。${byKey ? `右は真の能力(今の能力${truth.level === 2 ? " / 潜在能力" : ""})。` : ""}`),
    kvTable(rows),
  );
  $("player-scouting-note").textContent = byKey ? truth.note : "答え合わせモードをオンにすると、真の能力と並べて見られます。入団後の能力は、オフのときは表示されません。";
}

// ---- ドラフトの振り返り(当たり外れの一覧。D-216) ----

async function renderReview(args, token) {
  const rv = state.review;
  if (args.team_id) {
    rv.team = args.team_id;
    rv.year = null;
    args.team_id = null;
  }
  $("review-loading").hidden = false;
  let d;
  try {
    d = await query("draft_review", { team_id: rv.team, year: rv.year });
  } finally {
    if (token === state.token) $("review-loading").hidden = true;
  }
  if (token !== state.token) return;
  rv.team = d.team_id;
  rv.year = d.year;
  const teamSel = $("review-team");
  teamSel.replaceChildren(...d.teams.map((t) => el("option", { value: t.team_id }, t.team_name + (t.is_mine ? " ★" : ""))));
  teamSel.value = d.team_id;
  const yearSel = $("review-year");
  yearSel.replaceChildren(...d.years.map((y) => el("option", { value: String(y) }, `${y}シーズン目に入団`)));
  yearSel.disabled = d.years.length === 0;
  if (d.year !== null) yearSel.value = String(d.year);
  $("review-answers").replaceChildren();
  $("review-answers-note").textContent = "";
  if (d.year === null) {
    $("review-table").replaceChildren(el("p", { className: "info" }, d.note));
    $("review-note").textContent = "";
    return;
  }
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("draft_review_answers", { team_id: d.team_id, year: d.year });
    if (token !== state.token) return;
    if (state.answerLevel === 0) truth = null;
  }
  const on = truth && truth.available;
  const cols = [
    { key: "route", label: "経路", description: "入団の経路。ドラフトの巡、自由契約市場、自動補充" },
    { key: "position", label: "ポジション" },
    { key: "age", label: "年齢", description: "今の年齢(引退・退団した選手は入団時の年齢)" },
    { key: "entry", label: "入団時の総合", description: "入団時のスカウトの総合の推定値 ± ふれ幅(真の値が入る確率が約 80% の幅)" },
    { key: "ceiling", label: "天井", description: "入団時の天井の段階(S〜D。候補全体の中での伸びしろの見積もり)" },
    { key: "status", label: "今の所属", description: "在籍 / 他球団の名前 / 引退・退団" },
    { key: "games", label: "出場", description: "入団から今までの出場試合数の合計(投手は登板数)" },
    { key: "war", label: "WAR 累計", description: "入団から今までの WAR の合計(野手は WAR、投手は失点版)" },
  ];
  if (on) {
    cols.push(
      { key: "truth", label: "今の真の総合", description: "答え合わせ:今の真の能力の総合値" },
      { key: "diff", label: "差", description: "答え合わせ:今の真の総合 − 入団時の推定値(+ は期待以上、− は期待以下。入団後の成長・衰退も含む)" },
      { key: "actual", label: "実際の天井", description: "答え合わせ:潜在能力の総合値を、入団時の区切りで判定した段階" },
    );
    if (truth.level === 2) cols.push({ key: "potential", label: "潜在能力", description: "答え合わせ(2段階目):潜在能力の総合値" });
  }
  const rows = d.rows.map((r) => {
    const t = on ? truth.players[r.player_id] : null;
    return {
      ...r,
      values: {
        route: r.route_label,
        position: r.position_label,
        age: r.age !== null ? `${r.age}歳` : r.entry_age !== null ? `(${r.entry_age}歳)` : "",
        entry: r.entry_text,
        ceiling: ceilingText(r.entry_ceiling),
        status: r.status_label,
        games: String(r.games),
        war: r.war,
        truth: t ? t.overall : r.in_league ? "" : "-",
        diff: t ? t.diff : r.in_league ? "" : "-",
        actual: t ? t.actual_ceiling : "-",
        potential: t && t.potential ? t.potential : "",
      },
    };
  });
  $("review-table").replaceChildren(
    rows.length
      ? table({ firstLabel: "選手", columns: cols, rows, first: (r) => [r.in_league ? playerLink(r.name, r.player_id) : el("span", {}, r.name)], rowClass: (r) => (r.status === "left" ? "muted" : ""), fluid: true })
      : el("p", { className: "info" }, "この年度にこの球団に入った選手はいません。"),
  );
  $("review-note").textContent = `${d.note} 入団直後は「差」がマイナスに偏りやすい(評価が高く見えた候補が選ばれるため。いわゆる勝者の呪い)。数年進めて育った後に見ると、本来の差に近づきます。`;
  if (!on) {
    $("review-answers-note").textContent = state.answerLevel > 0 ? "" : "答え合わせモードをオンにすると(上の「メニュー」から)、今の真の総合・入団時の推定値との差・実際の天井と、球団ごとの「見る目」の目安が出ます。";
    return;
  }
  const tcols = [
    { key: "count", label: "人数", description: "その年度に入って、今もリーグにいる選手の数" },
    { key: "mean", label: "差の平均", description: "今の真の総合 − 入団時の推定値 の平均。リーグ全体より高ければ、見積もりが控えめ(当たりが多い)" },
    { key: "sd", label: "差の標準偏差", description: "差のばらつき。小さいほど評価のぶれが小さい" },
  ];
  const trows = truth.teams.map((t) => ({ ...t, values: { count: String(t.count), mean: t.mean, sd: t.sd } }));
  trows.push({ team_id: "", team_name: "リーグ全体", selected: false, is_mine: false, values: { count: String(truth.league.count), mean: truth.league.mean, sd: truth.league.sd } });
  $("review-answers").replaceChildren(
    el("h2", {}, `球団ごとの「見る目」の目安(${d.year}シーズン目の入団)`),
    table({ firstLabel: "球団", columns: tcols, rows: trows, first: (r) => [el("span", { className: r.selected ? "sorted" : "" }, r.team_name + (r.is_mine ? " ★" : ""))], rowClass: (r) => (r.selected ? "mine" : ""), fluid: true }),
  );
  $("review-answers-note").textContent = truth.note;
}

// ---- オフの手続き(F3-1。D-201〜D-207) ----

async function procCall(name, args = {}) {
  const r = await call("offseason", { name, args });
  if (!r.ok) throw new Error(r.message);
  return r.value;
}

function scoutCell(sc) {
  return `${sc.overall_text}`;
}

async function renderProcedure(token) {
  const r = await call("query", { name: "offseason_view", args: {} });
  if (!r.ok) throw new Error(r.message);
  if (token !== state.token) return;
  const v = r.value;
  const ps = state.proc;
  ps.phase = v.phase;
  ps.stage = v.stage;
  // 段階のタブは 5 つ(契約・FA・ドラフト・市場・完了)。今の段階の名前を大きく出す(D-271)
  $("proc-title").textContent = v.phase_label;
  const at = v.phases.findIndex((x) => x.key === v.stage);
  $("proc-steps").replaceChildren(...v.phases.map((p, i) => el("li", { className: i === at ? "current" : i < at ? "done" : "" }, p.label)));
  renderProcedureBar(v);
  renderAutoLog();
  if (ps.lastOffer) {
    const o = ps.lastOffer;
    $("proc-message").className = o.accepted ? "message ok" : "message ng";
    $("proc-message").textContent = o.accepted ? `${o.name} は受けました(${o.years} 年・${o.salary.toLocaleString()} 万円)。` : `${o.name} に断られました:${o.reason}。${o.released ? "提示の回数を使い切ったので、自由契約になりました。" : ""}`;
  }
  // 答え合わせモードがオンなら、真の総合値も出す(D-206)
  let truth = null;
  if (state.answerLevel > 0) {
    truth = await answer("procedure_answers", {});
    if (token !== state.token) return;
    if (state.answerLevel === 0) truth = null;
  }
  const body = $("proc-body");
  let parts = [];
  if (v.stage === "contract") parts = await contractSection(v, token);
  else if (v.phase === "fa") parts = await faSection(v, token);
  else if (v.phase === "draft") parts = poolSection(v, truth);
  else if (v.phase === "market") parts = await marketSection(v, truth, token);
  if (parts === null || token !== state.token) return;
  body.replaceChildren(...parts);
  renderProcedureContracts(v);
  $("proc-history").replaceChildren(historyTable(v.picks, v.released));
  $("proc-history-box").hidden = v.stage === "contract";
  $("proc-history-box").open = false;
}

// 状態バー(D-271):段階ごとの要約・予算・主ボタン。説明の文は置かない(D-270)
function renderProcedureBar(v) {
  const mine = v.my_team;
  const c = v.contracts;
  let info = "";
  let canNext = !state.running;
  if (v.stage === "contract" && v.renewal) {
    const rn = v.renewal;
    const n = rn.roster_counts;
    info = `${rn.players} 人:` + ["unoffered", "refused", "accepted", "declared", "released", "contracted"].filter((k) => n[k]).map((k) => `${rn.status_labels[k]} ${n[k]}`).join("・");
    canNext = canNext && rn.can_next;
  } else if (v.stage === "fa" && v.fa) {
    const f = v.fa;
    info = f.done ? `FA 終了:宣言 ${f.counts.declared}・契約 ${f.counts.signed}` : `ラウンド ${f.round}/${f.rounds}:宣言 ${f.counts.declared}・契約 ${f.counts.signed}・空き枠 ${f.space ?? "-"}`;
  } else if (v.stage === "draft" || v.stage === "market") {
    const turn = v.phase_finished ? "終了" : v.is_my_turn ? "あなたの番" : `${(v.order.find((o) => o.team_id === v.current_team) || {}).team_name || ""} の番`;
    info = `${Math.min(v.round, v.total_rounds)} / ${v.total_rounds} 巡・${turn}・${v.stage === "draft" ? "候補" : "市場"} ${v.stage === "draft" ? v.counts.candidates : v.counts.market} 人${mine ? `・${mine.players}/${mine.max} 人` : ""}`;
  }
  $("proc-info").textContent = info;
  const b = c && c.mine;
  const budget = $("proc-budget");
  if (b) {
    const over = b.hard && b.over_now > 0;
    budget.className = over ? "warn" : "";
    budget.textContent = b.cap ? `総年俸 ${b.total_text}(${b.hard ? "上限" : "目安"}の ${b.usage}%)${over ? `・上限を ${b.over_now.toLocaleString()} 万円超過` : ""}` : `総年俸 ${b.total_text}`;
  } else {
    budget.textContent = "";
  }
  $("proc-next").disabled = !canNext;
  $("proc-stage-auto").disabled = state.running;
  // 契約の段階は「自動案でまとめて更改」もバーに置く(D-272)
  const acts = $("proc-actions");
  const old = $("renew-auto");
  if (old) old.remove();
  if (v.stage === "contract" && v.renewal) {
    const rn = v.renewal;
    acts.prepend(el("button", { id: "renew-auto", type: "button", className: "secondary", title: "自動案でまとめて更改(未提示の全員に、1 年・算定した年俸で提示)", disabled: rn.unoffered === 0 || state.running, onclick: () => renewAuto() }, rn.unoffered ? `自動案で更改(${rn.unoffered})` : "自動案は提示済み"));
  }
}

// 「この段階をおまかせ」の結果:AI が自球団の分として行ったことの一覧(D-271)
function renderAutoLog() {
  const box = $("proc-autolog");
  const log = state.proc.autoLog;
  if (!log) { box.replaceChildren(); return; }
  const items = log.items;
  box.replaceChildren(el("details", { id: "auto-log" }, el("summary", {}, `おまかせの結果(${log.stage_label}):${items.length ? `${items.length} 件` : "自球団の動きなし"}`),
    items.length ? el("ul", { className: "offer-history" }, ...items.map((x) => el("li", {}, x.text))) : el("p", { className: "muted small" }, "なし")));
}

// 契約更改の結果(全球団。契約の段階の表の下に、閉じた形で出す)
function renderProcedureContracts(v) {
  const c = v.contracts;
  const box = $("proc-contracts");
  if (!c || v.stage !== "contract") { box.replaceChildren(); return; }
  const parts = [];
  if (c.renewals.length || c.budget_releases.length || c.negotiation_releases.length) {
    const mineFirst = (rows) => [...rows].sort((a, b) => (b.is_mine ? 1 : 0) - (a.is_mine ? 1 : 0));
    const renewCols = [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "old", label: "前の年俸" }, { key: "salary", label: "新しい年俸" }, { key: "change", label: "増減" }, { key: "years", label: "年数" }, { key: "offers", label: "提示", description: "受けるまでに提示した回数" }];
    const renewRows = mineFirst(c.renewals).map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, old: r.old_text || "-", salary: r.salary_text, change: r.change === null ? "-" : `${r.change >= 0 ? "+" : ""}${r.change.toLocaleString()}`, years: `${r.years} 年`, offers: r.offers ? `${r.offers} 回` : "-" } }));
    const det = el("details", { id: "renewal-results" }, el("summary", {}, `全球団の更改の結果(更改 ${c.renewals.length} 人${c.negotiation_releases.length ? `・交渉決裂 ${c.negotiation_releases.length} 人` : ""}${c.budget_releases.length ? `・予算超過 ${c.budget_releases.length} 人` : ""}・単価 ${c.rate_text})`));
    if (c.renewals.length) det.append(table({ firstLabel: "選手", columns: renewCols, rows: renewRows, first: (r) => [playerLink(r.name, r.player_id)], rowClass: (r) => (r.is_mine ? "mine" : ""), fluid: true, limit: state.proc.renewalsShown || 20, more: () => { state.proc.renewalsShown = (state.proc.renewalsShown || 20) + 50; renderCurrent(); } }));
    if (c.negotiation_releases.length) det.append(el("h3", {}, "交渉が決裂して自由契約"), table({ firstLabel: "選手", columns: [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "reason", label: "最後の理由" }, { key: "offers", label: "提示" }], rows: mineFirst(c.negotiation_releases).map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, reason: r.reason || "(提示せず)", offers: `${r.offers} 回` } })), first: (r) => [el("span", {}, r.name)], rowClass: (r) => (r.is_mine ? "mine" : ""), fluid: true, limit: 20 }));
    if (c.budget_releases.length) det.append(el("h3", {}, "予算超過で自由契約(AI 球団)"), table({ firstLabel: "選手", columns: [{ key: "team", label: "球団" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "salary", label: "年俸" }], rows: c.budget_releases.map((r) => ({ ...r, values: { team: r.team_name, position: r.position_label, age: `${r.age}歳`, salary: r.salary_text } })), first: (r) => [el("span", {}, r.name)], fluid: true }));
    parts.push(det);
  }
  box.replaceChildren(...parts);
}

function ceilingText(g) {
  return { S: "S(上位 5%)", A: "A", B: "B", C: "C", D: "D" }[g] || g;
}

// ---- 自由契約・市場の、成績つきの選手の一覧(D-222)。表の部品(table・sortToggle)と個人成績と同じ並び順の仕組みを使う ----

const POSITION_GROUPS = {
  batter: [["", "すべての野手"], ["C", "捕手"], ["IF", "内野手"], ["OF", "外野手"]],
  pitcher: [["", "すべての投手"], ["SP", "先発"], ["RP", "救援"]],
};
const SCOUT_SORT_KEYS = ["overall", "ceiling"]; // 評価の列は計算本体の表にないので、画面側で並べる

function inGroup(position, group) {
  if (!group) return true;
  if (group === "IF") return ["1B", "2B", "3B", "SS"].includes(position);
  if (group === "OF") return ["LF", "CF", "RF"].includes(position);
  return position === group;
}

// 成績つきの表を計算本体から受け取る。能力(答え合わせモード)のときだけ answer を呼ぶ
async function fetchRosterTable(phase) {
  const rt = state.rosterTable;
  rt.phase = phase;
  if (rt.kinds[phase] === "ability" && state.answerLevel === 0) rt.kinds[phase] = "basic";
  const kind = rt.kinds[phase];
  const sortBy = rt.sortBy[rt.role];
  const abilityKeys = state.answerLevel > 0 ? (await answer("ability_columns", { role: rt.role })).map((c) => c.key) : [];
  // 能力の項目で並べていたときは、成績の表ではその表の既定に戻す(個人成績と同じ)。評価の列は画面側で並べる
  const usable = sortBy.key && !SCOUT_SORT_KEYS.includes(sortBy.key) && (kind === "ability" || !abilityKeys.includes(sortBy.key));
  const args = { phase, role: rt.role, kind, sort: usable ? sortBy.key : null, order: usable ? sortBy.order : null, season: rt.season };
  const data = kind === "ability" ? await answer("offseason_ability_table", { ...args, level: state.answerLevel }) : await query("offseason_table", args);
  rt.shownSort[rt.role] = { key: data.sort.key, order: data.order };
  return data;
}

// 切り替え(野手・投手 / 基本・セイバー・WAR・能力 / シーズン / ポジション)
function rosterControls(data) {
  const rt = state.rosterTable;
  const seg = (id, items, value, onPick) => {
    const box = el("div", { className: "seg", id, role: "group" });
    for (const [k, label] of items) box.append(el("button", { type: "button", "aria-pressed": String(k === value), dataset: { value: k }, onclick: () => onPick(k) }, label));
    return box;
  };
  const kinds = [["basic", "基本"], ["saber", "セイバー"], ["war", "WAR"]];
  if (state.answerLevel > 0) kinds.push(["ability", "能力"]);
  const out = [
    seg("roster-role", [["batter", "野手"], ["pitcher", "投手"]], rt.role, (k) => { rt.role = k; rt.group = ""; renderCurrent(); }),
    seg("roster-kind", kinds, rt.kinds[rt.phase], (k) => { rt.kinds[rt.phase] = k; renderCurrent(); }),
  ];
  const filters = el("div", { className: "filters" });
  if (data.seasons.length > 1) {
    filters.append(el("select", { id: "roster-season", "aria-label": "シーズンの選択", onchange: (e) => { rt.season = e.target.value; renderCurrent(); } }, ...data.seasons.map((x) => el("option", { value: x.key, selected: x.key === rt.season }, x.label))));
  }
  filters.append(el("select", { id: "roster-group", "aria-label": "ポジションの絞り込み", onchange: (e) => { rt.group = e.target.value; renderCurrent(); } }, ...POSITION_GROUPS[rt.role].map(([k, label]) => el("option", { value: k, selected: k === rt.group }, label))));
  out.push(filters);
  return out;
}

function rosterSortLine(data) {
  const extraNote = data.extra_column ? `並び順に使っている「${data.extra_column.label}」はこの表にない列なので、名前の隣に固定して出しています。` : "";
  return el("p", { className: "muted small", id: "roster-sort-line" }, `並び順:${data.sort.label}(${sortWord(data.sort, data.order)})。見出しを押すと並べ替え、もう一度押すと逆の順になります。${extraNote}`);
}

function rosterSort(key) {
  const rt = state.rosterTable;
  sortToggle(rt.sortBy[rt.role], rt.shownSort[rt.role], key);
  renderCurrent();
}

// ---- 契約更改(F3-2b。D-243〜D-252):自動案のまとめて提示、選手を押して出る提示のパネル、断られた理由 ----

// 契約の画面(契約更改と自由契約をまとめたもの。D-272):自球団の全選手を 1 つの表に。絞り込みはポジションと状態の 2 つ
async function contractSection(v, token) {
  const ps = state.proc;
  const cs = ps.contract;
  if (!v.my_team || !v.renewal) return [el("p", { className: "muted" }, "操作する球団がありません。")];
  const rn = v.renewal;
  const usable = cs.sortBy.key;
  const data = await query("contract_table", { group: cs.group, kind: cs.kind, sort: usable, order: usable ? cs.sortBy.order : null, season: cs.season, status: cs.status });
  if (token !== state.token) return null;
  cs.shown = { key: data.sort.key, order: data.order };
  let prefs = null;
  if (state.answerLevel > 0) {
    prefs = await answer("negotiation_answers", {});
    if (token !== state.token) return null;
  }
  const pick = (id, label, items, value, onPick) => el("select", { id, "aria-label": label, onchange: (e) => onPick(e.target.value) }, ...items.map((x) => el("option", { value: x.key, selected: x.key === value }, x.label)));
  const filters = el("div", { className: "compact-filters" },
    pick("contract-group", "ポジションの絞り込み", data.groups, cs.group, (k) => { cs.group = k; cs.sortBy = { key: null, order: null }; renderCurrent(); }),
    pick("contract-status", "状態の絞り込み", data.statuses, cs.status, (k) => { cs.status = k; renderCurrent(); }));
  if (data.kinds.length) filters.append(pick("contract-kind", "成績の種類", data.kinds, cs.kind, (k) => { cs.kind = k; renderCurrent(); }));
  if (data.seasons.length > 1) filters.append(pick("contract-season", "シーズンの選択", data.seasons.map((x) => ({ key: x.key, label: x.key === "career" ? "通算" : "今季" })), cs.season, (k) => { cs.season = k; renderCurrent(); }));
  const first = (p) => [link(p.name, () => { ps.renewalOpen = ps.renewalOpen === p.player_id ? null : p.player_id; renderCurrent(); }), el("span", { className: "sub" }, p.hand || "")];
  const detail = (p) => (ps.renewalOpen === p.player_id ? contractPanel(p, rn, prefs) : null);
  const onSort = (key) => { sortToggle(cs.sortBy, cs.shown, key); renderCurrent(); };
  const rowClass = (p) => (p.status === "refused" ? "refused" : p.status === "released" || p.status === "declared" ? "muted" : "");
  return [
    filters,
    data.rows.length
      ? table({ firstLabel: "選手", columns: data.columns, rows: data.rows, first, sort: data.sort.key, order: data.order, onSort, rowClass, fluid: true, detail })
      : el("p", { className: "muted", id: "contract-empty" }, "この絞り込みに合う選手はいません。"),
  ];
}

// 年俸の入力欄と「+5%」「+10%」「−5%」のボタン(更改と FA の提示のパネルで共通。D-257)。基準は今の入力値、丸めは step 万円
function salaryInput(value, min, step, max = null) {
  const input = el("input", { id: "offer-salary", type: "number", inputMode: "numeric", min: String(min), step: String(step), value: String(value), "aria-label": "年俸(万円)" });
  if (max) input.max = String(max); // 「なし」は算定の 1.0〜1.3 倍(D-273)
  const bump = (rate) => {
    const now = Number(input.value) || value;
    const next = Math.max(min, Math.round((now * (1 + rate)) / step) * step);
    input.value = String(max ? Math.min(max, next) : next);
  };
  const btn = (label, rate, id) => el("button", { type: "button", className: "secondary pct", id, onclick: () => bump(rate) }, label);
  const wrap = el("span", { className: "salary-input" }, el("label", {}, "年俸 ", input, " 万円"), btn("+5%", 0.05, "offer-plus5"), btn("+10%", 0.1, "offer-plus10"), btn("−5%", -0.05, "offer-minus5"));
  return { wrap, input };
}

// 選手をタップして出るパネル(D-272):交渉中なら 年数・年俸(±%)・提示する・自由契約にする・残りの回数・断られた理由。
// 契約が残る選手(複数年の途中・更改済)は「自由契約にする」だけ(残りの契約は消える)
function contractPanel(p, rn, prefs) {
  const r = p.renewal;
  const box = el("div", { className: "offer-panel", id: "offer-panel", dataset: { playerId: p.player_id } });
  if (r && r.offers.length) {
    box.append(el("ul", { className: "offer-history" }, ...r.offers.map((o, i) => el("li", {}, `${i + 1} 回目:${o.years} 年・${o.salary_text} → ${o.accepted ? "受けた" : `断られた(${o.reason})`}`))));
  }
  const releaseBtn = el("button", { id: "offer-release", type: "button", className: "danger", disabled: state.running || !p.can_release, onclick: () => contractRelease(p) }, "自由契約にする");
  if (r && r.status !== "accepted" && p.status !== "released" && p.status !== "declared" && p.can_offer) {
    const years = el("select", { id: "offer-years", "aria-label": "契約年数" }, ...Array.from({ length: rn.max_years - rn.min_years + 1 }, (_, i) => rn.min_years + i).map((y) => el("option", { value: String(y) }, `${y} 年`)));
    const min = rn.none_max_ratio ? r.auto_salary : rn.minimum_salary;
    const si = salaryInput(r.auto_salary, min, rn.rounding, rn.none_max_ratio ? Math.floor(r.auto_salary * rn.none_max_ratio) : null);
    box.append(el("div", { className: "offer-fields" }, el("label", {}, "年数 ", years), si.wrap));
    if (rn.hard && rn.cap) box.append(el("p", { className: "muted small" }, `見込みの総年俸 ${rn.projected_text} / 上限 ${rn.cap_text}`));
    if (prefs && prefs.players && prefs.players[p.player_id]) {
      const w = prefs.players[p.player_id];
      box.append(el("p", { className: "small answer" }, `志望(答え合わせ):${w.map((x) => `${x.label} ${x.text}${x.active ? "" : "(効かない)"}`).join("・")}`));
    }
    const offerBtn = el("button", { id: "offer-send", type: "button", disabled: state.running, onclick: () => sendOffer(p, years, si.input) }, `提示する(残り ${r.offers_left} 回)`);
    box.append(el("div", { className: "row" }, offerBtn, releaseBtn));
  } else if (p.status === "released" || p.status === "declared") {
    box.append(el("p", { className: "small" }, p.status === "released" ? "自由契約にしました(市場へ)。" : "FA を宣言しました(FA の段階で提示できます)。"));
  } else {
    const c = p.contract;
    if (c) box.append(el("p", { className: "small" }, `契約:${c.salary_text}・残り ${c.remaining} 年`));
    box.append(el("div", { className: "row" }, releaseBtn));
  }
  return box;
}

async function contractRelease(p) {
  if (!confirm(`${p.name} を自由契約にします(残りの契約は消えます。戻せません)。よろしいですか?`)) return;
  state.proc.lastOffer = null;
  state.proc.renewalOpen = null;
  await runProc("contract_release", { player_id: p.player_id });
}

async function renewAuto() {
  state.proc.lastOffer = null;
  await runProc("renew_auto");
}

async function sendOffer(p, yearsEl, salaryEl) {
  const args = { player_id: p.player_id, years: Number(yearsEl.value) };
  if (salaryEl) {
    const v = salaryEl.value.trim();
    if (!/^\d+$/.test(v)) {
      $("proc-message").className = "message ng";
      $("proc-message").textContent = "年俸は整数(万円)で入れてください。";
      return;
    }
    args.salary = Number(v);
  }
  state.proc.lastOffer = null;
  await runProc("offer", args, (view) => { state.proc.lastOffer = view.last_offer || null; });
}

// ---- FA(F3-2c。D-258〜D-264):提示ラウンド制。表・提示のパネル・ラウンドの表示・結果の一覧 ----

async function faSection(v, token) {
  const ps = state.proc;
  const rt = state.rosterTable;
  const fa = v.fa;
  if (!fa) return [el("p", { className: "muted" }, "FA の情報がありません。")];
  const data = await fetchRosterTable("fa");
  if (token !== state.token) return null;
  let prefs = null;
  if (state.answerLevel > 0) {
    prefs = await answer("fa_answers", {});
    if (token !== state.token) return null;
  }
  const c = fa.counts;
  const head = [el("p", { className: "info", id: "fa-round" }, fa.done ? `FA は終わりました(宣言 ${c.declared} 人・契約 ${c.signed} 人・市場へ ${c.unsigned} 人)。「次の手続きへ」でドラフトに進みます。` : `ラウンド ${fa.round}/${fa.rounds}:宣言 ${c.declared} 人・契約 ${c.signed} 人・残り ${c.open} 人`)];
  if (ps.lastRound) {
    const lr = ps.lastRound;
    head.push(el("p", { className: "message ok", id: "fa-last" }, lr.signed.length ? `ラウンド ${lr.round} の結果:${lr.signed.map((x) => `${x.name} → ${x.team_name}(${x.years} 年・${x.salary_text})`).join("、")}` : `ラウンド ${lr.round} の結果:成立した契約はありません。`));
  }
  if (v.my_team && !fa.done) {
    const mine = fa.offers.length ? `提示中 ${fa.offers.length} 人(合計 ${fa.committed_text})` : "まだ提示していません";
    const budget = fa.hard && fa.cap ? `。総年俸 ${fa.total.toLocaleString()} 万円 / 上限 ${fa.cap_text}` : "";
    head.push(el("p", { className: "small", id: "fa-mine" }, `${mine}${budget}。空き枠 ${fa.space} 人。`));
    head.push(el("div", { className: "row" }, el("button", { id: "fa-close", type: "button", disabled: state.running, onclick: () => faClose() }, `ラウンド ${fa.round} を締める(結果を見る)`)));
  }
  const filter = el("select", { id: "fa-filter", "aria-label": "表示する選手", onchange: (e) => { ps.faFilter = e.target.value; renderCurrent(); } },
    el("option", { value: "open", selected: ps.faFilter !== "all" }, "未契約の選手だけ"),
    el("option", { value: "all", selected: ps.faFilter === "all" }, "契約した選手も表示"));
  const controls = rosterControls(data);
  controls[controls.length - 1].prepend(filter);
  const rows = data.rows.filter((r) => inGroup(r.position, rt.group) && (ps.faFilter === "all" || r.fa.status === "open"));
  const first = (p) => [link(p.name, () => { ps.faOpen = ps.faOpen === p.player_id ? null : p.player_id; renderCurrent(); }), el("span", { className: "sub" }, p.hand || "")];
  const detail = (p) => (ps.faOpen === p.player_id && p.fa && p.fa.status === "open" && v.my_team && !fa.done ? faPanel(p, fa, prefs) : null);
  const out = [
    ...head,
    ...controls,
    rows.length
      ? table({ firstLabel: "選手", columns: data.columns, rows, first, sort: data.sort.key, order: data.order, onSort: rosterSort, rowClass: (p) => (p.fa && p.fa.offer ? "selected" : ""), extra: data.extra_column, fluid: true, detail })
      : el("p", { className: "muted", id: "fa-empty" }, "この絞り込みに合う選手はいません。"),
    rosterSortLine(data),
  ];
  if (fa.results.length) {
    out.push(el("details", { id: "fa-results", open: true }, el("summary", {}, `FA の結果(契約 ${fa.results.length} 人)`),
      table({ firstLabel: "選手", columns: [{ key: "round", label: "ラウンド" }, { key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "former", label: "前の所属" }, { key: "team", label: "契約した球団" }, { key: "years", label: "年数" }, { key: "salary", label: "年俸" }, { key: "offers", label: "提示の数" }],
        rows: fa.results.map((x) => ({ ...x, values: { round: String(x.round), position: x.position_label, age: `${x.age}歳`, former: x.former_team_name, team: x.stayed ? `${x.team_name}(残留)` : x.team_name, years: `${x.years} 年`, salary: x.salary_text, offers: `${x.offers} 球団` } })), first: (x) => [playerLink(x.name, x.player_id)], rowClass: (x) => (x.is_mine ? "mine" : ""), fluid: true })));
  }
  out.push(el("p", { className: "muted small", id: "fa-note" }, `${fa.note}名前を押すと提示のパネルが開きます。${data.season_label}の成績です。初期の並び順は算定年俸の高い順。`));
  if (data.terms) out.push(el("details", {}, el("summary", {}, "WAR の用語の解説"), el("dl", { className: "terms" }, ...data.terms.flatMap((t) => [el("dt", {}, t.label), el("dd", {}, t.description)]))));
  return out;
}

function faPanel(p, fa, prefs) {
  const f = p.fa;
  const box = el("div", { className: "offer-panel", id: "fa-panel", dataset: { playerId: p.player_id } });
  box.append(el("p", {}, `前の所属 ${f.former_team_name}。算定年俸 ${f.calc_text}。見込みの WAR ${Number(f.expected).toFixed(2)}。`));
  if (f.offer) box.append(el("p", { className: "small" }, `提示中:${f.offer.years} 年・${Number(f.offer.salary).toLocaleString()} 万円(出し直すと上書き)`));
  const years = el("select", { id: "fa-years", "aria-label": "契約年数" }, ...Array.from({ length: fa.max_years - fa.min_years + 1 }, (_, i) => fa.min_years + i).map((y) => el("option", { value: String(y), selected: f.offer ? f.offer.years === y : y === 1 }, `${y} 年`)));
  const fields = el("div", { className: "offer-fields" }, el("label", {}, "年数 ", years));
  let salary = null;
  if (fa.salary_editable) {
    const si = salaryInput(f.offer ? f.offer.salary : f.calc_salary, fa.minimum_salary, fa.rounding);
    salary = si.input;
    fields.append(si.wrap);
  } else {
    fields.append(el("span", { className: "small" }, `年俸 ${f.calc_text}(お金のルール「なし」では算定どおり)`));
  }
  box.append(fields);
  if (prefs && prefs.players && prefs.players[p.player_id]) {
    const w = prefs.players[p.player_id];
    box.append(el("p", { className: "small answer" }, `志望(答え合わせ):${w.map((x) => `${x.label} ${x.text}${x.active ? "" : "(効かない)"}`).join("・")}`));
  }
  const send = el("button", { id: "fa-send", type: "button", disabled: state.running, onclick: () => faSend(p, years, salary) }, f.offer ? "提示を出し直す" : "提示する");
  const row = el("div", { className: "row" }, send);
  if (f.offer) row.append(el("button", { id: "fa-cancel", type: "button", className: "secondary", disabled: state.running, onclick: () => runProc("fa_cancel", { player_id: p.player_id }) }, "提示を取り消す"));
  box.append(row);
  return box;
}

async function faSend(p, yearsEl, salaryEl) {
  const args = { player_id: p.player_id, years: Number(yearsEl.value) };
  if (salaryEl) {
    const val = salaryEl.value.trim();
    if (!/^\d+$/.test(val)) {
      $("proc-message").className = "message ng";
      $("proc-message").textContent = "年俸は整数(万円)で入れてください。";
      return;
    }
    args.salary = Number(val);
  }
  state.proc.lastRound = null;
  await runProc("fa_offer", args);
}

async function faClose() {
  state.proc.faOpen = null;
  await runProc("fa_close", {}, (view) => { state.proc.lastRound = view.last_round || null; });
}

async function marketSection(v, truth, token) {
  const ps = state.proc;
  const rt = state.rosterTable;
  const turnText = v.phase_finished ? "この段階は終わりました。「次の手続きへ」を押してください。" : v.is_my_turn ? `${v.round} 巡目:あなたの番です。選手の「獲得」を押すか、「パス」してください。` : `${v.round} / ${v.total_rounds} 巡目。今の指名権:${v.order.find((o) => o.team_id === v.current_team)?.team_name ?? "-"}`;
  const head = [el("p", { className: "info" }, turnText)];
  const controls = el("div", { className: "row" });
  if (!v.phase_finished && !v.is_my_turn && v.my_team) controls.append(el("button", { onclick: () => runProc("advance") }, "次の自分の番まで進める"));
  if (v.is_my_turn) controls.append(el("button", { className: "secondary", onclick: () => runProc("pass") }, "パス(指名しない)"));
  head.push(controls);
  const data = await fetchRosterTable("market");
  if (token !== state.token) return null;
  const scout = Object.fromEntries(v.pool.map((p) => [p.player_id, p]));
  const grade = { S: 0, A: 1, B: 2, C: 3, D: 4 };
  const sortBy = rt.sortBy[rt.role];
  let rows = data.rows.filter((r) => inGroup(r.position, rt.group)).map((r) => {
    const sc = scout[r.player_id]?.scouting;
    const t = truth ? truth.players[r.player_id] : null;
    return { ...r, scouting: sc, values: { ...r.values, overall: sc ? sc.overall_text : "", ceiling: sc ? ceilingText(sc.ceiling) : "", former: r.former_team || "-", truth: t ? `${t.overall}${t.potential ? ` / ${t.potential}` : ""}` : "" } };
  });
  let sortKey = data.sort.key;
  let order = data.order;
  if (sortBy.key && SCOUT_SORT_KEYS.includes(sortBy.key) && rows.every((r) => r.scouting)) {
    sortKey = sortBy.key;
    order = sortBy.order || "desc";
    const value = (r) => (sortKey === "overall" ? r.scouting.overall : -grade[r.scouting.ceiling]);
    rows.sort((a, b) => a.player_id.localeCompare(b.player_id));
    rows.sort((a, b) => (order === "desc" ? value(b) - value(a) : value(a) - value(b)));
    rt.shownSort[rt.role] = { key: sortKey, order };
  }
  const columns = [
    { key: "overall", label: "総合(推定 ± 幅)", description: "自球団のスカウトの推定値と、真の値が約80%の確率で入る幅", type: "metric", better: "high" },
    { key: "ceiling", label: "天井", description: "潜在能力の見立て(S〜D)", type: "text", better: "high" },
    { key: "former", label: "前の球団", description: "手放した球団(指名されなかった候補は「-」)" },
    ...data.columns,
  ];
  if (truth) columns.push({ key: "truth", label: "真の総合", description: "答え合わせ:真の今の総合値" + (truth.level === 2 ? " / 潜在能力" : "") });
  if (v.is_my_turn) columns.push({ key: "pick", label: "" });
  for (const r of rows) if (v.is_my_turn) r.values.pick = r.offer && !r.offer.affordable ? el("span", { className: "muted small" }, "予算不足") : el("button", { className: "pick-btn", type: "button", onclick: () => runProc("pick", { player_id: r.player_id }) }, "獲得");
  const sortCol = SCOUT_SORT_KEYS.includes(sortKey) ? columns.find((c) => c.key === sortKey) : data.sort;
  const first = (p) => [link(p.name, () => { ps.open = ps.open === p.player_id ? null : p.player_id; renderCurrent(); }), el("span", { className: "sub" }, p.hand || "")];
  const detail = (p) => (ps.open === p.player_id && p.scouting ? `項目別の推定値 ± ふれ幅:${p.scouting.items.map((i) => `${i.label} ${i.text}`).join(" / ")}` : null);
  const tableEl = rows.length
    ? table({ firstLabel: "選手", columns, rows, first, sort: sortKey, order, onSort: rosterSort, rowClass: (p) => (ps.open === p.player_id ? "selected" : ""), extra: data.extra_column, fluid: true, detail })
    : el("p", { className: "muted" }, "この絞り込みに合う選手はいません。");
  return [
    ...head,
    ...rosterControls(data),
    el("p", { className: "muted small" }, `市場の選手 ${rows.length} 人。名前を押すと項目別の推定値が出ます。`),
    tableEl,
    rosterSortLine({ ...data, sort: sortCol, order }),
    el("p", { className: "muted small" }, `${data.kind === "ability" ? data.note + "。" : `${data.season_label}の成績です。`}指名されなかった候補は成績がないので「—」。表は狭い画面では横にずらせ、広い画面では全列が出ます。`),
  ];
}

function poolSection(v, truth) {
  const ps = state.proc;
  const isDraft = v.phase === "draft";
  const turnText = v.phase_finished ? "この段階は終わりました。「次の手続きへ」を押してください。" : v.is_my_turn ? `${v.round} 巡目:あなたの番です。選手の「指名」を押すか、「パス」してください。` : `${v.round} / ${v.total_rounds} 巡目。今の指名権:${v.order.find((o) => o.team_id === v.current_team)?.team_name ?? "-"}`;
  const head = [el("p", { className: "info" }, turnText)];
  const controls = el("div", { className: "row" });
  if (!v.phase_finished && !v.is_my_turn && v.my_team) controls.append(el("button", { onclick: () => runProc("advance") }, "次の自分の番まで進める"));
  if (v.is_my_turn) controls.append(el("button", { className: "secondary", onclick: () => runProc("pass") }, "パス(指名しない)"));
  head.push(controls);
  // 並べ替えと絞り込み
  const positions = [...new Set(v.pool.map((p) => p.position))];
  const posLabel = Object.fromEntries(v.pool.map((p) => [p.position, p.position_label]));
  const filters = el("div", { className: "filters" },
    el("select", { "aria-label": "並び順", onchange: (e) => { ps.sort = e.target.value; renderCurrent(); } }, ...[["overall", "総合の推定値が高い順"], ["ceiling", "天井が高い順"], ["age", "年齢が若い順"], ["position", "ポジション順"]].map(([k, l]) => el("option", { value: k, selected: ps.sort === k }, l))),
    el("select", { "aria-label": "ポジションの絞り込み", onchange: (e) => { ps.position = e.target.value; renderCurrent(); } }, el("option", { value: "", selected: ps.position === "" }, "すべてのポジション"), ...positions.map((p) => el("option", { value: p, selected: ps.position === p }, posLabel[p]))),
  );
  head.push(filters);
  const grade = { S: 0, A: 1, B: 2, C: 3, D: 4 };
  const order = Object.keys(posLabel);
  let rows = v.pool.filter((p) => !ps.position || p.position === ps.position);
  rows.sort((a, b) => a.player_id.localeCompare(b.player_id));
  if (ps.sort === "overall") rows.sort((a, b) => b.scouting.overall - a.scouting.overall);
  else if (ps.sort === "ceiling") rows.sort((a, b) => grade[a.scouting.ceiling] - grade[b.scouting.ceiling] || b.scouting.overall - a.scouting.overall);
  else if (ps.sort === "age") rows.sort((a, b) => a.age - b.age || b.scouting.overall - a.scouting.overall);
  else rows.sort((a, b) => order.indexOf(a.position) - order.indexOf(b.position) || b.scouting.overall - a.scouting.overall);
  const columns = [{ key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "overall", label: "総合(推定 ± 幅)", description: "自球団のスカウトの推定値と、真の値が約80%の確率で入る幅" }, { key: "ceiling", label: "天井", description: "潜在能力の見立て(S〜D)" }];
  if (!isDraft) columns.push({ key: "former", label: "前の球団" });
  if (truth) columns.push({ key: "truth", label: "真の総合", description: "答え合わせ:真の今の総合値" + (truth.level === 2 ? " / 潜在能力" : "") });
  if (v.is_my_turn) columns.push({ key: "pick", label: "" });
  const tbody = el("tbody");
  const thead = el("tr", {}, el("th", { className: "sticky", scope: "col" }, "選手"), ...columns.map((c) => el("th", { scope: "col", title: c.description || "" }, c.label)));
  for (const p of rows) {
    const t = truth ? truth.players[p.player_id] : null;
    const cells = { position: p.position_label, age: `${p.age}歳${p.origin ? `・${p.origin}` : ""}`, overall: p.scouting.overall_text, ceiling: ceilingText(p.scouting.ceiling), former: p.former_team || "-", truth: t ? `${t.overall}${t.potential ? ` / ${t.potential}` : ""}` : "" };
    const tr = el("tr", { className: ps.open === p.player_id ? "selected" : "" }, el("td", { className: "sticky name-cell" }, link(p.name, () => { ps.open = ps.open === p.player_id ? null : p.player_id; renderCurrent(); }), el("span", { className: "sub" }, p.hand || "")));
    for (const c of columns) {
      if (c.key === "pick") tr.append(el("td", {}, el("button", { className: "pick-btn", onclick: () => runProc("pick", { player_id: p.player_id }) }, isDraft ? "指名" : "獲得")));
      else tr.append(el("td", {}, cells[c.key] ?? ""));
    }
    tbody.append(tr);
    if (ps.open === p.player_id) {
      const items = p.scouting.items.map((i) => `${i.label} ${i.text}`).join(" / ");
      tbody.append(el("tr", { className: "detail" }, el("td", { colSpan: columns.length + 1 }, `項目別の推定値 ± ふれ幅:${items}`)));
    }
  }
  const tableEl = el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, thead), tbody));
  return [...head, el("p", { className: "muted small" }, `${isDraft ? "候補" : "市場の選手"} ${rows.length} 人。名前を押すと項目別の推定値が出ます。表は横にずらせます。`), tableEl];
}

function historyTable(picks, released) {
  const rows = [];
  for (const x of released) rows.push({ k: `自由契約`, team: x.team_name, name: x.name, pos: x.position_label, age: x.age, mine: x.is_mine });
  for (const x of picks) rows.push({ k: `${x.phase === "draft" ? "ドラフト" : "市場"} ${x.round} 巡`, team: x.team_name, name: x.player_id ? x.name : `(${x.note === "full" ? "空き枠なし" : "見送り"})`, pos: x.position_label, age: x.age, mine: x.is_mine });
  if (!rows.length) return el("p", { className: "muted small" }, "まだありません。");
  const body = el("tbody", {}, ...rows.map((r) => el("tr", { className: r.mine ? "mine" : "" }, el("td", {}, r.k), el("td", {}, r.team), el("td", {}, r.name), el("td", {}, r.pos || ""), el("td", {}, r.age ? `${r.age}歳` : ""))));
  return el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "手続き"), el("th", {}, "球団"), el("th", {}, "選手"), el("th", {}, "ポジション"), el("th", {}, "年齢"))), body));
}

async function runProc(name, args = {}, onResult = null) {
  if (state.running) return;
  state.running = true;
  $("proc-message").className = "message";
  $("proc-message").textContent = "";
  if (name !== "stage_auto") state.proc.autoLog = null;
  try {
    const r = await procCall(name, args);
    if (onResult) onResult(r);
    state.proc.selected = new Set();
    state.cache.clear();
    setDirty(true);
    if (r.finished) {
      state.view = r.view_all;
      state.stats.season = "current";
      state.gamesDay = null;
      buildStatsFilters();
      const s = r.summary;
      state.pages = [];
      showScreen("progress");
      renderProgress();
      $("progress-message").className = "message ok";
      $("progress-message").textContent = `オフの手続きが終わり、${s.next_year}シーズン目が始まりました(引退 ${s.counts.retired}人・入団 ${s.counts.rookies}人)。`;
      openPage("offseason", { year: s.year });
      return;
    }
    renderCurrent();
  } catch (err) {
    $("proc-message").className = "message ng";
    $("proc-message").textContent = `操作できませんでした:${err.message}`;
  } finally {
    state.running = false;
  }
}

// 「全部おまかせ」(メニューの中。D-271):残りの手続きを最後まで AI の方針で。やり直せないので確認し、未保存なら保存を案内する
async function procAuto() {
  const unsaved = state.dirty ? "\n\n未保存の変更があります。先に「保存」しておくと、あとでこの時点からやり直せます。" : "";
  if (!confirm(`残りのオフの手続きを全部、AI の方針で(自分の球団の判断も含めて)最後まで進め、次のシーズンを始めます。やり直せません。よろしいですか?${unsaved}`)) return;
  if (current().name === "settings") back();
  await runProc("auto");
}

// 「この段階をおまかせ」(主ボタン。D-271):今の段階だけを AI の方針で進め、AI が自球団の分として行ったことの一覧を出して、次の段階の入口で止まる
async function procStageAuto() {
  state.proc.lastOffer = null;
  state.proc.renewalOpen = null;
  await runProc("stage_auto", {}, (r) => { state.proc.autoLog = r.auto_log || null; });
}

// 「次の手続きへ」(D-271):自分の操作を終えて進む(AI の代行はしない)
async function procNext() {
  const stage = state.proc.stage;
  const ask = { fa: "FA の残りのラウンドでは、自球団は追加の提示をしません(今のラウンドの提示は有効)。次へ進みますか?", draft: "ドラフトの自球団の残りの番は、すべてパスします。次へ進みますか?", market: "市場の自球団の残りの番は、すべてパスして、手続きを完了します。よろしいですか?" }[stage];
  if (ask && !confirm(ask)) return;
  state.proc.lastOffer = null;
  state.proc.autoLog = null;
  await runProc("next");
}

// 年度別の成績(過去シーズン・今シーズン・通算。F2。D-182)。種類の切り替え(基本・セイバー・WAR)は上の表と同じ
function renderPlayerHistory(data) {
  const h = data.history;
  const box = $("player-history-box");
  if (!h || !h.rows.length || state.playerKind === "ability") {
    box.hidden = true;
    $("player-history").replaceChildren();
    return;
  }
  box.hidden = false;
  const kind = state.playerKind;
  const columns = kind === "war" ? h.war_columns : h.columns[kind];
  const rows = h.rows.map((r) => ({ ...r, values: kind === "war" ? r.war || {} : r.tables[kind] }));
  $("player-history").replaceChildren(
    table({
      firstLabel: "シーズン",
      columns,
      rows,
      first: (r) => [el("span", {}, r.season === "career" ? "通算" : r.season === "current" ? `${r.year}(進行中)` : String(r.year)), el("span", { className: "sub" }, r.season === "career" ? "" : `${r.age}歳 ${r.team_name} ${r.position}`)],
      rowClass: (r) => (r.season === "career" ? "career" : ""),
    }),
  );
  $("player-history-note").textContent = h.note;
}

// ---- 年度の確定(F2。D-185)と、オフの結果 ----

async function renderYearEnd(token) {
  const r = await call("query", { name: "year_end_preview", args: {} });
  if (!r.ok) throw new Error(r.message);
  if (token !== state.token) return;
  const d = r.value;
  $("yearend-title").textContent = `${d.year}シーズン目を終えて、${d.year + 1}シーズン目に進みます。`;
  $("yearend-champions").replaceChildren(...d.champions.map((c) => el("p", {}, `${c.league_name} 優勝:${c.teams.join("・")}`)));
  $("yearend-note").textContent = d.note;
  $("yearend-dirty").textContent = d.dirty ? "今のゲームには、未保存の変更があります。確定の前の状態を残しておきたいときは、先に保存してください(確定したあとの保存とは、別のファイルになります)。" : "今のゲームは保存済みです(確定したあとに保存すると、別のファイルになります)。";
  $("yearend-go").disabled = !d.is_over || state.running;
  $("yearend-message").textContent = "";
}

async function yearEnd() {
  if (state.running || !state.view || !state.view.status.can_year_end) return;
  state.running = true;
  $("yearend-go").disabled = true;
  $("yearend-message").className = "message";
  $("yearend-message").textContent = "年度を確定しています(集計を履歴に残し、選手の年齢・能力・引退・新人を決めています)…";
  try {
    const r = await call("yearEnd", {});
    if (!r.ok) throw new Error(r.message);
    state.view = r.value.view;
    state.cache.clear();
    state.stats.season = "current";
    state.gamesDay = null;
    setDirty(true);
    buildStatsFilters();
    const s = r.value.summary;
    state.pages = [];
    showScreen("progress");
    renderProgress();
    if (state.view.status.offseason) {
      // 操作する球団があるときは、オフの手続き(自由契約 → ドラフト → 市場)へ(F3-1)
      $("progress-message").className = "message ok";
      $("progress-message").textContent = `${s.year}シーズン目を確定しました(引退 ${s.counts.retired}人)。オフの手続きを進めてください。`;
      state.proc = freshProc();
      openPage("procedure");
    } else {
      $("progress-message").className = "message ok";
      $("progress-message").textContent = `${s.year}シーズン目を確定し、${s.next_year}シーズン目が始まりました(引退 ${s.counts.retired}人・新人 ${s.counts.rookies}人)。`;
      openPage("offseason", { year: s.year });
    }
  } catch (err) {
    $("yearend-message").className = "message ng";
    $("yearend-message").textContent = `年度を確定できませんでした:${err.message}`;
  } finally {
    state.running = false;
    if (current().name === "yearend") $("yearend-go").disabled = !state.view.status.can_year_end;
  }
}

function playerTable(rows, withOrigin) {
  const columns = [{ key: "position", label: "ポジション" }, { key: "age", label: "年齢" }].concat(withOrigin ? [{ key: "origin", label: "出身" }] : []);
  return table({
    firstLabel: "選手",
    columns,
    rows: rows.map((r) => ({ ...r, values: { position: r.position, age: `${r.age}歳`, origin: r.origin || "" } })),
    first: (r) => [playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)],
    rowClass: (r) => (r.is_mine ? "mine" : ""),
  });
}

async function renderOffseason(args, token) {
  const d = await query("offseason_summary", { year: args.year ?? null });
  if (token !== state.token) return;
  if (!d.available) {
    $("offseason-note").textContent = "まだ年度を確定していません。";
    for (const id of ["offseason-retired", "offseason-rookies", "offseason-answers"]) $(id).replaceChildren();
    return;
  }
  $("offseason-title").textContent = `オフの結果(${d.year}シーズン目の終わり)`;
  $("offseason-note").textContent = d.note;
  $("offseason-counts").textContent = `引退 ${d.counts.retired}人 / 新人 ${d.counts.rookies}人 / 選手の数 ${d.counts.players}人(変わりません)`;
  $("offseason-retired").replaceChildren(d.retired.length ? playerTable(d.retired, false) : el("p", { className: "muted" }, "引退した選手はいません。"));
  $("offseason-rookies").replaceChildren(d.rookies.length ? playerTable(d.rookies, true) : el("p", { className: "muted" }, "入団した新人はいません。"));
  const box = $("offseason-answers");
  if (state.answerLevel === 0) {
    box.replaceChildren(el("p", { className: "info" }, "答え合わせモードをオンにすると、残った選手の能力の増減が見られます(上の「メニュー」から)。"));
    return;
  }
  const a = await answer("offseason_answers", { year: d.year });
  if (token !== state.token || state.answerLevel === 0) return;
  const columns = [{ key: "age", label: "年齢" }, { key: "mean_change", label: "平均の増減", description: "能力の項目ごとの増減の平均" }, { key: "items", label: "項目ごと", description: "項目名と増減" }];
  box.replaceChildren(
    el("p", { className: "muted small" }, a.note),
    table({
      firstLabel: "選手",
      columns,
      rows: a.players.map((r) => ({ ...r, values: { age: `${r.age}歳`, mean_change: r.mean_change, items: r.items.map((i) => `${i.label}${i.change}`).join(" ") } })),
      first: (r) => [playerLink(r.name, r.player_id), el("span", { className: "sub" }, r.team_name)],
      rowClass: (r) => (r.is_mine ? "mine" : ""),
      limit: args.answersShown || STATS_PAGE,
      more: () => { args.answersShown = (args.answersShown || STATS_PAGE) + STATS_PAGE; renderCurrent(); },
    }),
  );
}

// ---- チームのページ ----

async function renderTeam(args, token) {
  const d = await query("team", { team_id: args.id });
  if (token !== state.token) return;
  $("team-name").textContent = d.name + (d.is_mine ? " ★" : "");
  $("team-info").replaceChildren(`${d.league_name} ${d.rank}位 / 本拠地:`, link(d.stadium, () => openPage("stadium", { id: d.team_id })));
  const r = d.record;
  $("team-record").replaceChildren(
    kvTable([
      { label: "試合", values: [String(r.games)] },
      { label: "勝・敗・分", values: [`${r.wins}勝 ${r.losses}敗 ${r.ties}分`] },
      { label: "勝率", description: "勝 ÷(勝 + 敗)。引き分けは数えない", values: [r.pct] },
      { label: "ゲーム差", description: "首位のチームとの差。首位は「-」", values: [r.games_behind] },
      { label: "得点", description: "チームが取った点の合計", values: [String(r.runs)] },
      { label: "失点", description: "チームが取られた点の合計", values: [String(r.runs_allowed)] },
    ]),
  );
  const w = d.war;
  $("team-war").replaceChildren(
    kvTable([
      { label: "野手の WAR", description: "チームの野手の WAR の合計", values: [w.batters] },
      { label: "投手の WAR(失点版)", description: "チームの投手の WAR(失点版)の合計", values: [w.pitchers_ra] },
      { label: "投手の WAR(FIP 版)", description: "チームの投手の WAR(FIP 版)の合計", values: [w.pitchers_fip] },
      { label: "合計(野手 + 投手の失点版)", description: "控え選手だけのチーム(勝率 .290 ほど)に比べて、何勝分多いか", values: [w.total_ra] },
    ]),
  );
  $("team-war-note").textContent = w.note;
  $("team-budget").replaceChildren(...budgetLines(d.budget));
  $("team-salaries").replaceChildren(
    d.salaries.length
      ? table({ firstLabel: "選手", columns: [{ key: "position", label: "ポジション" }, { key: "age", label: "年齢" }, { key: "salary", label: "年俸(万円)" }, { key: "remaining", label: "残り", description: "残りの契約年数(今シーズンを含む)" }], rows: d.salaries.map((r) => ({ ...r, values: { position: r.position, age: `${r.age}歳`, salary: r.salary_text, remaining: `${r.remaining} 年` } })), first: (r) => [playerLink(r.name, r.player_id)], fluid: true, limit: d.salariesShown || 20, more: () => { d.salariesShown = (d.salariesShown || 20) + 50; renderCurrent(); } })
      : el("p", { className: "muted" }, "契約の情報がありません。"),
  );
  $("team-salaries-note").textContent = "年俸の高い順。年俸は架空の「万円」で、見える情報(成績とスカウト評価)から算定した値です。" + (d.budget.cap ? "予算は支配下 70 人の総年俸の枠で、上限(目安)は基準予算の 1.10 倍です。" : "お金のルールが「なし」なので、予算はありません。");
  $("team-war-terms").replaceChildren(...w.terms.flatMap((c) => [el("dt", {}, c.label), el("dd", {}, c.description)]));
  const cols = [
    { key: "position", label: "ポジション" },
    { key: "age", label: "年齢" },
    { key: "hand", label: "投打" },
    { key: "games", label: "出場" },
  ];
  const rows = (role) => d.players.filter((p) => p.role === role).map((p) => ({ ...p, values: { position: p.position, age: String(p.age), hand: p.hand || "", games: String(p.games) } }));
  const first = (p) => [playerLink(p.name, p.player_id)];
  $("team-pitchers").replaceChildren(table({ firstLabel: "選手", columns: cols, rows: rows("pitcher"), first }));
  $("team-batters").replaceChildren(table({ firstLabel: "選手", columns: cols, rows: rows("batter"), first }));
}

// 総年俸と予算の表示(F3-2a。D-231):なしは総年俸だけ、ゆるいは目安、標準・きびしいは上限
function budgetLines(b) {
  const lines = [el("div", {}, el("span", { className: "big" }, `総年俸 ${b.total_text}`), b.tier_label ? el("span", { className: "sub" }, `予算の格差:${b.tier_label}`) : null)];
  if (b.cap) {
    const over = b.over > 0;
    lines.push(
      el("div", {}, `${b.hard ? "予算の上限" : "予算の目安"} ${b.cap_text}(使用率 ${b.usage}%)`),
      el("div", { className: over ? "budget-bar over" : "budget-bar" }, el("span", { style: `width: ${Math.min(100, b.usage)}%` })),
      over ? el("div", { className: "warn" }, `${b.hard ? "上限" : "目安"}を ${b.over.toLocaleString()} 万円超えています${b.hard ? "" : "(ゆるいなので契約は結べます)"}`) : null,
    );
  }
  lines.push(el("dl", {}, el("dt", {}, `お金のルール:${b.rule_label}`), el("dd", {}, b.rate ? `年俸の単価は 1 WAR あたり約 ${(b.rate / 10000).toFixed(2)} 億円(毎年のオフにリーグの環境から求め直します)。` : "")));
  return lines.filter((x) => x !== null);
}

// ---- 球場のページ(公開用の結果と、答え合わせモードでの真の倍率。D-138) ----

async function renderStadium(args, token) {
  const d = await query("stadium", { team_id: args.id });
  if (token !== state.token) return;
  $("stadium-name").textContent = d.name;
  $("stadium-info").replaceChildren("本拠地のチーム:", teamLink(d.team_name, d.team_id), d.is_mine ? " ★" : "", ` / ${d.league_name}`);
  $("stadium-record").replaceChildren(
    kvTable([
      { label: "試合", description: "この球場で行った試合の数", values: [String(d.games)] },
      { label: "本塁打", description: "この球場で出た本塁打の数(両チームの合計)", values: [String(d.home_runs)] },
      { label: "本塁打/試合", description: "1試合あたりの本塁打(両チームの合計)", values: [d.home_runs_per_game] },
      { label: "得点/試合", description: "1試合あたりの得点(両チームの合計)", values: [d.runs_per_game] },
    ]),
  );
  $("stadium-note").textContent = d.note;
  const cmpCols = [
    { key: "home", label: "本拠地" },
    { key: "away", label: "アウェイ" },
    { key: "ratio", label: "比" },
  ];
  $("stadium-compare").replaceChildren(
    table({
      firstLabel: "項目",
      columns: cmpCols,
      rows: ["home_run", "babip", "runs"].map((k) => ({ key: k, label: k === "home_run" ? "本塁打/打席" : k === "runs" ? "得点/打席" : "BABIP", values: d.this_season[k] })),
      first: (r) => [r.label],
    }),
  );
  if (d.estimate) {
    $("stadium-estimate").replaceChildren(
      kvTable([
        { label: "得点", description: "1打席あたりの得点の出やすさ(本塁打と BABIP の推定から組み立てた値。wRC+・OPS+ の球場補正に使う)", values: [d.estimate.runs] },
        { label: "本塁打", description: "本塁打の出やすさ(表示用)", values: [d.estimate.home_run] },
        { label: "BABIP(参考値)", description: "インプレーの打球が安打になりやすさ(参考値。運のぶれに埋もれやすい)", values: [d.estimate.babip] },
      ]),
    );
  } else {
    $("stadium-estimate").replaceChildren(el("p", { className: "info" }, d.estimate_note));
  }
  $("stadium-estimate-note").textContent = d.estimate ? d.estimate_note : "";
  const box = $("stadium-answers");
  if (state.answerLevel === 0) {
    box.replaceChildren(el("p", { className: "info" }, "答え合わせモードをオンにすると見られます(上の「メニュー」から)。"));
    return;
  }
  const a = await answer("stadium_answers", { team_id: args.id });
  if (token !== state.token || state.answerLevel === 0) return;
  box.replaceChildren(
    kvTable([
      { label: "本塁打の倍率", description: "この球場で本塁打が出やすい度合い。1.000 が平均", values: [a.home_run.text] },
      { label: "BABIP の倍率", description: "この球場でインプレーの打球が安打になりやすい度合い。1.000 が平均", values: [a.babip.text] },
    ]),
    el("p", { className: "muted small" }, a.note),
  );
}

// ---- 試合のページ(文章ログと投手の成績) ----

async function renderGame(args, token) {
  const d = await query("game", { game_no: args.game_no });
  if (token !== state.token) return;
  const s = d.summary;
  const st = d.story;
  $("game-title").textContent = `${s.day}日目 ${s.away.name} ${s.away.runs} - ${s.home.runs} ${s.home.name}`;
  $("game-tags").textContent = [s.innings > 9 ? `延長${s.innings}回` : "", ...st.tags.filter((t) => t !== "延長")].filter(Boolean).join("・");
  $("game-stadium").replaceChildren("球場:", link(s.stadium, () => openPage("stadium", { id: s.home.team_id })), `(${s.home.name}の本拠地)`);
  const lineCols = [...st.line.innings.map((i) => ({ key: i, label: i })), { key: "total", label: "計" }];
  $("game-line").replaceChildren(
    table({
      firstLabel: "チーム",
      columns: lineCols,
      rows: st.line.rows.map((r) => ({ ...r, values: { ...Object.fromEntries(st.line.innings.map((i, k) => [i, r.cells[k]])), total: String(r.total) } })),
      first: (r) => [teamLink(r.name, r.team_id)],
    }),
    el("p", { className: "muted small" }, "イニングごとの得点。X は、後攻のチームが勝っていて、9回裏などの攻撃をしなかったこと。"),
  );
  const pcols = [
    { key: "decision", label: "結果" },
    { key: "role", label: "区分" },
    { key: "innings", label: "投球回" },
    { key: "batters_faced", label: "対戦打者" },
    { key: "hits", label: "被安打" },
    { key: "runs", label: "失点" },
    { key: "earned_runs", label: "自責点" },
    { key: "exit_reason", label: "降板の理由" },
  ];
  $("game-pitchers").replaceChildren(
    table({
      firstLabel: "投手",
      columns: pcols,
      rows: st.pitchers.map((p) => ({ ...p, values: Object.fromEntries(pcols.map((c) => [c.key, String(p[c.key] ?? "")])) })),
      first: (p) => [playerLink(p.name, p.id), el("span", { className: "sub" }, p.team)],
    }),
  );
  $("game-lineups").replaceChildren(
    ...st.teams.map((t) =>
      el(
        "div",
        {},
        el("h3", {}, `${t.name}(${t.side})`),
        el("p", {}, "先発投手:", playerLink(t.starter.name, t.starter.id)),
        el("ol", {}, ...t.lineup.map((x) => el("li", {}, playerLink(x.name, x.id), `(${x.position})${x.rest_sub ? "※休養の代わり" : ""}`))),
      ),
    ),
  );
  const away = st.away.name;
  const home = st.home.name;
  $("game-log").replaceChildren(
    ...st.halves.map((h) =>
      el(
        "section",
        {},
        el("h3", {}, `${h.title}(${h.batting_team}の攻撃)`),
        el(
          "ul",
          {},
          ...h.events.map((e) => {
            if (e.type === "pitching_change") return el("li", { className: "change" }, `【投手交代】${e.team}:${e.pitcher}`);
            const extra = e.runs ? el("span", { className: "runs" }, ` → ${e.runs}点(スコア ${away} ${e.score.away} - ${e.score.home} ${home})`) : null;
            return el("li", { className: "pa" }, `[${e.situation}] ${e.order}番 ${e.batter}:${e.result}`, extra);
          }),
        ),
      ),
    ),
  );
}

// ---- 指標の解説(指標の定義データから) ----

async function renderGuide(token) {
  const d = await query("metrics_guide");
  if (token !== state.token) return;
  $("guide-body").replaceChildren(
    ...d.groups.flatMap((g) => [
      el("h2", {}, g.label),
      ...g.metrics.map((m) =>
        el(
          "div",
          { className: "guide-item", dataset: { key: m.key } },
          el("h3", {}, m.name),
          ...m.formulas.map((f) => el("div", { className: "formula" }, `式(${f.role}):${f.text}`)),
          el("p", { className: "description" }, m.description),
          el("ul", {}, ...m.better.map((b) => el("li", {}, b)), ...m.notes.map((n) => el("li", { className: "note" }, `注意:${n}`))),
        ),
      ),
    ]),
    el("h2", {}, "基準値の名前"),
    el("p", { className: "small" }, "セイバーの一部の指標は、リーグ全体の結果から求める「基準値」を式に使います。基準値は、シーズン序盤は前のシーズン(1年目は試運転)の値を混ぜて使います。"),
    el("ul", { className: "small" }, ...d.baseline_names.map((b) => el("li", {}, b.label))),
  );
}

// ---- メニュー(設定:答え合わせモード。D-114) ----

function renderSettings() {
  for (const r of document.querySelectorAll("input[name=answer-level]")) r.checked = Number(r.value) === state.answerLevel;
  $("menu-offseason").hidden = !(state.view && state.view.status && state.view.status.offseason && state.view.status.offseason.active); // 「全部おまかせ」は手続き中だけ(D-271)
  $("proc-auto").disabled = state.running;
}

function setAnswerLevel(level) {
  if (level > state.answerLevel && !confirm("見ると、成績から実力を推理する楽しみが減ります。答え合わせモードをオンにしますか?")) {
    renderSettings();
    return;
  }
  state.answerLevel = level;
  clearAnswers();
  if (level === 0) {
    // オフにしたら、隠れている画面に残った能力の表示も消す(D-108)。並び順の欄の選択肢と、能力の項目での並び順も戻す
    for (const id of ["stats-table", "stats-info", "stats-terms-list", "stats-rule", "player-season", "stats-sort", "stadium-answers", "offseason-answers", "review-answers"]) $(id).replaceChildren();
    for (const role of ["batter", "pitcher"]) {
      if (state.abilityKeys[role].includes(state.sortBy[role].key)) state.sortBy[role] = { key: null, order: null };
      if (state.shownSort[role] && state.abilityKeys[role].includes(state.shownSort[role].key)) state.shownSort[role] = null;
      state.abilityKeys[role] = [];
    }
  }
  $("answer-badge").hidden = level === 0;
  $("answer-badge").textContent = level === 0 ? "" : `答え合わせモード:${level}段階目`;
  renderSettings();
}

// ---- 新規開始 ----

function randomSeed() {
  return crypto.getRandomValues(new Uint32Array(1))[0];
}

function parseSeed(id) {
  // 空欄なら null(毎回ちがう値)。正しくない値なら理由の文字
  const text = $(id).value.trim();
  if (text === "") return { value: null };
  if (!/^\d+$/.test(text)) return { error: "0 以上の整数を入れてください" };
  const n = Number(text);
  if (n > MAX_SEED) return { error: `${MAX_SEED} 以下にしてください` };
  return { value: n };
}

function teamInputs() {
  return [...document.querySelectorAll("#league-fields input[type=text]")];
}

function names() {
  return teamInputs().map((i) => i.value);
}

async function showNewGame() {
  $("new-message").textContent = "";
  $("my-team-none").checked = true; // 既定は観戦のみ(D-198)
  $("league-seed").value = "";
  $("season-seed").value = "";
  $("league-seed-error").textContent = "";
  $("season-seed-error").textContent = "";
  await preparePreview(randomSeed(), true);
  showScreen("new");
}

async function preparePreview(seed, reset) {
  const r = await call("preview", { seed });
  const pv = r.value;
  // 入力済みの名前と自球団の選択は、作り直す直前の状態を引き継ぐ
  const prev = reset ? null : { names: names(), mine: myTeamIndex() };
  state.newGame = { seed, preview: pv };
  const box = $("league-fields");
  box.dataset.seed = String(seed);
  box.replaceChildren();
  let i = 0;
  for (const lg of pv.leagues) {
    const fs = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = lg.name;
    fs.appendChild(legend);
    for (const t of lg.teams) {
      const index = i++;
      const div = document.createElement("div");
      div.className = "team";
      const label = document.createElement("label");
      label.className = "name-label";
      label.htmlFor = `team-${index}`;
      label.textContent = `${index + 1}番目の球団(本拠地:${t.stadium})`;
      const input = document.createElement("input");
      input.type = "text";
      input.id = `team-${index}`;
      input.placeholder = t.default_name;
      input.autocomplete = "off";
      input.spellcheck = false;
      input.setAttribute("autocapitalize", "off");
      input.setAttribute("aria-describedby", `team-${index}-error`);
      input.value = prev ? prev.names[index] || "" : "";
      input.addEventListener("input", scheduleCheck);
      const err = document.createElement("p");
      err.className = "error";
      err.id = `team-${index}-error`;
      const pick = document.createElement("label");
      pick.className = "pick";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "my-team";
      radio.value = String(index);
      radio.checked = prev ? prev.mine === index : false; // 既定は観戦のみ(D-198)
      pick.append(radio, "自球団にする");
      div.append(label, input, err, pick);
      fs.appendChild(div);
    }
    box.appendChild(fs);
  }
}

function myTeamIndex() {
  const r = document.querySelector("input[name=my-team]:checked");
  return r ? Number(r.value) : -1;
}

let checkTimer = null;
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(checkNames, 250);
}

function showProblems(problems) {
  teamInputs().forEach((input, i) => {
    const p = problems[i];
    $(`team-${i}-error`).textContent = p || "";
    input.setAttribute("aria-invalid", String(Boolean(p)));
  });
}

async function checkNames() {
  if (!state.newGame) return [];
  const r = await call("check", { seed: state.newGame.seed, names: names() });
  showProblems(r.value);
  return r.value;
}

async function leagueSeedChanged() {
  const s = parseSeed("league-seed");
  $("league-seed-error").textContent = s.error || "";
  if (s.error) return;
  if (s.value !== null && state.newGame && state.newGame.seed === s.value) return; // 同じシードなら作り直さない
  const seed = s.value === null ? randomSeed() : s.value;
  await preparePreview(seed, false);
  await checkNames();
}

async function startNewGame() {
  $("new-message").textContent = "";
  const ls = parseSeed("league-seed");
  const ss = parseSeed("season-seed");
  $("league-seed-error").textContent = ls.error || "";
  $("season-seed-error").textContent = ss.error || "";
  const problems = await checkNames();
  const bad = problems.findIndex(Boolean);
  if (ls.error || ss.error || bad >= 0) {
    $("new-message").textContent = "入力に問題があります。赤い字の説明を見て、直してください。";
    if (bad >= 0) teamInputs()[bad].focus();
    return;
  }
  const mine = myTeamIndex(); // -1 は観戦のみ(球団を操作しない。D-198)
  if (state.dirty && !confirm("今のゲームに、未保存の変更があります。新しく始めると失われます。始めますか?")) return;
  $("new-start").disabled = true;
  try {
    const seed = state.newGame.seed;
    const seasonSeed = ss.value === null ? randomSeed() : ss.value;
    const baselines = document.querySelector("input[name=baseline-mode]:checked").value;
    $("trial-box").hidden = false; // 事前運転(リーグの歴史を作る)は、基準値の求め方にかかわらず行う(D-190)
    $("trial-progress").value = 0;
    $("trial-text").textContent = "リーグの歴史を作っています(数十年分の選手の入れ替わり)…";
    const t0 = performance.now();
    const scoutLevel = document.querySelector("input[name=scout-level]:checked").value;
    const moneyRule = document.querySelector("input[name=money-rule]:checked").value;
    const r = await call("newGame", { seed, seasonSeed, names: names(), myTeamIndex: mine, baselines, scoutLevel, moneyRule }).finally(() => ($("trial-box").hidden = true));
    state.trialSeconds = baselines === "trial" ? (performance.now() - t0) / 1000 : null;
    if (!r.ok) {
      $("new-message").textContent = [r.message, ...r.problems].join("\n");
      return;
    }
    // 入力欄は空にしておく(画面に名前を残さない)
    for (const input of teamInputs()) input.value = "";
    state.newGame = null;
    enterGame(r.value, true);
  } finally {
    $("new-start").disabled = false;
  }
}

function prerunProgress({ year, total }) {
  $("trial-progress").max = total;
  $("trial-progress").value = year;
  $("trial-text").textContent = `リーグの歴史を作っています:${year} / ${total} 年(試合はせず、選手の入れ替わりだけを進めます)`;
}

function trialProgress({ day, total }) {
  $("trial-progress").max = total;
  $("trial-progress").value = day;
  $("trial-text").textContent = `試運転のシーズン:${day} / ${total} 日(この結果は、画面に出さず保存もしません)`;
}

// ---- 進める ----

async function advance(days) {
  if (state.running || !state.view || state.view.status.is_over) return;
  state.running = true;
  state.stopRequested = false;
  $("stop").disabled = false;
  $("progress-message").className = "message";
  $("progress-message").textContent = "";
  renderProgress();
  const start = state.view.status.day;
  const total = days === 0 ? state.view.status.total_days - start : Math.min(days, state.view.status.total_days - start);
  const showRun = () => {
    $("run-progress").max = total;
    $("run-box").hidden = false;
  };
  // 「止める」と進み具合のバーは、0.6 秒を超えたときだけ出す。「1日」では出さない(D-118)
  const hideRun = days === 1 ? async () => {} : delayedShow(showRun, () => ($("run-box").hidden = true));
  let done = 0;
  try {
    // 1日ずつ裏に頼む。日の区切りごとに、止める指示を確かめる
    while (done < total && !state.view.status.is_over && !state.stopRequested) {
      const r = await call("advance", { days: 1 });
      state.view = r.value;
      setDirty(true);
      done += 1;
      $("run-progress").value = done;
      $("run-text").textContent = `${done} / ${total} 日分を進めました(${state.view.status.day}日目)`;
      renderProgress();
    }
    const s = state.view.status;
    if (s.is_over) {
      const m = s.my_team;
      $("progress-message").textContent = m
        ? `シーズンが終わりました。${m.name} は ${m.league_name} ${m.rank}位でした。`
        : "シーズンが終わりました。";
    } else if (state.stopRequested) {
      $("progress-message").textContent = `${s.day}日目の終わりで止めました。`;
    }
  } catch (err) {
    $("progress-message").className = "message ng";
    $("progress-message").textContent = `進められませんでした:${err.message}`;
  } finally {
    await hideRun();
    state.running = false;
    renderProgress();
    if (current().name !== "progress") renderCurrent(); // ほかの画面を見ていたら、新しい日の内容にする
  }
}

// ---- 保存と読み込み ----

function todayText() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

async function save() {
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

async function openFile(event, messageId) {
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

// ---- 閉じる前の警告(未保存の変更があるときだけ) ----

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

// ---- ボタン ----

buildTabs();
$("go-new").addEventListener("click", () => showNewGame().catch((err) => ($("start-message").textContent = err.message)));
$("new-back").addEventListener("click", () => {
  for (const input of teamInputs()) input.value = "";
  state.newGame = null;
  if (state.view) {
    showScreen(current().name);
    renderCurrent();
  } else {
    showScreen("start");
  }
});
$("new-start").addEventListener("click", startNewGame);
$("league-seed").addEventListener("change", leagueSeedChanged);
$("season-seed").addEventListener("change", () => ($("season-seed-error").textContent = parseSeed("season-seed").error || ""));
for (const b of document.querySelectorAll(".adv")) b.addEventListener("click", () => advance(Number(b.dataset.days)));
$("stop").addEventListener("click", () => {
  state.stopRequested = true;
  $("stop").disabled = true;
  $("progress-message").textContent = "この日の試合が終わったら止めます…";
});
$("save").addEventListener("click", save);
$("year-end").addEventListener("click", () => openPage("yearend"));
$("open-offseason").addEventListener("click", () => openPage("procedure"));
$("open-review").addEventListener("click", () => openPage("review"));
$("team-review").addEventListener("click", () => openPage("review", { team_id: current().args.id }));
$("review-team").addEventListener("change", () => {
  state.review.team = $("review-team").value;
  state.review.year = null;
  renderCurrent();
});
$("review-year").addEventListener("change", () => {
  state.review.year = Number($("review-year").value);
  renderCurrent();
});
$("proc-next").addEventListener("click", procNext);
$("proc-stage-auto").addEventListener("click", procStageAuto);
$("proc-auto").addEventListener("click", procAuto);
$("yearend-save").addEventListener("click", save);
$("yearend-go").addEventListener("click", yearEnd);
$("open-file-start").addEventListener("change", (e) => openFile(e, "start-message"));
$("open-file-top").addEventListener("change", (e) => openFile(e, "progress-message"));
$("open-file-start").disabled = true;
$("open-file-top").disabled = true;
$("open-guide").addEventListener("click", () => openPage("guide"));
$("menu").addEventListener("click", () => {
  if (current().name !== "settings") openPage("settings");
});
for (const b of document.querySelectorAll(".back")) b.addEventListener("click", back);

// 成績
for (const b of $("stats-role").children) {
  b.addEventListener("click", () => {
    // 打者と投手を切り替えたときは、その側の既定の並び順に戻す(絞り込みと表示件数は保つ。D-131)
    state.stats.role = b.dataset.value;
    state.sortBy[state.stats.role] = { key: null, order: null };
    state.shownSort[state.stats.role] = null;
    renderCurrent();
  });
}
for (const b of $("stats-kind").children) {
  b.addEventListener("click", () => {
    commitSort();
    state.stats.kind = b.dataset.value; // 並び順・絞り込み・表示件数は保つ(D-131)
    renderCurrent();
  });
}
$("stats-sort").addEventListener("change", () => {
  const key = $("stats-sort").value;
  state.sortBy[state.stats.role] = { key, order: null };
  if (isAbilityKey(key)) state.stats.kind = "ability"; // 能力の項目を選んだら、能力の表で見せる
  state.stats.shown = STATS_PAGE;
  renderCurrent();
});
for (const b of $("stats-order").children) {
  b.addEventListener("click", () => {
    commitSort();
    currentSort().order = b.dataset.value;
    state.stats.shown = STATS_PAGE;
    renderCurrent();
  });
}
$("stats-season").addEventListener("change", () => {
  state.stats.season = $("stats-season").value;
  state.stats.shown = STATS_PAGE;
  renderCurrent();
});
$("stats-league").addEventListener("change", () => {
  state.stats.league = $("stats-league").value;
  state.stats.shown = STATS_PAGE;
  buildStatsFilters();
  renderCurrent();
});
$("stats-team").addEventListener("change", () => {
  state.stats.team = $("stats-team").value;
  state.stats.shown = STATS_PAGE;
  renderCurrent();
});
$("stats-qualified").addEventListener("change", () => {
  state.stats.qualified = $("stats-qualified").checked;
  state.stats.shown = STATS_PAGE;
  renderCurrent();
});

// 試合
$("games-day").addEventListener("change", () => {
  state.gamesDay = Number($("games-day").value);
  renderCurrent();
});
$("games-prev").addEventListener("click", () => {
  state.gamesDay -= 1;
  renderCurrent();
});
$("games-next").addEventListener("click", () => {
  state.gamesDay += 1;
  renderCurrent();
});

// 選手のページ
for (const b of $("player-kind").children) {
  b.addEventListener("click", () => {
    state.playerKind = b.dataset.value;
    renderCurrent();
  });
}

// メニュー(答え合わせモード)
for (const r of document.querySelectorAll("input[name=answer-level]")) {
  r.addEventListener("change", () => setAnswerLevel(Number(r.value)));
}

boot();
