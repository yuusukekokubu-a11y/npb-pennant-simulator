"""今有効な決定の一覧(docs/ACTIVE_DECISIONS.md)のずれの点検(D-289、D-295)。

決定ログ(docs/DECISIONS.md)の D-001〜最新の番号が、一覧の本文か「載せなかった決定」のどちらかに必ず出ること。
決定を足したのに一覧を直し忘れると、このテストが失敗する。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _decisions() -> list[int]:
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    return sorted({int(n) for n in re.findall(r"^### D-(\d{3}) ", text, re.M)} - {0})  # D-000 は書式の例


def _active() -> tuple[str, str]:
    text = (ROOT / "docs" / "ACTIVE_DECISIONS.md").read_text(encoding="utf-8")
    body, excluded = text.split("## 載せなかった決定", 1)
    return body, excluded


def test_every_decision_appears_in_the_active_list():
    numbers = _decisions()
    assert numbers == list(range(1, numbers[-1] + 1)), "決定の番号に抜けがあります"
    body, excluded = _active()
    listed = {int(n) for n in re.findall(r"D-(\d{3})", body)}
    left_out = {int(n) for n in re.findall(r"D-(\d{3})", excluded.split("載せた決定の数")[0])}
    missing = [n for n in numbers if n not in listed and n not in left_out]
    assert not missing, "今有効な決定の一覧に出ていない決定があります: " + "、".join(f"D-{n:03d}" for n in missing)


def test_excluded_decisions_are_not_cited_as_sources():
    """置き換え・取り下げで外した決定(「D-xxx → D-yyy」の左側)を、一覧の本文の出典として書かない。"""
    body, excluded = _active()
    replaced = {int(n) for n in re.findall(r"^- D-(\d{3}) →", excluded, re.M)}
    sources = {int(n) for part in re.findall(r"[((]([^()()]*)[))]\s*$", body, re.M) for n in re.findall(r"D-(\d{3})", part)}
    assert not (replaced & sources), "外した決定が出典に残っています: " + "、".join(f"D-{n:03d}" for n in sorted(replaced & sources))


def test_header_and_counts_follow_the_latest_decision():
    numbers = _decisions()
    body, excluded = _active()
    assert f"最終更新:D-{numbers[-1]:03d} まで反映" in body
    left_out = re.findall(r"^- D-(\d{3}) →", excluded, re.M)
    once = re.findall(r"D-(\d{3})", excluded.split("### (b)")[1].split("---")[0])
    m = re.search(r"載せた決定の数[((]元の D 番号の数[))](\d+)、外した数 (\d+)[((]置き換え・取り下げ (\d+)、一度きりの段取り (\d+)[))]", excluded)
    assert m, "数の行が見つかりません"
    listed, out, a, b = map(int, m.groups())
    assert (a, b) == (len(left_out), len(once)) and out == a + b and listed + out == len(numbers)
