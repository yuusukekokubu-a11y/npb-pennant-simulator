"""1シーズンの進行と順位表(実装④。D-066、D-073、D-087)。

日程(schedule.py)どおりに、1日ずつ試合(game.py)を積み重ねる。
  - 1日の流れ:各チームのスタメンと先発を決める → 試合 → 疲労を加える → 1日を進めて回復させる。
  - 先発は6人ローテーション。疲労が大きい投手は飛ばす(manager.py)。
  - 試合ごとの乱数のシードは、シーズンのシードと試合の通し番号から、hashlib(SHA-256)で決める。
    Python の版や環境に依存しない(組み込みの hash() は使わない)。1試合だけを再現できる(replay_game)。
  - 「1日進める」「何日か進める」「最後まで進める」は、進み具合を呼び出し側に知らせられる(progress)。
  - 順位表は、勝率 → 勝利数 → 直接対決の成績 の順。最後まで同じなら、シーズンのシードで決めた抽選の順。
    同順位を許す設定では、勝率が同じチームを同じ順位にする。
  - ポストシーズンは、SeasonResult を受け取る別の処理として、後から足せる。

渡したリーグの選手の「状態」(疲労)は、シーズンの進行で書き換わる。
"""

from __future__ import annotations

import copy
import hashlib
import random
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

from .fatigue import advance_day, apply_game_fatigue
from .game import GameResult, simulate_game
from .game_config import GameConfig, load_game_config
from .manager import ActiveRoster, Manager, SimpleManager
from .models import League, Team
from .plate_appearance import PlateAppearanceModel, default_model
from .schedule import RoundRobinSchedule, Schedule, ScheduledGame, ScheduleMaker
from .season_config import SeasonConfig, load_season_config

# 進み具合を知らせる関数:(終わった日数, 全日数, 終わった試合数, 全試合数)
Progress = Callable[[int, int, int, int], None]


