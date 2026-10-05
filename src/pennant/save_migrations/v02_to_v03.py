"""セーブデータの変換:版 2 → 版 3。"""

from __future__ import annotations


def v2_to_v3(bundle: dict) -> dict:
    """版2には指標の基準値がない。「設定ファイルの既定値を使う」として足す(D-122)。"""
    bundle["state"].setdefault("baselines", {"source": "default"})
    return bundle
