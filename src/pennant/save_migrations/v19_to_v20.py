"""版 19 → 版 20(D-379):トレード(シーズン中のトレードの記録・提案・一軍の顔ぶれの移り変わり、オフの手続きのトレードの段階)を足した。
版 19 にはないので、何もしない(読み込みで、トレードなし・提案なしになる。手続きの途中なら、トレードの段階は市場の後に来る)。"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
