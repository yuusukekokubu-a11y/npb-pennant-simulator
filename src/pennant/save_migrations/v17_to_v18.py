"""版 17 → 版 18(D-374):選手に特殊能力(Player.skills)を足した。版 17 の選手は特殊能力なしのまま読む(読み込みで空の一覧になる)。"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
