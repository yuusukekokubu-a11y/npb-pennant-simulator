"""版 18 → 版 19(D-377):育成選手(Team.development)と、選手の外国人・育成の区分、外国人市場の状態を足した。
版 18 にはないので、何もしない(読み込みで、全員が支配下・日本人、育成選手なし、手続きは外国人市場・育成ドラフトを済ませた形になる)。"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
