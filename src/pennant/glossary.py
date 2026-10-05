"""用語集のデータ(data/glossary.json)の読み込み・検証と、名前での引き当て(UI の整理②。D-306〜D-312)。

画面の説明の文は、このデータ 1 か所に持つ。表の見出しの title・見出しを押すと出る解説・用語集のページ・
新規開始の説明バーは、どれもここから引く。指標の式と良い向きは、計算に使う指標の定義データ(metrics.json)から作る。
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json
from .metrics import MetricsConfig, formula_text, load_metrics_config

FORMAT_VERSION = 1
ROLE_LABELS = {"batter": "打者", "pitcher": "投手"}
BETTER_WORDS = {"high": "高いほど良い", "low": "低いほど良い"}
_OPTIONAL_TEXT = ("id", "full", "formula", "better", "metric")
_TAIL = re.compile(r"[((][^()()]*[))]$")


@dataclass(frozen=True)
class Glossary:
    categories: tuple[dict, ...]
    terms: tuple[dict, ...]
    source: str

    @functools.cached_property
    def _index(self) -> dict[str, dict]:
        out = {}
        for t in self.terms:
            for name in (t["name"], *t.get("aliases", ())):
                out[name] = t
        return out

    def find(self, label: str) -> dict | None:
        """表の列の名前などから項目を引く。名前・別名で見つからなければ、末尾のかっこ書きを外してもう一度引く。"""
        label = label.strip()
        hit = self._index.get(label)
        if hit is None and _TAIL.search(label):
            hit = self._index.get(_TAIL.sub("", label).strip())
        return hit

    def by_id(self, term_id: str) -> dict | None:
        return next((t for t in self.terms if t.get("id") == term_id), None)


def validate_glossary(data, source: str, metrics: MetricsConfig) -> Glossary:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is not None and c.get(root, "format_version", "") != FORMAT_VERSION:
        c.add("format_version", f"{FORMAT_VERSION} である必要があります")
    cats = c.get(root, "categories", "") or []
    keys = []
    for i, cat in enumerate(cats if isinstance(cats, list) else []):
        sec = c.section(cat, f"categories[{i}]")
        for k in ("key", "label"):
            v = c.get(sec, k, f"categories[{i}]")
            if v is not None and (not isinstance(v, str) or not v.strip()):
                c.add(f"categories[{i}].{k}", "空でない文字が必要です")
        if sec and isinstance(sec.get("key"), str):
            keys.append(sec["key"])
    if not keys:
        c.add("categories", "分類が 1 つ以上必要です")
    terms = c.get(root, "terms", "") or []
    seen: dict[str, str] = {}
    ids: set[str] = set()
    for i, t in enumerate(terms if isinstance(terms, list) else []):
        path = f"terms[{i}]"
        sec = c.section(t, path)
        if sec is None:
            continue
        for k in ("name", "category", "meaning"):  # 必須の欄(D-311)
            v = c.get(sec, k, path)
            if v is not None and (not isinstance(v, str) or not v.strip()):
                c.add(f"{path}.{k}", "空でない文字が必要です")
        if isinstance(sec.get("category"), str) and sec["category"] not in keys:
            c.add(f"{path}.category", f"分類 '{sec['category']}' はありません")
        for k in _OPTIONAL_TEXT:
            if k in sec and (not isinstance(sec[k], str) or not sec[k].strip()):
                c.add(f"{path}.{k}", "空でない文字が必要です")
        if "metric" in sec and isinstance(sec["metric"], str) and sec["metric"] not in metrics.metrics:
            c.add(f"{path}.metric", f"指標 '{sec['metric']}' は metrics.json にありません")
        if "metric" in sec and ("formula" in sec or "better" in sec):
            c.add(path, "指標の式と良い向きは metrics.json から作るので、formula・better は書きません")
        aliases = sec.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            c.add(f"{path}.aliases", "空でない文字の並び([ ] で囲んだもの)が必要です")
            aliases = []
        for name in [sec.get("name"), *aliases]:
            if isinstance(name, str):
                if name in seen:
                    c.add(path, f"名前 '{name}' が {seen[name]} と重なっています")
                seen[name] = path
        if isinstance(sec.get("id"), str):
            if sec["id"] in ids:
                c.add(f"{path}.id", f"id '{sec['id']}' が重なっています")
            ids.add(sec["id"])
        extra = set(sec) - {"name", "category", "meaning", "aliases", *_OPTIONAL_TEXT}
        if extra:
            c.add(path, f"知らない欄があります: {', '.join(sorted(extra))}")
    for mid, m in metrics.metrics.items():  # 成績の画面のすべての指標が、用語集に載っている
        if not any(isinstance(t, dict) and t.get("metric") == mid for t in terms if isinstance(terms, list)):
            c.add("terms", f"指標 '{mid}'({m['name']})の項目がありません")
    if c.problems:
        raise ConfigError(source, c.problems)
    out = []
    for t in terms:
        term = {k: t[k] for k in ("name", "category", "meaning", "aliases", "id", "full", "formula", "better") if k in t}
        if "metric" in t:
            m = metrics.metrics[t["metric"]]
            term["metric"] = t["metric"]
            term["formula"] = " / ".join(
                (f"{ROLE_LABELS[r]}:" if len(m["formulas"]) > 1 else "") + formula_text(metrics, r, e) for r, e in m["formulas"].items()
            )
            words = {r: BETTER_WORDS[b] for r, b in m["better"].items()}
            term["better"] = next(iter(words.values())) if len(set(words.values())) == 1 else "・".join(f"{ROLE_LABELS[r]}は{w}" for r, w in words.items())
        out.append(term)
    return Glossary(tuple(cats), tuple(out), source)


def load_glossary(path: str | Path | None = None, metrics: MetricsConfig | None = None) -> Glossary:
    data, source = _read_json(path, "glossary.json")
    return validate_glossary(data, source, metrics or load_metrics_config())


@functools.lru_cache(maxsize=1)
def default_glossary() -> Glossary:
    return load_glossary()


def term_text(term: dict) -> str:
    """見出しの title などに出す 1 つの文(意味・式・良い向き)。"""
    parts = [term["meaning"]]
    if term.get("formula"):
        parts.append(f"式:{term['formula']}")
    if term.get("better"):
        parts.append(term["better"])
    return " ".join(parts)