def derive_seed(seed: int, label: str) -> int:
    """シーズンのシードと名札から、別の乱数のシードを作る(版や環境に依存しない)。"""
    digest = hashlib.sha256(f"pennant:{seed}:{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass
class GameContext:
    """1試合を再現するための、試合前の状態(ローテーションの位置と、投手の疲労)。"""

    rotation: dict[str, int]
    fatigue: dict[str, float]


@dataclass
class PlayedGame:
    scheduled: ScheduledGame
    result: GameResult
    context: GameContext
    starter_skipped: dict[str, bool]  # チーム ID → 疲労で、ローテーションの順番の先発を飛ばしたか


@dataclass
class StandingRow:
    team_id: str
    name: str
    wins: int
    losses: int
    ties: int
    pct: float  # 勝率 = 勝利数 ÷(勝利数 + 敗戦数)。引き分けは分母に入れない
    games_behind: float  # 首位とのゲーム差
    games_behind_prev: float  # 1つ上の順位のチームとのゲーム差
    rank: int

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties


@dataclass
class DayResult:
    day: int  # 0 から
    games: list[PlayedGame]


@dataclass
class SeasonResult:
    standings: dict[int, list[StandingRow]]  # リーグ番号 → 最終順位表
    champions: dict[int, list[str]]  # リーグ番号 → 優勝チームの ID(同順位を許す設定では複数のことがある)
    games: list[PlayedGame]  # 全試合の結果(打席ログを含む)
    schedule: Schedule


@dataclass
class _Record:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    vs: dict[str, list[int]] = field(default_factory=dict)  # 相手 ID → [勝, 敗, 分]


def _pct(wins: int, losses: int) -> Fraction:
    return Fraction(wins, wins + losses) if wins + losses else Fraction(0)


class Season:
    """1シーズンを日ごとに進める。"""

    def __init__(
        self,
        league: League,
        seed: int,
        season_config: SeasonConfig | None = None,
        game_config: GameConfig | None = None,
        model: PlateAppearanceModel | None = None,
        manager: Manager | None = None,
        schedule_maker: ScheduleMaker | None = None,
    ):
        self.league = league
        self.seed = seed
        self.season_config = season_config or load_season_config()
        self.game_config = game_config or load_game_config()
        self.model = model or default_model()
        self.manager = manager or SimpleManager(self.game_config)
        maker = schedule_maker or RoundRobinSchedule(self.season_config["schedule"]["games_per_opponent"])
        self.schedule = maker.make(league, random.Random(derive_seed(seed, "schedule")))
        self.teams: dict[str, Team] = {t.id: t for t in league.teams}
        self.actives: dict[str, ActiveRoster] = {t.id: self.manager.select_active(t) for t in league.teams}
        self.rotation: dict[str, int] = {t.id: 0 for t in league.teams}
        self.players = {p.id: p for p in league.all_players()}
        self.pitchers = [p for a in self.actives.values() for p in a.starters + a.relievers]
        self.day = 0
        self.played: list[PlayedGame] = []
        self._records: dict[str, _Record] = {t.id: _Record() for t in league.teams}

    # ---- 進行 ----

    @property
    def total_days(self) -> int:
        return len(self.schedule.days)

    @property
    def is_over(self) -> bool:
        return self.day >= self.total_days

    def game_rng(self, number: int) -> random.Random:
        """試合の通し番号から、その試合の乱数を作る。"""
        return random.Random(derive_seed(self.seed, f"game:{number}"))

    def _play(self, g: ScheduledGame) -> PlayedGame:
        home, away = self.teams[g.home_id], self.teams[g.away_id]
        context = GameContext(
            rotation={home.id: self.rotation[home.id], away.id: self.rotation[away.id]},
            fatigue={p.id: p.state.fatigue for t in (home, away) for p in self._active_pitchers(t.id)},
        )
        rng = self.game_rng(g.number)
        skipped = {}
        setups = {}
        for team in (home, away):
            active = self.actives[team.id]
            expected = active.starters[self.rotation[team.id] % len(active.starters)]
            setup, self.rotation[team.id] = self.manager.prepare(team, rng, self.rotation[team.id], active)
            skipped[team.id] = setup.starter is not expected
            setups[team.id] = setup
        result = simulate_game(setups[home.id], setups[away.id], rng, model=self.model, config=self.game_config, manager=self.manager, park=home.park)
        apply_game_fatigue(result.batters_faced(), self.players, self.game_config)
        self._record(result)
        return PlayedGame(g, result, context, skipped)

    def _active_pitchers(self, team_id: str):
        a = self.actives[team_id]
        return a.starters + a.relievers

    def _record(self, r: GameResult) -> None:
        h, a = self._records[r.home_team_id], self._records[r.away_team_id]
        hv = h.vs.setdefault(r.away_team_id, [0, 0, 0])
        av = a.vs.setdefault(r.home_team_id, [0, 0, 0])
        if r.tie:
            h.ties += 1
            a.ties += 1
            hv[2] += 1
            av[2] += 1
        elif r.home_runs > r.away_runs:
            h.wins += 1
            a.losses += 1
            hv[0] += 1
            av[1] += 1
        else:
            a.wins += 1
            h.losses += 1
            av[0] += 1
            hv[1] += 1

    def play_day(self) -> DayResult:
        """1日分の試合を行い、1日を進めて投手の疲労を回復させる。"""
        if self.is_over:
            raise ValueError("シーズンは終わっています")
        games = [self._play(g) for g in self.schedule.days[self.day]]
        self.played.extend(games)
        advance_day(self.pitchers, self.game_config)
        result = DayResult(self.day, games)
        self.day += 1
        return result

    def play_days(self, n: int, progress: Progress | None = None) -> list[DayResult]:
        """n 日進める(シーズンの終わりで止まる)。progress には1日ごとに進み具合を知らせる。"""
        days = []
        total_games = len(self.schedule)
        for _ in range(n):
            if self.is_over:
                break
            days.append(self.play_day())
            if progress:
                progress(self.day, self.total_days, len(self.played), total_games)
        return days

    def play_to_end(self, progress: Progress | None = None) -> SeasonResult:
        """シーズンの最後まで進め、結果を返す。"""
        self.play_days(self.total_days - self.day, progress)
        return self.result()

    # ---- 再現 ----

    def replay_game(self, number: int) -> GameResult:
        """行った試合を、試合前の状態とシーズンのシードから、もう一度計算する(シーズンの状態は変えない)。"""
        played = next(p for p in self.played if p.scheduled.number == number)
        g = played.scheduled
        teams = [self.teams[g.home_id], self.teams[g.away_id]]
        copied_teams, copied_actives = copy.deepcopy((teams, [self.actives[t.id] for t in teams]))
        for t, a in zip(copied_teams, copied_actives):
            for p in a.starters + a.relievers:
                p.state.fatigue = played.context.fatigue[p.id]
        rng = self.game_rng(number)
        setups = [
            self.manager.prepare(t, rng, played.context.rotation[t.id], a)[0] for t, a in zip(copied_teams, copied_actives)
        ]
        return simulate_game(setups[0], setups[1], rng, model=self.model, config=self.game_config, manager=self.manager, park=copied_teams[0].park)

    # ---- 順位表 ----

    def standings(self, league_index: int) -> list[StandingRow]:
        teams = [t for t in self.league.teams if t.league_index == league_index]
        recs = self._records
        shared = self.season_config["standings"]["allow_shared_rank"]

        def lot(team_id: str) -> int:  # 最後まで同じときの抽選(シーズンのシードで決まる)
            return derive_seed(self.seed, f"lot:{team_id}")

        def h2h(team_id: str, group: list[str]) -> Fraction:  # 同率のチームどうしの対戦成績
            w = sum(recs[team_id].vs.get(o, [0, 0, 0])[0] for o in group if o != team_id)
            losses = sum(recs[team_id].vs.get(o, [0, 0, 0])[1] for o in group if o != team_id)
            return _pct(w, losses) if w + losses else Fraction(1, 2)

        ordered: list[str] = []
        by_pct: dict[Fraction, list[str]] = {}
        for t in teams:
            by_pct.setdefault(_pct(recs[t.id].wins, recs[t.id].losses), []).append(t.id)
        for pct in sorted(by_pct, reverse=True):
            group = by_pct[pct]
            by_wins: dict[int, list[str]] = {}
            for tid in group:
                by_wins.setdefault(recs[tid].wins, []).append(tid)
            for wins in sorted(by_wins, reverse=True):
                tied = by_wins[wins]
                ordered += sorted(tied, key=lambda tid: (-h2h(tid, tied), lot(tid)))

        rows = []
        lead = recs[ordered[0]] if ordered else None
        prev = None
        for i, tid in enumerate(ordered):
            r = recs[tid]
            pct = _pct(r.wins, r.losses)
            if shared and i > 0 and pct == _pct(recs[ordered[i - 1]].wins, recs[ordered[i - 1]].losses):
                rank = rows[-1].rank
            else:
                rank = i + 1
            gb = ((lead.wins - r.wins) + (r.losses - lead.losses)) / 2
            gbp = 0.0 if prev is None else ((prev.wins - r.wins) + (r.losses - prev.losses)) / 2
            rows.append(StandingRow(tid, self.teams[tid].name, r.wins, r.losses, r.ties, float(pct), gb, gbp, rank))
            prev = r
        return rows

    def league_indexes(self) -> list[int]:
        return sorted({t.league_index for t in self.league.teams})

    # ---- 結果 ----

    def result(self) -> SeasonResult:
        standings = {i: self.standings(i) for i in self.league_indexes()}
        champions = {i: [row.team_id for row in rows if row.rank == 1] for i, rows in standings.items()}
        return SeasonResult(standings, champions, list(self.played), self.schedule)
