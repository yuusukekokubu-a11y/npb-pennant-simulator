"""用語集(UI の整理②。D-306〜D-312):必須の欄・分類・指標の式・表の列の名前がすべて載っていること・検証。"""

import copy
import json
import re
from pathlib import Path

import pytest

from pennant import api
from pennant.abilities import ITEM_LABELS
from pennant.config import ConfigError
from pennant.glossary import default_glossary, term_text, validate_glossary
from pennant.metrics import load_metrics_config

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "src" / "pennant" / "data" / "glossary.json").read_text(encoding="utf-8"))
CATEGORIES = ["指標:打撃", "指標:投球", "指標:守備と走塁", "指標:WAR", "契約とお金", "ドラフトとスカウト", "試合と球場", "画面の見方"]
# 列ではない選択肢(絞り込みの「全部」、シーズンの「今シーズン」「通算」)は、key で見分けて除く
_NOT_COLUMNS = {"all", "current", "career"}


def _strip_template(text: str) -> str:
    return re.sub(r"\$\{[^}]*\}", "", text)


def column_labels() -> dict[str, str]:
    """画面のすべての表の列の名前(と、見出しを押すと解説が出る行の名前)。名前 → 見つかった場所。"""
    out: dict[str, str] = {}
    py = list((ROOT / "src" / "pennant" / "api").glob("*.py")) + [ROOT / "src" / "pennant" / "answers.py"]
    for f in py:
        for key, label in re.findall(r'"key":\s*"([^"]*)",\s*"label":\s*"([^"]+)"', f.read_text(encoding="utf-8")):
            if key not in _NOT_COLUMNS:
                out.setdefault(label, f.name)
    for f in (ROOT / "web" / "js").glob("*.js"):
        text = f.read_text(encoding="utf-8")
        for key, label in re.findall(r'key:\s*["`]?([^,"`]*)["`]?,\s*label:\s*["`]([^"`]+)["`]', text):
            if key not in _NOT_COLUMNS:
                out.setdefault(_strip_template(label), f.name)
        for label in re.findall(r'\{\s*label:\s*["`]([^"`]+)["`],\s*values:', text):  # 見出しを押すと解説が出る行
            out.setdefault(_strip_template(label), f.name)
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for th in re.findall(r'<th[^>]*scope="col"[^>]*>(.*?)</th>', html):
        out.setdefault(re.sub(r"<[^>]+>", "", th), "index.html")
    config = load_metrics_config()
    for role in ("batter", "pitcher"):
        for k, v in config["counts"][role].items():
            out.setdefault(v, "metrics.json counts")
    for m in config.metrics.values():
        out.setdefault(m["name"], "metrics.json")
    for v in ITEM_LABELS.values():
        out.setdefault(v, "abilities.ITEM_LABELS")
    return out


def test_required_fields_and_categories():
    g = default_glossary()
    assert [c["label"] for c in g.categories] == CATEGORIES  # 分類(D-307)
    keys = {c["key"] for c in g.categories}
    for t in g.terms:
        assert t["name"].strip() and t["meaning"].strip() and t["category"] in keys, t
        assert len(t["meaning"]) <= 120, f"{t['name']}:意味は 1〜2 行に(D-308)"
    assert all(any(t["category"] == k for t in g.terms) for k in keys)  # どの分類にも項目がある


def test_every_column_label_is_in_the_glossary():
    """表の列の名前が、すべて用語集に載っていること(D-311)。"""
    g = default_glossary()
    labels = column_labels()
    assert len(labels) > 100
    missing = {label: where for label, where in labels.items() if label.strip() and not label.strip().isdigit() and g.find(label) is None}
    assert not missing, f"用語集に載っていない列の名前があります:{missing}"


