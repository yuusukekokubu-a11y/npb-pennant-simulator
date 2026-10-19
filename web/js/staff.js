// 球団職員(D-474〜D-477):オフの手続きの「職員」の段階と、チームのページの「職員」の欄。
// 腕はランク(S〜E)だけを出す(D-473)。どの球団の職員も入れ替えられ、触った他球団は「あなたが決めた」(D-476)

import { $, state } from "./core.js";
import { call } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, playerChip, table } from "./parts.js";
import { runProc } from "./procedure.js";
import { setDirty } from "./progress.js";

const contractText = (m) => (m.remaining === null || m.remaining === undefined ? `${m.years} 年` : `${m.remaining} 年`);
const careerText = (m) => [m.former ? "元選手" : "", m.former_team_name ? `前 ${m.former_team_name}` : ""].filter(Boolean).join("・");
// 職員は選手ではないので、行の中のボタン(解任・希望)のまま。元選手の名前は名札(押すと選手のページ。D-484)
const nameCell = (m) => [m.player_id ? playerChip(m.name, m.player_id) : el("span", { className: "pname" }, m.name)];

// ---- オフの手続きの「職員」の段階 ----

export function staffSection(v) {
  const s = v.staff;
  if (!s) return [el("p", { className: "muted" }, "職員の情報がありません。")];
  const ps = state.proc;
  const out = [];
  if (v.my_team) {
    const cols = [{ key: "name", label: "職員" }, { key: "rank", label: "腕" }, { key: "age", label: "年齢" }, { key: "trait", label: "特徴" }, { key: "salary", label: "年俸" }, { key: "contract", label: "残り" }, { key: "action", label: "" }];
    const rows = s.mine.map((m) => ({ ...m, values: {
      name: el("span", {}, ...nameCell(m)), rank: m.rank, age: `${m.age}歳`, trait: [m.trait_label, careerText(m)].filter(Boolean).join("・"), salary: m.salary_text, contract: contractText(m),
      action: s.done ? "" : el("button", { type: "button", className: "secondary", dataset: { role: m.role }, id: `staff-dismiss-${m.role}`, disabled: state.running, onclick: () => { if (confirm(`${m.role_label}の ${m.name} を解任します(残りの年俸は払います)。よろしいですか?`)) runProc("staff_dismiss", { role: m.role }); } }, "解任する"),
    } }));
    out.push(el("h3", {}, `自球団の職員${s.vacancies.length ? `(空き:${s.vacancies.map((x) => x.label).join("・")})` : ""}`),
      el("div", { id: "staff-mine" }, table({ firstLabel: "担当", columns: cols, rows, first: (m) => [el("span", {}, m.role_label)], fluid: true })));
  }
  if (s.events.length) {
    const kind = { expire: "契約切れ", fired: "監督を解任", former: "元選手が候補に" };
    out.push(el("details", { id: "staff-events" }, el("summary", {}, `このオフの職員の動き(${s.events.length} 件)`),
      el("ul", { className: "offer-history" }, ...s.events.map((e) => el("li", { className: e.is_mine ? "mine" : "" }, `${kind[e.kind] || e.kind}:${e.team_name ? `${e.team_name} ` : ""}${e.role_label} ${e.name}`)))));
  }
  if (s.done) {
    if (s.results.length) out.push(staffResults(s.results));
    return out;
  }
  const role = ps.staffRole || "all";
  const sel = el("select", { id: "staff-role-filter", "aria-label": "役割の絞り込み", onchange: (e) => { ps.staffRole = e.target.value; renderCurrent(); } },
    el("option", { value: "all" }, "すべての役割"), ...s.roles.map((r) => el("option", { value: r.key }, r.label)));
  sel.value = role;
  const pool = s.pool.filter((m) => role === "all" || m.role === role);
  const cols = [{ key: "role", label: "担当" }, { key: "rank", label: "腕" }, { key: "age", label: "年齢" }, { key: "trait", label: "特徴" }, { key: "salary", label: "年俸" }, { key: "years", label: "年数" }, { key: "career", label: "経歴" }, { key: "action", label: "" }];
  const rows = pool.map((m) => ({ ...m, values: {
    role: m.role_label, rank: m.rank, age: `${m.age}歳`, trait: m.trait_label, salary: m.salary_text, years: `${m.years} 年`, career: careerText(m),
    action: !v.my_team ? "" : m.offer
      ? el("button", { type: "button", className: "secondary staff-cancel", dataset: { staffId: m.staff_id }, disabled: state.running, onclick: () => runProc("staff_cancel", { staff_id: m.staff_id }) }, `希望 ${m.offer_rank} 番目を取り消す`)
      : el("button", { type: "button", className: "staff-offer", dataset: { staffId: m.staff_id }, disabled: state.running || !m.open, onclick: () => runProc("staff_offer", { staff_id: m.staff_id }) }, "希望する"),
  } }));
  out.push(el("h3", {}, `職員の市場(${s.pool.length} 人)`), el("div", { className: "row" }, sel),
    pool.length ? el("div", { id: "staff-pool" }, table({ firstLabel: "職員", columns: cols, rows, first: nameCell, rowClass: (m) => (m.offer ? "selected" : !m.open ? "muted" : ""), fluid: true })) : el("p", { className: "muted small" }, "この役割の候補はいません。"),
    el("p", { className: "muted small" }, "希望できるのは、空いている役割で、年俸に使える額に収まる候補だけ。締めた後に空いている役割は、最も安い候補で埋まります。"));
  return out;
}

