"""成績の元の数(実装⑤。D-067、D-095、D-096)。

試合の結果(GameResult:打席ログと投手の登板記録)から、選手ごと・チームごとの元の数を、その都度計算する。
成績を別に数え上げて持たない(D-096)。数はすべて整数(投球回はアウトの数)。
  - 試合ごと:game_records(result)
  - シーズン通算:season_records(results) = 試合ごとの合計
数の名前(H・AB など)は、指標の定義データ(data/metrics.json)の counts と同じ。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .baserunning import HOME
from .decisions import Decisions, decide
from .game import GameResult

HITS = {"single": "B1", "double": "B2", "triple": "B3", "home_run": "HR"}
BASES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
BATTER_COUNTS = ("G", "PA", "AB", "H", "B1", "B2", "B3", "HR", "TB", "BB", "HBP", "SO", "SF", "GDP", "RBI", "R")
PITCHER_COUNTS = ("G", "GS", "OUTS", "BF", "H", "HR", "BB", "HBP", "SO", "R", "ER", "W", "L", "SV", "HLD", "CG")
TEAM_COUNTS = ("G", "W", "L", "T", "R", "RA")


@dataclass
class Records:
    """元の数。batters・pitchers は選手 ID → 数、teams はチーム ID → 数。"""

    batters: dict[str, Counter] = field(default_factory=dict)
    pitchers: dict[str, Counter] = field(default_factory=dict)
    teams: dict[str, Counter] = field(default_factory=dict)
    batter_team: dict[str, str] = field(default_factory=dict)  # 選手 ID → 所属チーム ID
    pitcher_team: dict[str, str] = field(default_factory=dict)

    def add(self, other: "Records") -> None:
        for mine, theirs in ((self.batters, other.batters), (self.pitchers, other.pitchers), (self.teams, other.teams)):
            for key, counts in theirs.items():
                mine.setdefault(key, Counter()).update(counts)
        self.batter_team.update(other.batter_team)
        self.pitcher_team.update(other.pitcher_team)


def _batter(rec: Records, pid: str, team_id: str) -> Counter:
    rec.batter_team[pid] = team_id
    return rec.batters.setdefault(pid, Counter())


def _pitcher(rec: Records, pid: str, team_id: str) -> Counter:
    rec.pitcher_team[pid] = team_id
    return rec.pitchers.setdefault(pid, Counter())


def earned_flags(result: GameResult) -> list[list[bool]]:
    """打席ごとに、その打席の生還(走者の動きの並び)が自責点かどうか(D-095 の簡易版)。

    半イニングごとに走者を追いかけ、失策で出塁した走者と、それまでの進塁に失策が関わった走者の生還は、
    自責点にしない。
    """
    flags: list[list[bool]] = []
    tainted: dict[str, bool] = {}  # 塁上の走者 ID → 出塁・進塁に失策が関わったか
    half = None
    for x in result.log:
        if (x.inning, x.half) != half:
            half = (x.inning, x.half)
            tainted = {}
        row = []
        for m in x.moves:
            t = tainted.get(m.player_id, False) or m.reached_on_error or m.advanced_on_error
            if m.scored:
                row.append(not t)
            if m.end is None or m.end == HOME:
                tainted.pop(m.player_id, None)
            else:
                tainted[m.player_id] = t
        flags.append(row)
    return flags


def game_records(result: GameResult, decisions: Decisions | None = None) -> Records:
    """1試合の元の数。"""
    rec = Records()
    home, away = result.home_team_id, result.away_team_id
    for team_id, slots in result.lineups.items():
        for _, pid, _, _ in slots:
            _batter(rec, pid, team_id)["G"] += 1

    earned = earned_flags(result)
    pitcher_team = {line.pitcher_id: line.team_id for line in result.pitchers}
    for x, earned_row in zip(result.log, earned):
        b = _batter(rec, x.batter_id, x.batting_team_id)
        p = _pitcher(rec, x.pitcher_id, x.fielding_team_id)
        r = x.pa.result
        b["PA"] += 1
        p["BF"] += 1
        if r in HITS:
            for c in (b, p):
                c["H"] += 1
            b[HITS[r]] += 1
            b["TB"] += BASES[r]
            if r == "home_run":
                p["HR"] += 1
        elif r == "walk":
            b["BB"] += 1
            p["BB"] += 1
        elif r == "hit_by_pitch":
            b["HBP"] += 1
            p["HBP"] += 1
        elif r == "strikeout":
            b["SO"] += 1
            p["SO"] += 1
        if x.sac_fly:
            b["SF"] += 1
        if x.double_play:
            b["GDP"] += 1
        if r not in ("walk", "hit_by_pitch") and not x.sac_fly:
            b["AB"] += 1
        scored = [m for m in x.moves if m.scored]
        if not x.double_play:
            b["RBI"] += sum(1 for m in scored if not m.advanced_on_error)
        for m, is_earned in zip(scored, earned_row):
            _batter(rec, m.player_id, x.batting_team_id)["R"] += 1
            charged = _pitcher(rec, m.responsible_pitcher_id, pitcher_team[m.responsible_pitcher_id])
            charged["R"] += 1
            if is_earned:
                charged["ER"] += 1

    for line in result.pitchers:
        p = _pitcher(rec, line.pitcher_id, line.team_id)
        p["G"] += 1
        p["OUTS"] += line.outs
        if line.role == "starter":
            p["GS"] += 1
            team_lines = [x for x in result.pitchers if x.team_id == line.team_id]
            if len(team_lines) == 1:
                p["CG"] += 1

    d = decisions or decide(result)
    for key, pid in (("W", d.win), ("L", d.loss), ("SV", d.save)):
        if pid:
            rec.pitchers[pid][key] += 1
    for pid in d.holds:
        rec.pitchers[pid]["HLD"] += 1

    for team_id, runs, allowed in ((home, result.home_runs, result.away_runs), (away, result.away_runs, result.home_runs)):
        t = rec.teams.setdefault(team_id, Counter())
        t["G"] += 1
        t["R"] += runs
        t["RA"] += allowed
        if result.tie:
            t["T"] += 1
        elif result.winner == team_id:
            t["W"] += 1
        else:
            t["L"] += 1
    return rec


def season_records(results: Iterable[GameResult]) -> Records:
    """シーズン通算の元の数(試合ごとの合計)。"""
    total = Records()
    for r in results:
        total.add(game_records(r))
    return total


# ---- 規定打席・規定投球回(D-096) ----

def qualifying_plate_appearances(team_games: int) -> int:
    """規定打席 = チーム試合数 × 3.1(端数は四捨五入)。"""
    return (team_games * 31 + 5) // 10


def qualifying_outs(team_games: int) -> int:
    """規定投球回 = チーム試合数 × 1.0 回(アウトの数で返す)。"""
    return team_games * 3


def qualified_batters(rec: Records) -> list[str]:
    return [
        pid
        for pid, c in rec.batters.items()
        if c["PA"] >= qualifying_plate_appearances(rec.teams[rec.batter_team[pid]]["G"])
    ]


def qualified_pitchers(rec: Records) -> list[str]:
    return [pid for pid, c in rec.pitchers.items() if c["OUTS"] >= qualifying_outs(rec.teams[rec.pitcher_team[pid]]["G"])]
