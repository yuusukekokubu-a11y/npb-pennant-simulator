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
from .game import CAUGHT, PASSED_BALL, STEAL, WILD_PITCH, GameResult, steps

HITS = {"single": "B1", "double": "B2", "triple": "B3", "home_run": "HR"}
BASES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
BATTER_COUNTS = ("G", "PA", "AB", "H", "B1", "B2", "B3", "HR", "TB", "BB", "HBP", "SO", "SF", "GDP", "RBI", "R", "SB", "CS")
PITCHER_COUNTS = ("G", "GS", "OUTS", "BF", "H", "HR", "BB", "HBP", "SO", "R", "ER", "W", "L", "SV", "HLD", "CG", "WP")
FIELDER_COUNTS = ("G", "E", "SBA", "CSC", "PB")  # ポジションごと:スタメンの試合・失策・盗塁企図・盗塁阻止・捕逸(D-431)
DH = "DH"
TEAM_COUNTS = ("G", "W", "L", "T", "R", "RA")


@dataclass
class Records:
    """元の数。batters・pitchers は選手 ID → 数、teams はチーム ID → 数。"""

    batters: dict[str, Counter] = field(default_factory=dict)
    pitchers: dict[str, Counter] = field(default_factory=dict)
    teams: dict[str, Counter] = field(default_factory=dict)
    batter_team: dict[str, str] = field(default_factory=dict)  # 選手 ID → 所属チーム ID
    pitcher_team: dict[str, str] = field(default_factory=dict)
    fielders: dict[str, dict[str, Counter]] = field(default_factory=dict)  # 選手 ID → ポジション → 守備の数(D-431)

    def add(self, other: "Records") -> None:
        for mine, theirs in ((self.batters, other.batters), (self.pitchers, other.pitchers), (self.teams, other.teams)):
            for key, counts in theirs.items():
                mine.setdefault(key, Counter()).update(counts)
        for pid, by_pos in other.fielders.items():
            mine = self.fielders.setdefault(pid, {})
            for pos, counts in by_pos.items():
                mine.setdefault(pos, Counter()).update(counts)
        self.batter_team.update(other.batter_team)
        self.pitcher_team.update(other.pitcher_team)


def _batter(rec: Records, pid: str, team_id: str) -> Counter:
    rec.batter_team[pid] = team_id
    return rec.batters.setdefault(pid, Counter())


def _pitcher(rec: Records, pid: str, team_id: str) -> Counter:
    rec.pitcher_team[pid] = team_id
    return rec.pitchers.setdefault(pid, Counter())


def earned_flags(result: GameResult) -> list[list[bool]]:
    """打席と盗塁などの出来事(steps の順)ごとに、その生還(走者の動きの並び)が自責点かどうか(D-095 の簡易版)。

    半イニングごとに走者を追いかけ、失策で出塁した走者と、それまでの進塁に失策(捕逸を含む。D-430)が関わった走者の生還は、
    自責点にしない。
    """
    flags: list[list[bool]] = []
    tainted: dict[str, bool] = {}  # 塁上の走者 ID → 出塁・進塁に失策が関わったか
    half = None
    for x in steps(result):
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


def _fielder(rec: Records, pid: str, position: str) -> Counter:
    return rec.fielders.setdefault(pid, {}).setdefault(position, Counter())


def _count_pa(rec: Records, x, earned_row: list[bool], pitcher_team: dict[str, str]) -> None:
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
    if x.pa.result == "error" and x.fielder_id and x.pa.fielder not in (None, "P"):
        _fielder(rec, x.fielder_id, x.pa.fielder)["E"] += 1


def _count_event(rec: Records, e, earned_row: list[bool], pitcher_team: dict[str, str]) -> None:
    """盗塁・盗塁死・暴投・捕逸の数と、その生還の得点・失点(D-430)。"""
    if e.kind in (STEAL, CAUGHT):
        runner = _batter(rec, e.moves[0].player_id, e.batting_team_id)
        c = _fielder(rec, e.catcher_id, "C")
        c["SBA"] += 1
        if e.kind == STEAL:
            runner["SB"] += 1
        else:
            runner["CS"] += 1
            c["CSC"] += 1
    elif e.kind == WILD_PITCH:
        _pitcher(rec, e.pitcher_id, e.fielding_team_id)["WP"] += 1
    elif e.kind == PASSED_BALL:
        _fielder(rec, e.catcher_id, "C")["PB"] += 1
    for m, is_earned in zip([m for m in e.moves if m.scored], earned_row):
        _batter(rec, m.player_id, e.batting_team_id)["R"] += 1
        charged = _pitcher(rec, m.responsible_pitcher_id, pitcher_team[m.responsible_pitcher_id])
        charged["R"] += 1
        if is_earned:
            charged["ER"] += 1


def game_records(result: GameResult, decisions: Decisions | None = None) -> Records:
    """1試合の元の数。"""
    rec = Records()
    home, away = result.home_team_id, result.away_team_id
    for team_id, slots in result.lineups.items():
        for _, pid, pos, _ in slots:
            _batter(rec, pid, team_id)["G"] += 1
            if pos != DH:
                _fielder(rec, pid, pos)["G"] += 1
    earned = iter(earned_flags(result))
    pitcher_team = {line.pitcher_id: line.team_id for line in result.pitchers}
    for x in result.log:
        earned_row = next(earned)
        _count_pa(rec, x, earned_row, pitcher_team)
        for e in x.events:
            _count_event(rec, e, next(earned), pitcher_team)

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
