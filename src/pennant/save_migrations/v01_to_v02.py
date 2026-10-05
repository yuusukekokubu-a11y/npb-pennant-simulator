"""セーブデータの変換:版 1 → 版 2。"""

from __future__ import annotations


def v1_to_v2(bundle: dict) -> dict:
    """版1には自球団の情報がない。「自球団なし」として足す。"""
    bundle["state"].setdefault("user", {"my_team_id": None})
    return bundle
