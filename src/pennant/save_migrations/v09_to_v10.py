"""セーブデータの変換:版 9 → 版 10。"""

from __future__ import annotations


def v9_to_v10(bundle: dict) -> dict:
    """版9には契約がない。ルールは「なし」、格差なし、契約は読み込みの後に算定で補う(残りの年数は別の乱数系列で 1〜3 年。D-235)。"""
    state = bundle["state"]
    state.setdefault("money_rule", "none")
    state.setdefault("budget_tiers", {})
    state.setdefault("contract_rates", {})
    for team in state.get("league", {}).get("teams", []):
        for pd in team.get("players", []):
            if isinstance(pd, dict):
                pd.setdefault("contract", None)
    return bundle
