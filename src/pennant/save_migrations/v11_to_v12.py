"""セーブデータの変換:版 11 → 版 12。"""

from __future__ import annotations

import copy

from ..negotiation import load_negotiation_settings


def v11_to_v12(bundle: dict) -> dict:
    """版11には FA がない(F3-2c。D-263):FA 権の年数は読み込みの後に補う(D-264)。FA の設定は設定ファイルの値。進行中の手続きはそのまま(FA は飛ばす)。"""
    state = bundle["state"]
    neg = state.get("configs", {}).get("negotiation")
    if isinstance(neg, dict):
        neg.setdefault("fa", copy.deepcopy(load_negotiation_settings().data["fa"]))
    for team in state.get("league", {}).get("teams", []):
        for pd in team.get("players", []):
            if isinstance(pd, dict):
                pd.setdefault("fa_seasons", None)
    proc = state.get("procedure")
    if isinstance(proc, dict):
        for key in ("candidates", "market"):
            for pd in proc.get(key, []):
                if isinstance(pd, dict):
                    pd.setdefault("fa_seasons", 0)
    return bundle
