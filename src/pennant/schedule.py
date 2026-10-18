"""日程の生成(実装④。D-066、D-087)。

日程を作る部分は差し替えられる形にする(ScheduleMaker)。休養日・3連戦・交流戦は、別の作り方を足して入れる。
既定は RoundRobinSchedule(ラウンド制):
  - 毎日、同じリーグの全チームが1試合ずつ行う(6チームなら3試合)。2つのリーグは同じ日に並行する。
  - 1巡(5日)で総当たりが一巡し、これを「同じ相手との対戦回数」(既定25)回くり返す。
  - ホームとアウェイは、同じ相手との対戦ごとに交互にする。対戦回数が奇数のときは、
    1つ多くホームを持つ側(13試合)を、各チームが2〜3組で持つように割り振る(ホームは各チーム62か63試合)。
  - 割り振りと、各巡の中の日の並びは、シーズンのシードから作った乱数で決める。
143 試合の日程は SeriesSchedule(D-435、D-436):
  - 同じ相手との連戦(シリーズ)で組む。リーグ戦は総当たりの巡(5 シリーズ)を、巡ごとの長さ(既定 2・3・2・3・3・3・3・3・3)で
    くり返す。各ペアのホームはシリーズごとに交互で、1 つ目のシリーズは多く持つ側(home_holders)。
  - 交流戦は、リーグ戦の試合数が interleague_after 以上になった最初のシリーズの区切りに、まとめて置く(6 カード)。
  - 試合が続くのは max_run 日まで(超えるときは、シリーズの区切りに全球団の休みを 1 日入れる)。
  - オールスターは、試合数が allstar_after 以上になった最初のシリーズの区切りに、休み 4 日(その 2・3 日目に 2 試合)。
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
    league_index: int  # 交流戦はホームの球団のリーグ
    home_id: str
    away_id: str
    interleague: bool = False


@dataclass
class Schedule:
    days: list[list[ScheduledGame]] = field(default_factory=list)  # 休みの日は空
    allstar_days: list[int] = field(default_factory=list)  # オールスターの日(その日は公式戦なし。D-436)

    @property
    def games(self) -> list[ScheduledGame]:
        return [g for day in self.days for g in day]

    def __len__(self) -> int:
        return sum(len(d) for d in self.days)


class ScheduleMaker(Protocol):
    """日程の作り方の形(差し替え用)。"""

    def make(self, league: League, rng: random.Random) -> Schedule: ...


def _groups(league: League) -> dict[int, list[str]]:
    groups: dict[int, list[str]] = {}
    for t in league.teams:
        groups.setdefault(t.league_index, []).append(t.id)
    return groups


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


@dataclass
class SeriesSchedule:
    """連戦・交流戦・休み・オールスターのある日程(D-435、D-436)。year の偶奇で交流戦のホームを入れ替える。"""

    series: tuple[int, ...] = (2, 3, 2, 3, 3, 3, 3, 3, 3)
    interleague_games: int = 3
    interleague_after: int = 45
    max_run: int = 6
    allstar_after: int | None = 86
    allstar_games: int = 2
    year: int = 1

    def make(self, league: League, rng: random.Random) -> Schedule:
        groups = _groups(league)
        idx = sorted(groups)
        # 枠(シリーズ)の並び:[(長さ, [(リーグ番号, ホーム, アウェイ, 交流戦か)])]
        league_slots: list[list[tuple[int, list]]] = []
        for li in idx:
            ids = groups[li]
            holders = home_holders(ids, rng)
            rounds = round_robin_rounds(ids)
            meetings: dict[frozenset, int] = {}
            slots = []
            for length in self.series:
                order = list(range(len(rounds)))
                rng.shuffle(order)
                for r in order:
                    pairs = []
                    for a, b in rounds[r]:
                        pair = frozenset((a, b))
                        k = meetings.get(pair, 0)
                        meetings[pair] = k + 1
                        holder = holders[pair]
                        other = b if holder == a else a
                        pairs.append((li, *((holder, other) if k % 2 == 0 else (other, holder)), False))
                    slots.append((length, pairs))
            league_slots.append(slots)
        merged = [(slots[0][0], [g for _, ps in slots for g in ps]) for slots in zip(*league_slots)]
        inter = self._interleague(groups, idx, rng) if len(idx) == 2 and self.interleague_games > 0 else []
        timeline: list[tuple[int, list]] = []
        played = 0
        for length, games in merged:
            if inter and played >= self.interleague_after:
                timeline += inter
                inter = []
            timeline.append((length, games))
            played += length
        timeline += inter
        return self._lay_out(timeline)

    def _interleague(self, groups, idx, rng) -> list[tuple[int, list]]:
        a, b = list(groups[idx[0]]), list(groups[idx[1]])
        if len(a) != len(b):
            raise ValueError("交流戦は、2 つのリーグのチーム数が同じときだけ作れます")
        rng.shuffle(a)
        rng.shuffle(b)
        ks = list(range(len(b)))
        rng.shuffle(ks)
        slots = []
        for n, k in enumerate(ks):
            first_home = (k + self.year) % 2 == 0  # どの球団も半分のカードがホーム。年ごとに入れ替え
            games = []
            for i, x in enumerate(a):
                y = b[(i + k) % len(b)]
                home, away, li = (x, y, idx[0]) if first_home else (y, x, idx[1])
                games.append((li, home, away, True))
            slots.append((self.interleague_games, games))
        return slots

    def _lay_out(self, timeline: list[tuple[int, list]]) -> Schedule:
        schedule = Schedule()
        days: list[list] = []
        run = 0
        played = 0
        allstar_done = self.allstar_after is None
        for length, games in timeline:
            if not allstar_done and played >= self.allstar_after:
                days.append([])  # オールスターの休み:休み・試合・試合・休み
                for _ in range(self.allstar_games):
                    schedule.allstar_days.append(len(days))
                    days.append([])
                days.append([])
                allstar_done = True
                run = 0
            elif run and run + length > self.max_run:
                days.append([])
                run = 0
            for _ in range(length):
                days.append(games)
            run += length
            played += length
        number = 0
        for d, games in enumerate(days):
            day = []
            for li, home, away, inter in games:
                day.append(ScheduledGame(number, d, li, home, away, inter))
                number += 1
            schedule.days.append(day)
        return schedule


def make_schedule_maker(config: dict, year: int = 1) -> ScheduleMaker:
    """シーズンの設定(season.json の schedule)から日程の作り方を選ぶ。series がなければ今までのラウンド制(版 26 の続き。D-438)。"""
    if "series" not in config:
        return RoundRobinSchedule(int(config["games_per_opponent"]))
    il = config.get("interleague", {})
    al = config.get("allstar")
    return SeriesSchedule(
        series=tuple(int(x) for x in config["series"]),
        interleague_games=int(il.get("games", 0)),
        interleague_after=int(il.get("after_games", 0)),
        max_run=int(config.get("max_consecutive_days", 6)),
        allstar_after=None if not al else int(al["after_games"]),
        allstar_games=0 if not al else int(al.get("games", 2)),
        year=year,
    )
