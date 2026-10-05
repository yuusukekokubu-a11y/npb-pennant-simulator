"""セーブデータの変換:版 6 → 版 7。"""

from __future__ import annotations


def v6_to_v7(bundle: dict) -> dict:
    """版6には校正の定数がない。校正なし(0)として足す(旧版の選手には校正を適用しない。D-197)。"""
    bundle["state"].setdefault("calibration", {"batter": 0.0, "pitcher": 0.0})
    return bundle
