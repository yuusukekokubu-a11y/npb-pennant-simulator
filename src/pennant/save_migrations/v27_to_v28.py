"""版 27 → 版 28(D-441):ポストシーズン(D-439、D-440)。

設定(configs.season)はそのまま(postseason の値がないので、今のシーズンはポストシーズンなし)。
次のシーズンを作るときに、今の data/season.json の設定に置き換える(api/offseason.py)。
"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
