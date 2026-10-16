"""版 24 → 版 25(D-426):調子(D-424)と一軍の入れ替え(D-425)。

版 24 のシーズンは、読み込んだ日から調子を始める(condition.start = その日)。それより前の試合を再現するときのため、
選手ごとの好調・不調(state.form)を legacy_form に残す。一軍は、版 24 と同じ選び方(総合だけ)の顔ぶれから続ける(legacy)。
"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    state = bundle["state"]
    season = state.get("season")
    if not isinstance(season, dict):
        return bundle
    forms = {}
    for team in (state.get("league") or {}).get("teams") or []:
        for pd in list(team.get("players") or []) + list(team.get("development") or []):
            try:
                v = float(pd["state"]["form"])
            except (KeyError, TypeError, ValueError):
                continue
            if v:
                forms[str(pd["id"])] = round(v, 6)
    season.setdefault("condition", {"start": int(season.get("day", 0) or 0), "legacy": True, "legacy_form": forms})
    return bundle
