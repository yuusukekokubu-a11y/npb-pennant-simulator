"""版 32 → 版 33(D-479):球団職員(D-469〜D-478)。

- 球団に職員(staff・staff_manual・staff_dead)、リーグに職員の市場(staff_free)と担当の記録(staff_log)を足した。
  版 32 にはないので、印(league.staff_init)を付け、読み込みの最後(savegame.load_game)に全球団へ初期の職員を付ける
  (新規開始と同じく、リーグのシードから決める。同じセーブデータなら同じ職員)。担当の記録は空から始める。
- 版 32 の途中のオフの手続きは、職員の段階を飛ばす(手続きに staff_on がないため)。
- 打順の計画の監督の腕の段階(manager)は、ないときは 0(今までと同じ采配)。
"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    league = (bundle["state"] or {}).get("league")
    if isinstance(league, dict):
        league["staff_init"] = True
        league.setdefault("staff_free", [])
        league.setdefault("staff_log", [])
    return bundle
