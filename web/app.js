// 画面の動き(最小のブラウザ画面①②。D-107、D-114)。計算は worker.js(裏の Python)に任せ、ここは表示と操作だけを行う。
// ブラウザの保存領域(localStorage・sessionStorage・IndexedDB・Cookie)には何も書かない。自動保存もしない。
// 選手の非公開の情報は、答え合わせモードがオンのときだけ、答え合わせ用の入口(answer)から受け取る(D-108、D-114)。
// オフのときは、その入口を呼ばない。答え合わせモードの設定は、どこにも保存しない(開き直すとオフ)。

const LOCAL_PYODIDE = new URLSearchParams(location.search).get("pyodide") === "local"; // 試験用(このページに置いた Pyodide を使う)
const MAX_SEED = 4294967295;
const WAIT_SHOW_MS = 600; // 待ち時間の表示は、これを超えたときだけ出し、出したらこれ以上は続ける(D-118)
const STATS_PAGE = 50; // 個人成績の表で、一度に出す人数(仮置き。DESIGN 9章)
const GAMES_PAGE = 20; // 選手の試合ごとの成績で、一度に出す試合数(仮置き。DESIGN 9章)
// 下のタブ。増やすときは、ここに足して、同じ名前の画面(screen-…)を index.html に作る
const TABS = [
  { id: "progress", label: "進行" },
  { id: "standings", label: "順位表" },
  { id: "stats", label: "成績" },
  { id: "games", label: "試合" },
];
// タブの上に重ねて開くページ(「戻る」で前の画面へ)
const PAGES = ["player", "team", "game", "settings", "guide", "stadium"];
const SCREENS = ["start", "new", ...TABS.map((t) => t.id), ...PAGES];

const $ = (id) => document.getElementById(id);
let worker = null;
let ready = false;
let nextId = 1;
const pending = new Map();

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
  stats: { role: "batter", kind: "basic", qualified: true, league: "", team: "", shown: STATS_PAGE },
  // 並び順(打者/投手ごとに保つ。基本・セイバー・能力を切り替えても保つ。D-131)。key が null なら、その表の既定
  sortBy: { batter: { key: null, order: null }, pitcher: { key: null, order: null } },
  shownSort: { batter: null, pitcher: null }, // 今の表で実際に使った並び順(既定を解決したもの)
  abilityKeys: { batter: [], pitcher: [] }, // 答え合わせモードがオンのときだけ入る、能力の項目の名前
  playerKind: "basic",
  gamesDay: null,
  token: 0, // 表示の作り直しの番号(古い結果を捨てるため)
};

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
  return state.view ? `${state.view.status.games_played}` : "";
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
  }[name];
  Promise.resolve(job && job()).catch((err) => showError(name, err));
}

function showError(name, err) {
  const box = { stats: "stats-table", games: "games-list", player: "player-season", team: "team-record", game: "game-log" }[name];
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
function table({ firstLabel, columns, rows, first, sort, order, onSort, rowClass, limit, more, extra }) {
  const headCell = (c, className) => {
    const arrow = sort === c.key ? (order === "desc" ? " ▼" : " ▲") : "";
    const label = onSort ? el("button", { className: "sort", type: "button", onclick: () => onSort(c.key) }, c.label + arrow) : c.label;
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
    if (extra) tr.append(el("td", { className: "sticky2" }, r.values[extra.key] ?? ""));
    for (const c of columns) tr.append(el("td", {}, r.values[c.key] ?? ""));
    body.append(tr);
  }
  const wrap = el("div", {}, el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, head), body)));
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

function renderProgress() {
  const s = state.view.status;
  $("day-text").textContent = s.is_over ? `全${s.total_days}日 終了` : `${s.day}日目 / ${s.total_days}日`;
  $("topbar-day").textContent = s.is_over ? "シーズン終了" : `${s.day}日目 / ${s.total_days}日`;
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
  };
}

