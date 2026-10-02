"""日程の生成(実装④。D-066、D-087)。

日程を作る部分は差し替えられる形にする(ScheduleMaker)。休養日・3連戦・交流戦は、別の作り方を足して入れる。
既定は RoundRobinSchedule(ラウンド制):
  - 毎日、同じリーグの全チームが1試合ずつ行う(6チームなら3試合)。2つのリーグは同じ日に並行する。
  - 1巡(5日)で総当たりが一巡し、これを「同じ相手との対戦回数」(既定25)回くり返す。
  - ホームとアウェイは、同じ相手との対戦ごとに交互にする。対戦回数が奇数のときは、
    1つ多くホームを持つ側(13試合)を、各チームが2〜3組で持つように割り振る(ホームは各チーム62か63試合)。
  - 割り振りと、各巡の中の日の並びは、シーズンのシードから作った乱数で決める。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from .models import League, Team


@dataclass(frozen=True)
class ScheduledGame:
    """日程の中の1試合。number はシーズンの通し番号(0から)。試合の乱数のシードに使う。"""

    number: int
    day: int  # 0 から
    league_index: int
    home_id: str
    away_id: str


@dataclass
class Schedule:
    days: list[list[ScheduledGame]] = field(default_factory=list)

    @property
    def games(self) -> list[ScheduledGame]:
        return [g for day in self.days for g in day]

    def __len__(self) -> int:
        return sum(len(d) for d in self.days)


class ScheduleMaker(Protocol):
    """日程の作り方の形(差し替え用)。"""

    def make(self, league: League, rng: random.Random) -> Schedule: ...


def round_robin_rounds(team_ids: list[str]) -> list[list[tuple[str, str]]]:
    """総当たりの1巡を、1日ずつの組み合わせに分ける(円の方式)。チーム数は偶数。"""
    n = len(team_ids)
    if n % 2:
        raise ValueError("ラウンド制の日程は、リーグのチーム数が偶数のときだけ作れます")
    order = list(team_ids)
    rounds = []
    for _ in range(n - 1):
        rounds.append([(order[i], order[n - 1 - i]) for i in range(n // 2)])
        order = [order[0], order[-1]] + order[1:-1]  # 先頭を固定して残りを1つずつ回す
    return rounds


def home_holders(team_ids: list[str], rng: random.Random) -> dict[frozenset, str]:
    """各ペアで、対戦回数が奇数のときに1試合多くホームを持つ側を決める。

    チームを乱数で円に並べ、円の上で1つ先・2つ先…(半周未満)の相手には自分が持ち、
    ちょうど半周先の相手とは、円の前半のチームが持つ。6チームなら、各チームが2組か3組を持つ。
    """
    n = len(team_ids)
    circle = list(team_ids)
    rng.shuffle(circle)
    holders = {}
    for i, a in enumerate(circle):
        for step in range(1, n // 2):
            b = circle[(i + step) % n]
            holders[frozenset((a, b))] = a
        if i < n // 2:
            holders[frozenset((a, circle[i + n // 2]))] = a
    return holders


@dataclass
class RoundRobinSchedule:
    """ラウンド制の日程(既定)。games_per_opponent は同じ相手との対戦回数。"""

    games_per_opponent: int = 25

    def make(self, league: League, rng: random.Random) -> Schedule:
        groups: dict[int, list[Team]] = {}
        for t in league.teams:
            groups.setdefault(t.league_index, []).append(t)
        per_league_days = []
        for league_index in sorted(groups):
            ids = [t.id for t in groups[league_index]]
            holders = home_holders(ids, rng)
            rounds = round_robin_rounds(ids)
            meetings: dict[frozenset, int] = {}
            days = []
            for _ in range(self.games_per_opponent):
                order = list(range(len(rounds)))
                rng.shuffle(order)  # 巡ごとに日の並びを入れ替える
                for r in order:
                    day = []
                    for a, b in rounds[r]:
                        pair = frozenset((a, b))
                        k = meetings.get(pair, 0)
                        meetings[pair] = k + 1
                        holder = holders[pair]
                        other = b if holder == a else a
                        home, away = (holder, other) if k % 2 == 0 else (other, holder)  # 交互。1試合目は持つ側
                        day.append((league_index, home, away))
                    days.append(day)
            per_league_days.append(days)
        schedule = Schedule()
        number = 0
        for d, parts in enumerate(zip(*per_league_days)):  # 2つのリーグを同じ日に並べる
            day = []
            for part in parts:
                for league_index, home, away in part:
                    day.append(ScheduledGame(number, d, league_index, home, away))
                    number += 1
            schedule.days.append(day)
        return schedule
