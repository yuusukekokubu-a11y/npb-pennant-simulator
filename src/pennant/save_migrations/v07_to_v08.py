"""セーブデータの変換:版 7 → 版 8。"""

from __future__ import annotations


def v7_to_v8(bundle: dict) -> dict:
    """版7にはオフの手続き・スカウト評価・指名の履歴がない。段階「中」・手続きなし・履歴なしとして足す(F3-1)。
    校正の定数の設定は、段階ごとの形(small / medium / large)に直す(D-209)。"""
    state = bundle["state"]
    state.setdefault("scout_level", "medium")
    state.setdefault("scout_sd", {})
    state.setdefault("procedure", None)
    state.setdefault("transactions", [])
    cfg = state.get("configs", {})
    off = cfg.get("offseason")
    if isinstance(off, dict) and isinstance(off.get("calibration"), dict) and "medium" not in off["calibration"]:
        old = off["calibration"]
        off["calibration"] = {level: dict(old) for level in ("small", "medium", "large")}
    for team in state.get("league", {}).get("teams", []):
        for pd in team.get("players", []):
            if isinstance(pd, dict):
                pd.setdefault("scouting", None)
    return bundle
