// FA の画面(表・提示のパネル・ラウンド)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { answer } from "./backend.js";
import { renderCurrent } from "./screens.js";
import { el, link, playerLink, table } from "./parts.js";
import { fetchRosterTable, inGroup, rosterControls, rosterSort, rosterSortLine, runProc } from "./procedure.js";
import { salaryInput } from "./contract.js";

// ---- FA(F3-2c。D-258〜D-264):提示ラウンド制。表・提示のパネル・ラウンドの表示・結果の一覧 ----

export async function faSection(v, token) {
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
    const si = salaryInput(f.offer ? f.offer.salary : f.calc_salary, fa.none_max_ratio ? f.calc_salary : fa.minimum_salary, fa.rounding, fa.none_max_ratio ? Math.floor(f.calc_salary * fa.none_max_ratio) : null);
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
