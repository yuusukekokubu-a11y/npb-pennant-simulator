// メニュー(指標の解説・答え合わせモード)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { clearAnswers, query } from "./backend.js";
import { el } from "./parts.js";

// ---- 指標の解説(指標の定義データから) ----

export async function renderGuide(token) {
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

export function renderSettings() {
  for (const r of document.querySelectorAll("input[name=answer-level]")) r.checked = Number(r.value) === state.answerLevel;
  $("menu-offseason").hidden = !(state.view && state.view.status && state.view.status.offseason && state.view.status.offseason.active); // 「全部おまかせ」は手続き中だけ(D-271)
  $("proc-auto").disabled = state.running;
}

export function setAnswerLevel(level) {
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
