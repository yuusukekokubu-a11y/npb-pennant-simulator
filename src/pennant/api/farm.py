"""画面から呼ぶ操作の関数のうち、二軍の試合(D-446)とけが(D-445)。

- 二軍の順位表(リーグごと)と、選手の二軍の成績(今シーズン・過去のシーズン)。
- けがの状態(何のけがか、あと何日か)と、自球団のけがの知らせ(進行の画面)。
"""

from __future__ import annotations

from fractions import Fraction

from ..abilities import ABILITY_LABELS
from ..injuries import SEASON_OUT
from .common import _gb_text, _pct_text

FARM_COLUMNS = {
    "batter": [("G", "試合"), ("PA", "打席"), ("H", "安打"), ("HR", "本塁打"), ("RBI", "打点"), ("SB", "盗塁"), ("AVG", "打率")],
    "pitcher": [("G", "登板"), ("GS", "先発"), ("W", "勝"), ("L", "敗"), ("SV", "S"), ("IP", "投球回"), ("SO", "奪三振"), ("ERA", "防御率")],
}


def _avg(c) -> str:
    if not c.get("AB"):
        return "-"
    s = f"{c['H'] / c['AB']:.3f}"
    return s[1:] if s.startswith("0.") else s


def _era(c) -> str:
    if not c.get("OUTS"):
        return "-"
    return f"{float(Fraction(c.get('ER', 0) * 27, c['OUTS'])):.2f}"


def farm_values(role: str, c) -> dict[str, str]:
    out = {}
    for key, _ in FARM_COLUMNS[role]:
        if key == "AVG":
            out[key] = _avg(c)
        elif key == "ERA":
            out[key] = _era(c)
        elif key == "IP":
            outs = int(c.get("OUTS", 0))
            out[key] = f"{outs // 3}" + (f" {outs % 3}/3" if outs % 3 else "")
        else:
            out[key] = str(int(c.get(key, 0)))
    return out


def farm_short(role: str, c) -> str:
    """一軍登録の表の短い二軍の成績。"""
    if not c:
        return "—"
    if role == "batter":
        return f"{_avg(c)}・{int(c.get('HR', 0))}本"
    return f"{_era(c)}・{int(c.get('G', 0))}登板"


class FarmMixin:
    """Game の一部:二軍の試合とけが。"""

    def injury_public(self, player_id: str) -> dict | None:
        """選手の今のけが(公開用)。けがをしていなければ None。"""
        season = self.state.season
        if not season.is_injured(player_id):
            return None
        x = season.injuries[player_id]
        out = int(x["until"]) >= SEASON_OUT
        left = None if out else int(x["until"]) - season.day
        return {"label": x["label"], "severity": x["severity"], "days_left": left, "text": f"{x['label']}(今シーズンは戻れません)" if out else f"{x['label']}(あと {left} 日)"}

    def farm_records_of(self, player_id: str):
        season = self.state.season
        if season.farm is None:
            return None
        rec = season.farm.records
        return rec.batters.get(player_id) or rec.pitchers.get(player_id)

    def farm_standings(self) -> dict:
        """二軍の順位表(リーグごと。一軍と同じ組み合わせ。D-446)。"""
        season = self.state.season
        if season.farm is None:
            return {"available": False, "leagues": [], "note": "このシーズンは二軍の試合がありません(版 29 のセーブデータの続きは、次のシーズンから)。"}
        names = self._team_names()
        lnames = self.state.league.league_names
        leagues = []
        for li in season.league_indexes():
            rows = []
            for t in self.state.league.teams:
                if t.league_index != li:
                    continue
                w, l, tie, _ = season.farm.standings[t.id]
                rows.append({"team_id": t.id, "team_name": names[t.id], "wins": w, "losses": l, "ties": tie, "pct_value": w / (w + l) if w + l else 0.0, "mine": t.id == self.state.my_team_id})
            rows.sort(key=lambda r: (-r["pct_value"], -r["wins"], r["team_id"]))
            top = rows[0] if rows else None
            for i, r in enumerate(rows):
                gb = ((top["wins"] - r["wins"]) + (r["losses"] - top["losses"])) / 2 if top else 0.0
                r.update(rank=i + 1, pct=_pct_text(r["pct_value"], r["wins"] + r["losses"]), games_behind=_gb_text(gb, i == 0))
                del r["pct_value"]
            leagues.append({"league_name": lnames[li] if li < len(lnames) else f"リーグ {li + 1}", "rows": rows})
        return {"available": True, "leagues": leagues, "games": season.farm.games, "note": "二軍の試合は、一軍の公式戦の日に同じ組み合わせで行います。一軍にいない選手(育成選手を含む。けが人を除く)が出ます。"}

    def player_farm(self, player_id: str, role: str) -> dict | None:
        """選手のページの二軍の成績(過去のシーズンと今シーズン)。一度も出ていなければ None。"""
        rows = []
        for a in self.state.history:
            if a.farm_records is None:
                continue
            c = (a.farm_records.batters if role == "batter" else a.farm_records.pitchers).get(player_id)
            if c:
                rows.append({"label": f"{a.year}シーズン目", "values": farm_values(role, c)})
        season = self.state.season
        if season.farm is not None:
            c = (season.farm.records.batters if role == "batter" else season.farm.records.pitchers).get(player_id)
            if c:
                rows.append({"label": f"{self.state.year}シーズン目(進行中)", "values": farm_values(role, c)})
        if not rows:
            return None
        return {"columns": [{"key": k, "label": label} for k, label in FARM_COLUMNS[role]], "rows": rows}

    def player_injuries(self, player_id: str) -> list[dict]:
        """選手のけがの記録(過去のシーズンと今シーズン。新しい順)。"""
        out = []
        for year, log in [(a.year, a.injuries) for a in self.state.history] + [(self.state.year, self.state.season.injury_log)]:
            for x in log:
                if x["player_id"] != player_id:
                    continue
                days = "今シーズン全休" if int(x["until"]) >= SEASON_OUT else f"{x['days']} 日"
                after = "・".join(f"{ABILITY_LABELS.get(k, k)} −{v}" for k, v in (x.get("after_items") or {}).items())
                out.append({"year": year, "day": int(x["start"]) + 1, "label": x["label"], "where": {"first": "一軍", "farm": "二軍", "post": "ポストシーズン"}.get(x.get("where"), ""), "days": days, "after": bool(x.get("after_items")), "after_text": after})
        return out[::-1]

    def my_injuries(self) -> dict | None:
        """自球団のけが(進行の画面):直前の日に起きたけがと、今けがをしている選手。"""
        my = self.state.my_team_id
        season = self.state.season
        if my is None or season.injury_settings is None:
            return None
        last = season.day - 1
        new = [{"player_id": x["player_id"], "name": x["name"], "text": self.injury_public(x["player_id"])["text"] if season.is_injured(x["player_id"]) else x["label"], "where": {"first": "一軍", "farm": "二軍", "post": "ポストシーズン"}.get(x.get("where"), "")}
               for x in season.injury_log if x["team_id"] == my and int(x["start"]) == last]
        team = self._team(my)
        current = [{"player_id": p.id, "name": p.name, "text": self.injury_public(p.id)["text"]} for p in list(team.players) + list(team.development) if season.is_injured(p.id)]
        return {"new": new, "current": current}
