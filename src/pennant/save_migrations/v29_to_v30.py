"""版 29 → 版 30(D-448):二軍の試合とけが(D-445、D-446)。

シーズンに二軍の試合(farm)とけが(injury_on・injury_log)、履歴に二軍の成績とけがを足した。
版 29 の続きのシーズンは、二軍・けがなしのまま(farm がない、injury_on がない = なし)。次のシーズンから始める。変換することはない。
"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