def test_metrics_formula_and_better_come_from_metrics_json():
    g = default_glossary()
    config = load_metrics_config()
    for mid, m in config.metrics.items():
        t = g.find(m["name"])
        assert t is not None and t.get("metric") == mid and t["formula"] and t["better"], mid
    assert g.find("K%")["better"] == "打者は低いほど良い・投手は高いほど良い"
    assert g.find("防御率")["formula"] == "自責点 × 27 ÷ アウトの数" and g.find("防御率")["better"] == "低いほど良い"
    assert g.find("wOBA")["full"].startswith("weighted On-Base Average")
    assert g.find("wOBA")["formula"].startswith("(四球の重み × 四球 + ")
    for name in ("長打率", "OPS", "ISO"):
        assert "サヨナラ" in g.find(name)["meaning"]
    assert "運" in g.find("BABIP")["meaning"]


def test_find_by_alias_and_parenthesis():
    g = default_glossary()
    assert g.find("年俸(万円)")["name"] == "年俸"
    assert g.find("投球回(アウトの数)")["name"] == "投球回"
    assert g.find("失点版")["name"] == "WAR(失点版)"
    assert g.find("主な選手(今季の WAR・年齢)")["name"] == "主な選手"
    assert g.find("存在しない言葉") is None
    assert term_text(g.find("勝率")) == "勝 ÷(勝 + 敗)。引き分けは数えない。 式:勝 ÷(勝 + 敗) 高いほど良い"


def test_game_rules_and_new_game_settings_are_terms():
    """ゲームのルール(D-309)と、新規開始の説明バーで引く設定の項目(D-313)。"""
    g = default_glossary()
    for name in ("お金のルール", "志望", "FA 権", "ドラフト", "自由契約市場", "天井", "ふれ幅", "答え合わせモード"):
        assert g.find(name) is not None, name
    newgame = (ROOT / "web" / "js" / "newgame.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'"(setting\.[a-z_.]+)"', newgame))
    for rule in ("none", "loose", "standard", "strict"):
        ids.add(f"setting.money_rule.{rule}")
    assert {"setting.team_name", "setting.my_team", "setting.money_rule"} <= ids
    for i in ids:
        assert g.by_id(i) is not None, i


def test_glossary_has_no_hidden_words():
    """用語集は答え合わせモードがオフでも画面に届くので、隠し情報の言葉(成長タイプ・生成時の型・球質・役割の名前)を書かない(D-108)。"""
    from pennant.config import load_generation_config

    c = load_generation_config()
    words = {v["label"] for v in c["aging"]["growth_types"].values()} | {v["label"] for v in c["batter_archetypes"].values()}
    words |= {v["label"] for v in c["pitcher_qualities"].values()} | {v["label"] for v in c["pitcher_roles"].values()}
    words -= {"標準"}
    text = json.dumps(api.glossary_view(), ensure_ascii=False)
    assert words and not [w for w in words if w in text]


def test_glossary_view_for_the_screen():
    v = api.glossary_view()
    assert [c["label"] for c in v["categories"]] == CATEGORIES
    assert len(v["terms"]) == len(DATA["terms"]) and all("formula" in t for t in v["terms"] if t.get("metric"))
    json.dumps(v, ensure_ascii=False)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["terms"][0].update(meaning=""), "terms[0].meaning"),
        (lambda d: d["terms"][0].pop("category"), "terms[0].category"),
        (lambda d: d["terms"][1].update(category="xyz"), "分類 'xyz'"),
        (lambda d: d["terms"][1].update(name=d["terms"][0]["name"]), "重なっています"),
        (lambda d: d["terms"][0].update(metric="xyz"), "指標 'xyz'"),
        (lambda d: d["terms"][0].update(formula="H / AB"), "formula・better は書きません"),
        (lambda d: d["terms"][0].update(note="?"), "知らない欄"),
        (lambda d: d.update(terms=[t for t in d["terms"] if t.get("metric") != "fip"]), "指標 'fip'"),
        (lambda d: d.update(format_version=2), "format_version"),
    ],
)
def test_glossary_validation(change, message):
    data = copy.deepcopy(DATA)
    change(data)
    with pytest.raises(ConfigError, match=re.escape(message)):
        validate_glossary(data, "test", load_metrics_config())