function staffResults(results) {
  const cols = [{ key: "team", label: "球団" }, { key: "role", label: "担当" }, { key: "rank", label: "腕" }, { key: "salary", label: "年俸" }, { key: "years", label: "年数" }];
  const rows = [...results].sort((a, b) => (b.is_mine ? 1 : 0) - (a.is_mine ? 1 : 0)).map((r) => ({ ...r, values: { team: r.team_name, role: r.role_label, rank: r.rank, salary: r.salary_text, years: `${r.years} 年` } }));
  return el("details", { id: "staff-results", open: true }, el("summary", {}, `職員の市場の結果(雇った職員 ${results.length} 人)`),
    table({ firstLabel: "職員", columns: cols, rows, first: (r) => [el("span", {}, r.name + (r.by === "fill" ? "(空きを埋めた)" : ""))], rowClass: (r) => (r.is_mine ? "mine" : ""), fluid: true }));
}

export async function staffClose() {
  await runProc("staff_close");
}

// ---- チームのページの「職員」(D-476) ----

export function renderTeamStaff(d) {
  const box = $("team-staff");
  if (!d) { box.replaceChildren(); return; }
  const ts = state.teamStaff && state.teamStaff.team === d.team_id ? state.teamStaff : (state.teamStaff = { team: d.team_id, pick: "", message: null });
  const cols = [{ key: "name", label: "職員" }, { key: "rank", label: "腕" }, { key: "age", label: "年齢" }, { key: "trait", label: "特徴" }, { key: "contract", label: "残り" }];
  const rows = d.staff.map((m) => ({ ...m, values: { name: el("span", {}, ...nameCell(m)), rank: m.rank, age: `${m.age}歳`, trait: [m.trait_label, careerText(m)].filter(Boolean).join("・"), contract: contractText(m) } }));
  const run = async (name, args, ok) => {
    if (state.running) return;
    state.running = true;
    try {
      const r = await call("staff", { name, args: { team_id: d.team_id, ...args } });
      ts.message = r.ok ? { ok: true, text: ok } : { ok: false, text: r.message };
      if (r.ok) { state.cache.clear(); ts.pick = ""; setDirty(true); }
    } finally {
      state.running = false;
    }
    renderCurrent();
  };
  const sel = el("select", { id: "staff-swap-pick", "aria-label": "入れ替える職員の候補", disabled: state.running, onchange: (e) => { ts.pick = e.target.value; ts.message = null; renderCurrent(); } },
    el("option", { value: "" }, "入れ替える候補を選ぶ(職員の市場)"),
    ...d.roles.flatMap((r) => r.candidates.map((m) => el("option", { value: `${r.key}:${m.staff_id}` }, `${r.label}:${m.name}(${m.rank}・${m.age}歳${m.trait_label ? `・${m.trait_label}` : ""}${m.former ? "・元選手" : ""}・${m.salary_text}・${m.years} 年)`))));
  sel.value = ts.pick;
  const [role, sid] = ts.pick.split(":");
  const msg = el("p", { className: `message ${ts.message ? (ts.message.ok ? "ok" : "ng") : ""}`, id: "staff-message" }, ts.message ? ts.message.text : "");
  box.replaceChildren(
    el("p", { className: "small", id: "staff-source" }, d.manual ? `★ ${d.source_text}` : d.source_text),
    el("div", { id: "team-staff-table" }, table({ firstLabel: "担当", columns: cols, rows, first: (m) => [el("span", {}, m.role_label)], fluid: true })),
    d.dead.length ? el("p", { className: "muted small" }, `解任した職員の残りの年俸:${d.dead.map((x) => `${x.role_label} ${x.name} ${x.salary_text}(${x.until}シーズン目まで)`).join("、")}`) : "",
    el("div", { className: "row" }, sel,
      el("button", { id: "staff-swap", type: "button", disabled: state.running || !ts.pick, onclick: () => run("swap", { role, staff_id: sid }, "職員を入れ替えました(前の職員の残りの年俸は払います)。") }, "この職員にする"),
      d.is_mine ? "" : el("button", { id: "staff-ai", className: "secondary", type: "button", disabled: state.running || !d.manual, onclick: () => run("reset", {}, "AI に戻しました(次のオフから AI が雇います)。") }, "AI に戻す")),
    msg,
  );
}
