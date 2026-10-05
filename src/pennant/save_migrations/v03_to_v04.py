"""セーブデータの変換:版 3 → 版 4。"""

from __future__ import annotations


def v3_to_v4(bundle: dict) -> dict:
    """版3には球場の倍率がない。すべて 1.0(1000)として足し、これまでどおりの計算を続ける(D-138)。"""
    league = bundle["state"].get("league")
    if isinstance(league, dict) and isinstance(league.get("teams"), list):
        for td in league["teams"]:
            if isinstance(td, dict):
                td.setdefault("park", {"home_run": 1000, "babip": 1000})
    return bundle
