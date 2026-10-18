"""版 26 → 版 27(D-438):143 試合の日程(D-435〜D-437)。

設定(configs.season)はそのまま(連戦の値 series がないので、今のシーズンは今までの 125 試合・毎日 1 試合の日程で続く)。
次のシーズンを作るときに、今の data/season.json の日程に置き換える(api/offseason.py)。
オールスターの結果はまだない(空)。履歴の交流戦とオールスターもない。
"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    season = bundle["state"].get("season")
    if isinstance(season, dict):
        season.setdefault("allstar", [])
    return bundle
