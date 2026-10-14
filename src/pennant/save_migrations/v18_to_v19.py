"""版 18 → 版 19(D-377):育成選手(Team.development)と、選手の外国人・育成の区分、外国人市場の状態を足した。

- 選手・球団・手続きの新しい項目は、読み込みで既定を補う(全員が支配下・日本人、育成選手なし、手続きは外国人市場・育成ドラフトを済ませた形)。
- 保存してある設定値(configs)には、育成と外国人の設定がないので、設定ファイルの既定の値を足す。一軍の外国人の上限(4 人)も足す。
"""

from __future__ import annotations

from ..config import _read_json


def convert(bundle: dict) -> dict:
    configs = bundle.get("state", {}).get("configs")
    if isinstance(configs, dict):
        draft = configs.get("draft")
        if isinstance(draft, dict):
            defaults = _read_json(None, "draft.json")[0]
            for key in ("foreign", "development"):
                draft.setdefault(key, defaults[key])
        names = configs.get("names")
        if isinstance(names, dict):  # 外国人選手の名前の部品(D-376)
            defaults = _read_json(None, "names.json")[0]
            for key in ("foreign_surnames", "foreign_given_names"):
                names.setdefault(key, defaults[key])
        game = configs.get("game")
        if isinstance(game, dict) and isinstance(game.get("active_roster"), dict):
            game["active_roster"].setdefault("foreign_max", 4)
    return bundle