function currentSort() {
  return state.sortBy[state.stats.role];
}

function isAbilityKey(key) {
  return state.abilityKeys[state.stats.role].includes(key);
}

// 並び順の選択欄(D-132):基本・セイバーの全指標。答え合わせモードがオンなら、能力の項目も
async function buildSortSelect(sortKey) {
  const role = state.stats.role;
  const keys = await query("sortable_keys", { role });
  const groups = { basic: [], saber: [], count: [] };
  for (const k of keys) (groups[k.type === "count" ? "count" : k.category] || groups.count).push(k);
  const options = [
    el("optgroup", { label: "基本" }, ...groups.basic.map((k) => el("option", { value: k.key }, k.label))),
    el("optgroup", { label: "セイバー" }, ...groups.saber.map((k) => el("option", { value: k.key }, k.label))),
    el("optgroup", { label: "元の数" }, ...groups.count.map((k) => el("option", { value: k.key }, k.label))),
  ];
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
    ability: "能力:選手の本当の実力の数値です(答え合わせモードのときだけ)。",
  }[s.kind];
  $("stats-kind-note").textContent = note;
  if (s.kind === "ability") {
    $("stats-baseline").hidden = true;
    return renderAbilityTable(token);
  }
  const sort = currentSort();
  // 能力の項目で並べていたときは、成績の表ではその表の既定に戻す(能力の値は、公開用の関数では扱わない)
  const key = sort.key && !isAbilityKey(sort.key) ? sort.key : null;
  const args = { ...statsArgs(), kind: s.kind, sort: key, order: key ? sort.order : null };
  const data = await withLoading("stats-loading", query("stats", args));
  if (token !== state.token) return;
  await buildSortSelect(data.sort.key);
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
  $("stats-rule").textContent = `${data.qualify_rule}。${s.qualified ? "今は、規定に届いた選手だけを出しています。" : "今は、試合に出た全員を出しています。"} ${extraNote}表は横にずらせます。列の見出しを押すと並べ替え、もう一度押すと逆の順になります。上の「並び順」の欄からも選べます。`;
  terms("stats-terms-list", data.extra_column ? [data.extra_column, ...data.columns] : data.columns);
}

function sortStats(key) {
  // 今の並びと同じ列なら逆順に、違う列ならその指標の「よい」向き(能力の項目は高い順)から
  const s = currentSort();
  const shown = state.shownSort[state.stats.role];
  if (shown && shown.key === key) {
    s.key = key;
    s.order = shown.order === "desc" ? "asc" : "desc";
  } else {
    s.key = key;
    s.order = null;
  }
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
    ` / ${p.position_label} / ${p.age}歳 / ${p.hand || ""}`,
  );
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
    for (const id of ["stats-table", "stats-info", "stats-terms-list", "stats-rule", "player-season", "stats-sort", "stadium-answers"]) $(id).replaceChildren();
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
      radio.checked = prev ? prev.mine === index : index === 0;
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
  const mine = myTeamIndex();
  if (mine < 0) {
    $("new-message").textContent = "「自球団にする」で、球団を1つ選んでください。";
    return;
  }
  if (state.dirty && !confirm("今のゲームに、未保存の変更があります。新しく始めると失われます。始めますか?")) return;
  $("new-start").disabled = true;
  try {
    const seed = state.newGame.seed;
    const seasonSeed = ss.value === null ? randomSeed() : ss.value;
    const baselines = document.querySelector("input[name=baseline-mode]:checked").value;
    $("trial-box").hidden = baselines !== "trial";
    $("trial-progress").value = 0;
    $("trial-text").textContent = "試運転のシーズンを始めています…";
    const t0 = performance.now();
    const r = await call("newGame", { seed, seasonSeed, names: names(), myTeamIndex: mine, baselines }).finally(() => ($("trial-box").hidden = true));
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
    if (current().name !== "progress") showTab("progress");
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
