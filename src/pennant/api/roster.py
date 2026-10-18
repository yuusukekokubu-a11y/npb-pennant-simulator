"""画面から呼ぶ操作の関数のうち、自球団の一軍登録(D-425)と、選手の調子(D-424)。

- 一軍と二軍の選手を、調子・疲労・総合・今シーズンの成績と並べる。1 人ずつ入れ替える(投手は投手と、野手は野手と)。
- 「AI に任せる」なら、7 日ごとの見直しで AI が一軍を選び直す(ほかの球団と同じ基準)。
"""

from __future__ import annotations

from fractions import Fraction

from .. import ability_model
from ..abilities import POSITION_LABELS
from ..condition import public as condition_public
from .common import BATS_LABELS, THROWS_LABELS


def _avg(c) -> str:
    if not c or not c.get("AB"):
        return "-"
    s = f"{c['H'] / c['AB']:.3f}"
    return s[1:] if s.startswith("0.") else s


def _era(c) -> str:
    if not c or not c.get("OUTS"):
        return "-"
    return f"{float(Fraction(c.get('ER', 0) * 27, c['OUTS'])):.2f}"


class RosterMixin:
    """Game の一部:自球団の一軍登録(D-425)と調子(D-424)。"""

    def player_condition(self, player_id: str) -> dict | None:
        """選手の今日の調子(公開用。シーズンの選手だけ)。"""
        season = self.state.season
        if player_id not in season.players:
            return None
        return condition_public(season.condition_level(player_id))

    def _roster_check(self):
        if self.state.my_team_id is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        if self.state.procedure is not None or self.state.season.complete:
            raise ValueError("シーズン中だけ、一軍を入れ替えられます")
        return self.state.season

    def roster_view(self) -> dict:
        """一軍登録の画面(公開用):一軍(先発・救援・野手)と二軍の選手、AI に任せるか、次の見直しの日、最近の入れ替え。"""
        state = self.state
        my = state.my_team_id
        if my is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        season = state.season
        cfg = state.gen_config
        team = self._team(my)
        active = season.actives[my]
        ids = {p.id: kind for kind, xs in (("starter", active.starters), ("reliever", active.relievers), ("batter", active.batters)) for p in xs}
        rec = self.records.total
        lock_days = int(season.roster_settings()["lock_days"])

        def row(p) -> dict:
            ov = ability_model.display(cfg, ability_model.overall(p))
            c = (rec.batters if p.role == "batter" else rec.pitchers).get(p.id)
            if p.role == "batter":
                stats = f"{_avg(c)}・{int(c.get('HR', 0)) if c else 0}本"
            else:
                stats = f"{_era(c)}・{int(c.get('G', 0)) if c else 0}登板"
            left = lock_days - (season.day - season.demoted[p.id]) if p.id in season.demoted and season.day - season.demoted[p.id] < lock_days else 0
            return {
                "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws), "foreign": p.foreign,
                "overall": ov, "overall_text": f"{ability_model.rank(cfg, ov)} {ov}", "condition": condition_public(season.condition_level(p.id)),
                "fatigue": round(float(p.state.fatigue), 1) if p.role == "pitcher" else None, "stats": stats,
                "slot": ids.get(p.id), "lock_days": left, "role_label": active.relief_roles.get(p.id),
            }

        rows = [row(p) for p in team.players]
        by = {r["player_id"]: r for r in rows}
        order = {k: i for i, k in enumerate(POSITION_LABELS)}
        farm = sorted((r for r in rows if r["slot"] is None), key=lambda r: (r["role"] != "pitcher", order[r["position"]], -r["overall"], r["player_id"]))
        hist = season.active_history.get(my) or []
        recent = None
        if len(hist) >= 2:
            a, b = hist[-2], hist[-1]
            before = set(a["starters"] + a["relievers"] + a["batters"])
            after = set(b["starters"] + b["relievers"] + b["batters"])
            name = lambda pid: season.players[pid].name if pid in season.players else pid
            recent = {"day": int(b["day"]) + 1, "out": [name(x) for x in sorted(before - after)], "in": [name(x) for x in sorted(after - before)]}
        ar = season.game_config["active_roster"]
        return {
            "team_name": team.name, "day": season.day, "auto": season.roster_auto.get(my, True),
            "next_review": season.next_review(None) + 1, "lock_days": lock_days,
            "starters": [by[p.id] for p in active.starters], "relievers": [by[p.id] for p in active.relievers], "batters": [by[p.id] for p in active.batters], "farm": farm,
            "counts": {"active": len(active.players), "foreign": sum(1 for p in active.players if p.foreign), "foreign_max": ar.get("foreign_max"), "catchers": sum(1 for p in active.batters if p.position == "C"), "catchers_min": int(ar["catchers"])},
            "recent": recent,
        }

    def roster_swap(self, out_id: str, in_id: str) -> dict:
        """一軍の選手と二軍の選手を入れ替える(D-425)。"""
        season = self._roster_check()
        season.swap(self.state.my_team_id, str(out_id), str(in_id))
        self.dirty = True
        return self.roster_view()

    def roster_set_auto(self, auto: bool) -> dict:
        """「AI に任せる」の切り替え(D-425)。"""
        season = self._roster_check()
        season.roster_auto[self.state.my_team_id] = bool(auto)
        self.dirty = True
        return self.roster_view()
