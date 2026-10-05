"""セーブデータの変換:版 4 → 版 5。"""

from __future__ import annotations


def v4_to_v5(bundle: dict) -> dict:
    """版4には球場 × シーズンの集計の履歴がない。履歴なし(球場補正 1.0)として足す(D-146)。"""
    bundle["state"].setdefault("park_history", [])
    return bundle
