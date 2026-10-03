"""シーズンごとの集計(履歴)の形(F2。D-182、D-189)。

年度の確定のときに、終わったシーズンの集計を SeasonArchive にして GameState.history に足す。
  - 順位表・優勝・球団の成績(勝敗・得点・失点)
  - 選手 × シーズンの元の数(records)、球場補正、WAR の内訳(分数を文字に)
  - 選手の名前・年齢・ポジション・球団の写し(引退した後も表示できるように)
  - 最終の基準値(過去シーズンの指標を同じ値で出し直すため)
打席ログ(games)は直近 log_seasons シーズンだけ残し、それ以前は None(D-189)。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Mapping

from .baselines import Baselines
from .records import Records
from .war import WarLine


@dataclass
class ArchivedStanding:
    league_index: int
    rank: int
    team_id: str
    wins: int
    losses: int
    ties: int
    games_behind: Fraction

    def to_dict(self) -> dict:
        return {"league_index": self.league_index, "rank": self.rank, "team_id": self.team_id, "wins": self.wins, "losses": self.losses, "ties": self.ties, "games_behind": str(self.games_behind)}

    @classmethod
    def from_dict(cls, d: Mapping) -> "ArchivedStanding":
        return cls(int(d["league_index"]), int(d["rank"]), str(d["team_id"]), int(d["wins"]), int(d["losses"]), int(d["ties"]), Fraction(d["games_behind"]))


@dataclass
class SeasonArchive:
    year: int  # 何シーズン目か(1 から)
    seed: int  # そのシーズンのシード
    standings: list[ArchivedStanding]
    champions: dict[int, list[str]]
    records: Records  # 選手 × シーズンの元の数(batters / pitchers / teams)
    players: dict[str, dict]  # 選手 ID → {name, team_id, role, position, age}(そのシーズン時点の写し)
    park_factors: dict[str, Fraction]  # 打者の球場補正(選手 ID → 値)
    war: dict[str, WarLine]
    baselines: Baselines  # 最終の基準値(混ぜた値)
    games: list | None = None  # 打席ログつきの試合の結果(残しているときだけ)
    day: int = 0
    total_days: int = 0

    def to_dict(self) -> dict:
        return {
            "year": self.year,
            "seed": self.seed,
            "day": self.day,
            "total_days": self.total_days,
            "standings": [s.to_dict() for s in self.standings],
            "champions": {str(k): list(v) for k, v in self.champions.items()},
            "records": {
                "batters": {pid: dict(c) for pid, c in self.records.batters.items()},
                "pitchers": {pid: dict(c) for pid, c in self.records.pitchers.items()},
                "teams": {tid: dict(c) for tid, c in self.records.teams.items()},
                "batter_team": dict(self.records.batter_team),
                "pitcher_team": dict(self.records.pitcher_team),
            },
            "players": {pid: dict(info) for pid, info in self.players.items()},
            "park_factors": {pid: str(v) for pid, v in self.park_factors.items()},
            "war": {pid: _war_to_dict(v) for pid, v in self.war.items()},
            "baselines": self.baselines.to_dict(),
            "has_games": self.games is not None,
        }

    @classmethod
    def from_dict(cls, d: Mapping, games: list | None = None) -> "SeasonArchive":
        rec = Records()
        r = d["records"]
        rec.batters = {pid: Counter({k: int(v) for k, v in c.items()}) for pid, c in r["batters"].items()}
        rec.pitchers = {pid: Counter({k: int(v) for k, v in c.items()}) for pid, c in r["pitchers"].items()}
        rec.teams = {tid: Counter({k: int(v) for k, v in c.items()}) for tid, c in r["teams"].items()}
        rec.batter_team = {k: str(v) for k, v in r["batter_team"].items()}
        rec.pitcher_team = {k: str(v) for k, v in r["pitcher_team"].items()}
        return cls(
            year=int(d["year"]),
            seed=int(d["seed"]),
            standings=[ArchivedStanding.from_dict(s) for s in d["standings"]],
            champions={int(k): list(v) for k, v in d["champions"].items()},
            records=rec,
            players={pid: dict(info) for pid, info in d["players"].items()},
            park_factors={pid: Fraction(v) for pid, v in d["park_factors"].items()},
            war={pid: _war_from_dict(pid, v) for pid, v in d["war"].items()},
            baselines=Baselines.from_dict(d["baselines"]),
            games=games,
            day=int(d.get("day", 0)),
            total_days=int(d.get("total_days", 0)),
        )


_WAR_FIELDS = ("batting", "baserunning", "fielding", "position", "replacement", "war", "fip_runs", "ra_runs", "defense_adjustment", "park_factor", "war_fip", "war_ra", "runs_per_win")


def _war_to_dict(v: WarLine) -> dict:
    d = {"role": v.role, "team_id": v.team_id, "plate_appearances": v.plate_appearances, "outs": v.outs}
    for k in _WAR_FIELDS:
        d[k] = str(getattr(v, k))
    return d


def _war_from_dict(pid: str, d: Mapping) -> WarLine:
    v = WarLine(str(d["role"]), str(d["team_id"]))
    v.plate_appearances = int(d.get("plate_appearances", 0))
    v.outs = int(d.get("outs", 0))
    for k in _WAR_FIELDS:
        if k in d:
            setattr(v, k, Fraction(d[k]))
    return v
