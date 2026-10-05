"""セーブデータの変換:版 8 → 版 9。"""

from __future__ import annotations

import copy

from ..draft import load_draft_settings


def v8_to_v9(bundle: dict) -> dict:
    """版8のずれの値は 1 つの数。2 層の形({common, item}。合計は元の値のまま)に直す。設定の段階の値は今の設定ファイルに置き換える(D-215)。
    進行中の手続きと入団時の評価は、方式の版 1(旧方式)として読む。"""
    from ..scouting import total_from_single

    state = bundle["state"]
    state["scout_sd"] = {tid: (v if isinstance(v, dict) else total_from_single(v)) for tid, v in state.get("scout_sd", {}).items()}
    cfg = state.get("configs", {})
    draft = cfg.get("draft")
    if isinstance(draft, dict) and isinstance(draft.get("scouting"), dict):
        levels = draft["scouting"].get("levels")
        if isinstance(levels, dict) and any(not isinstance(v, dict) for v in levels.values()):
            draft["scouting"]["levels"] = copy.deepcopy(load_draft_settings().data["scouting"]["levels"])
    proc = state.get("procedure")
    if isinstance(proc, dict):
        proc.setdefault("method", 1)
    for team in state.get("league", {}).get("teams", []):
        for pd in team.get("players", []):
            if isinstance(pd, dict) and isinstance(pd.get("scouting"), dict):
                pd["scouting"].setdefault("method", 1)
    return bundle
