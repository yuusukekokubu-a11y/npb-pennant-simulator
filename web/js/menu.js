// メニュー(用語集・答え合わせモード)(web/app.js から分けた。保守②。D-291)

import { $, state } from "./core.js";
import { clearAnswers } from "./backend.js";
import { el } from "./parts.js";

// ---- 用語集(UI の整理②。D-307〜D-309):分類と検索の 1 行、項目の一覧。データは起動のときに受け取った用語集 ----

const guide = { category: "all", text: "" };
const norm = (x) => (x || "").normalize("NFKC").toLowerCase();

// 検索:名前・別名・略語の正式名・意味の文字に、入れた文字が含まれる項目(略語「wRC」でも日本語「年俸」でも見つかる)
export function matchTerms(terms, category, text) {
  const q = norm(text).trim();
  return terms.filter((t) => (category === "all" || t.category === category) && (!q || [t.name, t.full, t.meaning, ...(t.aliases || [])].some((x) => norm(x).includes(q))));
}

function termItem(t) {
  return el(
    "div",
    { className: "guide-item", dataset: { name: t.name } },
    el("h3", {}, t.name, t.full ? el("span", { className: "full" }, t.full) : null),
    el("p", { className: "description" }, t.meaning),
    t.formula ? el("div", { className: "formula" }, `式:${t.formula}`) : null,
    t.better ? el("p", { className: "better" }, t.better) : null,
  );
}

export function renderGuide() {
  const g = state.glossary;
  if (!g) return;
  const sel = $("guide-category");
  if (!sel.options.length) {
    sel.replaceChildren(el("option", { value: "all" }, "すべての分類"), ...g.categories.map((c) => el("option", { value: c.key }, c.label)));
    sel.addEventListener("change", () => { guide.category = sel.value; renderGuide(); });
    $("guide-search").addEventListener("input", () => { guide.text = $("guide-search").value; renderGuide(); });
  }
  sel.value = guide.category;
  const hits = matchTerms(g.terms, guide.category, guide.text);
  $("guide-count").textContent = `${hits.length} 項目`;
  const groups = g.categories.filter((c) => hits.some((t) => t.category === c.key));
  $("guide-body").replaceChildren(
    ...groups.flatMap((c) => [el("h2", {}, c.label), ...hits.filter((t) => t.category === c.key).map(termItem)]),
    hits.length ? null : el("p", { className: "muted", id: "guide-empty" }, "見つかりませんでした。"),
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
    for (const id of ["stats-table", "stats-info", "player-season", "stats-sort", "stadium-answers", "offseason-answers", "review-answers"]) $(id).replaceChildren();
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
