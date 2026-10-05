"""セーブデータの変換:版 10 → 版 11。"""

from __future__ import annotations

import copy

from ..contracts import load_contract_settings
from ..negotiation import load_negotiation_settings


def v10_to_v11(bundle: dict) -> dict:
    """版10には志望と交渉がない(F3-2b。D-253):志望は読み込みの後にシードから補う。契約の設定の年数は新しい既定(1 年)に置き換え、
    交渉の設定は設定ファイルの値を使う。進行中の手続き(更改は済んでいる)は、自由契約の段階から続ける(from_dict が更改の結果を交渉の形に直す)。"""
    state = bundle["state"]
    cfg = state.get("configs", {})
    ct = cfg.get("contracts")
    if isinstance(ct, dict):
        ct["years"] = copy.deepcopy(load_contract_settings().data["years"])
    cfg.setdefault("negotiation", copy.deepcopy(load_negotiation_settings().data))
    for team in state.get("league", {}).get("teams", []):
        for pd in team.get("players", []):
            if isinstance(pd, dict):
                pd.setdefault("preference", None)
    proc = state.get("procedure")
    if isinstance(proc, dict):
        for key in ("candidates", "market"):
            for pd in proc.get(key, []):
                if isinstance(pd, dict):
                    pd.setdefault("preference", None)
    return bundle
