"""画面から呼ぶ操作の関数のうち、設備(D-383):全球団の設備の段階・あなたの球団の段階を変える(オフの契約の段階だけ)。"""

from __future__ import annotations

from .. import facilities as facmod
from ..draft import stage_of


class FacilitiesMixin:
    """Game の一部:設備。"""

    def _facility_editable(self) -> tuple[bool, str]:
        state = self.state
        if state.my_team_id is None:
            return False, "操作する球団がありません(観戦のみ)"
        proc = state.procedure
        if proc is None or stage_of(proc.phase) != "contract":
            return False, "設備は、オフの手続きの契約の段階で変えられます"
        return True, ""

    def facilities_view(self) -> dict:
        """設備の画面の情報(公開用):段階ごとの維持費と効き、全球団の段階、あなたの球団の資金と年俸に使える額、変えられるか。"""
        state = self.state
        s = state.facility_settings
        editable, reason = self._facility_editable()
        my = state.my_team_id

        def effect_text(kind, level):
            v = s.effect(kind, level)
            if kind == "medical":
                return "標準" if abs(v - 1.0) < 1e-9 else f"下がり方 {'−' if v < 1 else '+'}{abs(round((1 - v) * 100))}%"
            return "標準" if abs(v - 1.0) < 1e-9 else f"伸び {'+' if v > 1 else '−'}{abs(round((v - 1) * 100))}%"

        kinds = [{"kind": k, "label": s.label(k), "options": [{"level": lv, "upkeep": s.upkeep(lv), "upkeep_text": f"{s.upkeep(lv):,} 万円", "effect": effect_text(k, lv)} for lv in range(1, s.levels + 1)]} for k in s.kinds]
        teams = []
        for t in state.league.teams:
            lv = facmod.levels_of(t, s)
            teams.append({"team_id": t.id, "team_name": t.name, "is_mine": t.id == my, "levels": lv, "upkeep": facmod.team_upkeep(t, s), "upkeep_text": f"{facmod.team_upkeep(t, s):,} 万円"})
        out = {"editable": editable, "reason": reason, "levels": s.levels, "standard": s.standard, "kinds": kinds, "teams": teams}
        if my is not None:
            out["mine"] = {**next(x for x in teams if x["is_mine"]), "budget": self.budget_info(my)}
        return out

    def set_facility(self, kind: str, level: int) -> dict:
        """あなたの球団の設備の段階を変える(オフの契約の段階だけ。何段でも)。年俸に使える額がすぐ変わる。戻り値は設備の画面の情報。"""
        state = self.state
        editable, reason = self._facility_editable()
        if not editable:
            raise ValueError(reason)
        s = state.facility_settings
        if kind not in s.kinds:
            raise ValueError(f"知らない設備です({kind})")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= s.levels:
            raise ValueError(f"段階は 1〜{s.levels} にしてください(値: {level!r})")
        team = self._team(state.my_team_id)
        team.facilities = {**facmod.levels_of(team, s), kind: int(level)}
        self.dirty = True
        return self.facilities_view()
