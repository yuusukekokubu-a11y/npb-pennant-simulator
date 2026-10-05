// 用語集のデータの引き当て(UI の整理②。D-306〜D-313)。説明の文は計算本体の用語集のデータ(glossary.json)1 か所にあり、
// 表の見出しの title・見出しを押すと出る解説・用語集のページ・新規開始の説明バーは、どれもここから引く。
// 引き当ての決まりは計算本体(pennant/glossary.py の find)と同じ:名前・別名で見つからなければ、末尾のかっこ書きを外してもう一度。

import { state } from "./core.js";

const TAIL = /[((][^()()]*[))]$/;
let index = null;

// 起動のときに 1 回だけ受け取る(ゲームがなくても見られる)
export function setGlossary(data) {
  state.glossary = data;
  index = new Map();
  for (const t of data.terms) for (const name of [t.name, ...(t.aliases || [])]) index.set(name, t);
}

export function findTerm(label) {
  if (!index || typeof label !== "string") return null;
  const text = label.trim();
  return index.get(text) || (TAIL.test(text) ? index.get(text.replace(TAIL, "").trim()) || null : null);
}

export function termById(id) {
  return state.glossary ? state.glossary.terms.find((t) => t.id === id) || null : null;
}

// 見出しの title などに出す 1 つの文(意味・式・良い向き)
export function termText(t) {
  if (!t) return "";
  return [t.meaning, t.formula ? `式:${t.formula}` : "", t.better || ""].filter(Boolean).join(" ");
}

export const titleFor = (label) => termText(findTerm(label));

// 画面に最初からある表(順位表など)の見出しに、用語集の解説を付ける
export function applyStaticTitles(root = document) {
  for (const th of root.querySelectorAll("th[scope=col]")) {
    if (!th.title) th.title = titleFor(th.textContent);
  }
}

// 1 行の注意書き(D-310):シーズン途中の値・通算の参考値。詳しい理由(基準値の混ぜ方・球場補正)は用語集に
export function seasonCaution(season) {
  const st = state.view && state.view.status;
  if (season === "career") return "通算の指標は参考値(シーズンごとの値の加重平均)。";
  if ((season === undefined || season === null || season === "current") && st && !st.is_over) return "シーズン途中の値(ここまで)。";
  return "";
}
