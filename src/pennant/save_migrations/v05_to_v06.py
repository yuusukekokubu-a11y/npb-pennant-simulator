"""セーブデータの変換:版 5 → 版 6。"""

from __future__ import annotations


def v5_to_v6(bundle: dict) -> dict:
    """版5には年とシーズンの履歴がない。1シーズン目・履歴なしとして足す(F2。D-189)。"""
    bundle["state"].setdefault("year", 1)
    bundle["state"].setdefault("history", [])
    bundle["state"].setdefault("offseasons", [])
    return bundle
