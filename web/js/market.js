// ドラフトと市場の候補の表・指名と入退団の記録(web/app.js から分けた。保守②。D-291)

import { state } from "./core.js";
import { renderCurrent } from "./screens.js";
import { el, link, table } from "./parts.js";
import { SCOUT_SORT_KEYS, ceilingText, fetchRosterTable, inGroup, rosterControls, rosterSort, rosterSortLine, runProc } from "./procedure.js";

export async function marketSection(v, truth, token) {
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

export function poolSection(v, truth) {
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

export function historyTable(picks, released) {
  const rows = [];
  for (const x of released) rows.push({ k: `自由契約`, team: x.team_name, name: x.name, pos: x.position_label, age: x.age, mine: x.is_mine });
  for (const x of picks) rows.push({ k: `${x.phase === "draft" ? "ドラフト" : "市場"} ${x.round} 巡`, team: x.team_name, name: x.player_id ? x.name : `(${x.note === "full" ? "空き枠なし" : "見送り"})`, pos: x.position_label, age: x.age, mine: x.is_mine });
  if (!rows.length) return el("p", { className: "muted small" }, "まだありません。");
  const body = el("tbody", {}, ...rows.map((r) => el("tr", { className: r.mine ? "mine" : "" }, el("td", {}, r.k), el("td", {}, r.team), el("td", {}, r.name), el("td", {}, r.pos || ""), el("td", {}, r.age ? `${r.age}歳` : ""))));
  return el("div", { className: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "手続き"), el("th", {}, "球団"), el("th", {}, "選手"), el("th", {}, "ポジション"), el("th", {}, "年齢"))), body));
}
