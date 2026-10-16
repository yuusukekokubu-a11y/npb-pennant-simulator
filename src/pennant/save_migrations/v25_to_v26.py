"""版 25 → 版 26(D-433):盗塁・暴投・捕逸(D-427)と盗塁の作戦(D-429)。

設定(configs.game)に、盗塁・暴投・捕逸の値(今の data/game.json の steal・battery)を足す。
版 25 のシーズンは、読み込んだ日から盗塁などを始める(season.running_start = その日)。それより前の試合は、盗塁などなしで再現する。
試合の記録(打席ログ)には盗塁などがないので、それまでの盗塁などは 0 として扱う。打順の計画の作戦は「普通」。
"""

from __future__ import annotations

import copy


def convert(bundle: dict) -> dict:
    from ..game_config import default_game_data

    state = bundle["state"]
    game = (state.get("configs") or {}).get("game")
    if isinstance(game, dict):
        defaults = default_game_data()
        for key in ("steal", "battery"):
            game.setdefault(key, copy.deepcopy(defaults[key]))
    season = state.get("season")
    if isinstance(season, dict):
        season.setdefault("running_start", int(season.get("day", 0) or 0))
    return bundle
