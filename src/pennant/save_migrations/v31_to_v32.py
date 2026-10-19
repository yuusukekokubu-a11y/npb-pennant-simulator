"""版 31 → 版 32(D-461):チーム方針(D-454〜D-457)と送りバント(D-458)。

- 球団にチーム方針(policy)を足した。版 31 にはないので、全球団を「標準・バランス・普通」(AI が決めた扱い)で読む。次の年度の確定から AI が決める。
- 打順の計画にあった盗塁の作戦は、チーム方針へ移す。普通でなければ、その球団の方針は「あなたが決めた」にする。
- 設定(configs.game)に、送りバントの値(今の data/game.json の bunt)を足す。
- 版 31 のシーズンは、読み込んだ日から送りバントを始める(season.bunt_start = その日)。それより前の試合は、バントなしで再現する。
"""

from __future__ import annotations

import copy


def convert(bundle: dict) -> dict:
    from ..game_config import default_game_data

    state = bundle["state"]
    game = (state.get("configs") or {}).get("game")
    if isinstance(game, dict):
        game.setdefault("bunt", copy.deepcopy(default_game_data()["bunt"]))
    season = state.get("season")
    teams = {t.get("id"): t for t in ((state.get("league") or {}).get("teams") or []) if isinstance(t, dict)}
    if isinstance(season, dict):
        season.setdefault("bunt_start", int(season.get("day", 0) or 0))
        for tid, plan in (season.get("lineup_plans") or {}).items():
            if not isinstance(plan, dict):
                continue
            steal = plan.pop("steal", "normal")
            if steal != "normal" and tid in teams:
                teams[tid]["policy"] = {"steal": steal, "manual": True}
    return bundle
