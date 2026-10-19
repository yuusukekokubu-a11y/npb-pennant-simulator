"""版 33 → 版 34(D-494):新規開始のときの資金力(tier_start)と、始めた後に変えた次のオフからの資金力(tier_next。D-493)。

版 33 にはないので、何もしない(読み込みで「新規開始のときの資金力 = 今の規模」「次のオフからの変更なし」になる。GameState)。
"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
