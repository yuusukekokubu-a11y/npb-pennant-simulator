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

from .ability_model import overall
from .condition import Conditions, level_at, settings as condition_settings
from .fatigue import advance_day, apply_game_fatigue
from .game import GameResult, simulate_game
from .game_config import GameConfig, load_game_config
from .manager import ActiveRoster, Manager, SimpleManager
from .models import League, Team
from .plate_appearance import PlateAppearanceModel, default_model
from . import allstar
from .schedule import Schedule, ScheduledGame, ScheduleMaker, make_schedule_maker
from .season_config import SeasonConfig, load_season_config

# 進み具合を知らせる関数:(終わった日数, 全日数, 終わった試合数, 全試合数)
Progress = Callable[[int, int, int, int], None]


def derive_seed(seed: int, label: str) -> int:
    """シーズンのシードと名札から、別の乱数のシードを作る(版や環境に依存しない)。"""
    digest = hashlib.sha256(f"pennant:{seed}:{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass
class GameContext:
    """1試合を再現するための、試合前の状態(ローテーションの位置と、投手の疲労。自球団の打順の型と自分で組んだスタメン。D-373)。"""

    rotation: dict[str, int]
    fatigue: dict[str, float]
    lineups: dict[str, dict] = field(default_factory=dict)  # 球団 → {style, slots}(型か自分のスタメンを決めた球団だけ)


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
        year: int = 1,
    ):
        self.league = league
        self.seed = seed
        self.season_config = season_config or load_season_config()
        self.game_config = game_config or load_game_config()
        self.model = model or default_model()
        self.manager = manager or SimpleManager(self.game_config)
        self.year = year  # 何シーズン目か(交流戦のホームを年ごとに入れ替える。D-435)
        maker = schedule_maker or make_schedule_maker(self.season_config["schedule"], year)
        self.schedule = maker.make(league, random.Random(derive_seed(seed, "schedule")))
        self.teams: dict[str, Team] = {t.id: t for t in league.teams}
        self.players = {p.id: p for p in league.all_players()}
        self.day = 0
        self.conditions = Conditions(seed, condition_settings(self.game_config))  # 調子(D-424)
        self.legacy_form: dict[str, float] = {}  # 版 24 から読んだシーズンの、調子を始める前の好調・不調(試合の再現用。D-426)
        self.demoted: dict[str, int] = {}  # 一軍から外した選手 → 外した日(10 日は戻せない。D-425)
        self.roster_auto: dict[str, bool] = {}  # 球団 → AI に任せるか(書いていない球団は任せる。自球団の設定)
        self.legacy_roster = False  # 版 24 から読んだシーズン(一軍を変えていない球団は、総合だけで選んだ顔ぶれ。D-426)
        self.apply_conditions()
        self.actives: dict[str, ActiveRoster] = {t.id: self.manager.select_active(t, self._roster_score(t.id, keep=False)) for t in league.teams}
        self.rotation: dict[str, int] = {t.id: 0 for t in league.teams}
        self.running_start = 0  # 盗塁・暴投・捕逸を始めた日(版 25 から読んだシーズンだけ 0 でない。D-433)
        self.lineup_plans: dict[str, dict] = {}  # 球団 → {style, slots([[選手 ID, 守備位置]] × 9 か None), set_day, notice}(D-373)
        self.trades: list[dict] = []  # シーズン中のトレード(trade.execute の記録。D-378)
        self.trade_offers: list[dict] = []  # AI からあなたへのトレードの提案(まだ答えていないもの)
        self.active_history: dict[str, list[dict]] = {}  # 一軍を変えた球団 → 一軍の顔ぶれの移り変わり [{day, starters, relievers, batters, roles}](試合の再現と保存。D-378、D-426)
        self.played: list[PlayedGame] = []
        self.allstar: list[dict] = []  # オールスターの結果(allstar.play の辞書。D-436)
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
            plan = self._lineup_plan(team.id)
            if plan is not None:
                context.lineups[team.id] = {"style": plan["style"], "slots": None if plan.get("slots") is None else [list(x) for x in plan["slots"]]}
                if plan.get("steal", "normal") != "normal":
                    context.lineups[team.id]["steal"] = plan["steal"]  # 盗塁の作戦(D-429)
            expected = active.starters[self.rotation[team.id] % len(active.starters)]
            setup, self.rotation[team.id] = self.manager.prepare(team, rng, self.rotation[team.id], active, plan)
            skipped[team.id] = setup.starter is not expected
            setups[team.id] = setup
        result = simulate_game(setups[home.id], setups[away.id], rng, model=self.model, config=self.game_config, manager=self.manager, park=home.park, running=g.day >= self.running_start)
        apply_game_fatigue(result.batters_faced(), self.players, self.game_config)
        self._record(result)
        return PlayedGame(g, result, context, skipped)

    def _lineup_plan(self, team_id: str) -> dict | None:
        """球団の打順の計画(D-373)。自分で組んだスタメンに一軍でない選手がいれば、AI が組み直す(知らせを残す)。"""
        plan = self.lineup_plans.get(team_id)
        if plan is not None and plan.get("slots") is not None:
            ids = {p.id for p in self.actives[team_id].batters}
            if any(pid not in ids for pid, _ in plan["slots"]):
                plan["slots"] = None
                plan["notice"] = {"day": self.day + 1, "reason": "roster"}
        return plan

    def review_days(self) -> int:
        return int(self.game_config["lineup"].get("review_days", 7))

    def next_review(self, set_day: int | None) -> int:
        """次に見直す日(この日数の試合をした後、次の日の試合の前。0 から数えた日数)。"""
        n = self.review_days()
        m = max(self.day, (set_day if set_day is not None else -1) + 1, 1)
        return -(-m // n) * n

    def _review_lineups(self) -> None:
        """見直しの日(review_days ごと)に、自分で組んだスタメンを外し、型で AI が組み直す(D-373)。"""
        if self.day == 0 or self.day % self.review_days() != 0:
            return
        for plan in self.lineup_plans.values():
            if plan.get("slots") is not None and (plan.get("set_day") is None or int(plan["set_day"]) < self.day):  # その日に組んだばかりなら次の見直しまで
                plan["slots"] = None
                plan["notice"] = {"day": self.day + 1, "reason": "review"}

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
        self.apply_conditions()
        self._review_lineups()
        self._review_rosters()
        games = [self._play(g) for g in self.schedule.days[self.day]]
        self.played.extend(games)
        if self.day in self.schedule.allstar_days and len(self.allstar) <= self.schedule.allstar_days.index(self.day):
            self.allstar.append(allstar.play(self, self.schedule.allstar_days.index(self.day), self.day))
        advance_day(self.players.values(), self.game_config)  # 二軍の投手も回復する(D-425)
        result = DayResult(self.day, games)
        self.day += 1
        return result

    def is_game_day(self, day: int) -> bool:
        """公式戦のある日か(休みとオールスターの日でないか)。"""
        return bool(self.schedule.days[day])

    def days_to_next_game(self) -> int:
        """次の公式戦の日まで(その日を含めて)何日か。進行の「1日」に使う(休みとオールスターの日を飛ばす。D-435)。"""
        d = self.day
        while d < self.total_days and not self.is_game_day(d):
            d += 1
        return min(d + 1, self.total_days) - self.day

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

    # ---- トレード(D-378) ----

    def _active_snapshot(self, team_id: str, day: int) -> dict:
        a = self.actives[team_id]
        return {"day": int(day), "starters": [p.id for p in a.starters], "relievers": [p.id for p in a.relievers], "batters": [p.id for p in a.batters], "roles": dict(a.relief_roles)}

    def reselect_actives(self, team_ids) -> None:
        """トレードの後、球団の一軍を選び直す(見直しと同じ基準。D-425)。"""
        for tid in team_ids:
            self._reselect(tid)

    # ---- 調子と一軍の入れ替え(D-424、D-425) ----

    def apply_conditions(self) -> None:
        """今日(self.day)の試合の調子を、選手の状態(form)に入れる。"""
        for p in self.players.values():
            p.state.form = self.conditions.form(p.id, self.day)

    def condition_level(self, player_id: str) -> int:
        return self.conditions.level(player_id, self.day)

    def roster_settings(self) -> dict:
        r = self.game_config.get("roster")
        return {"lock_days": 10, "keep_bonus": 4.0, **(r or {})}

    def locked(self) -> set[str]:
        """一軍から外して日が浅く、まだ戻せない選手。"""
        days = int(self.roster_settings()["lock_days"])
        return {pid for pid, d in self.demoted.items() if self.day - d < days}

    def _roster_score(self, team_id: str, keep: bool = True):
        step = float(self.conditions.cfg["step"])
        current = {p.id for p in self.actives[team_id].players} if keep else set()
        bonus = float(self.roster_settings()["keep_bonus"])
        return lambda p: overall(p) + step * self.conditions.level(p.id, self.day) + (bonus if p.id in current else 0.0)

    def set_active(self, team_id: str, active: ActiveRoster) -> None:
        """一軍を変える。外した選手の日を覚え、試合の再現と保存のため顔ぶれを記録する(この日の試合から新しい顔ぶれ)。"""
        before = {p.id for p in self.actives[team_id].players}
        after = {p.id for p in active.players}
        if team_id not in self.active_history:
            self.active_history[team_id] = [self._active_snapshot(team_id, 0)]
        for pid in before - after:
            self.demoted[pid] = self.day
        for pid in after - before:
            self.demoted.pop(pid, None)
        self.actives[team_id] = active
        snap = self._active_snapshot(team_id, self.day)
        hist = self.active_history[team_id]
        if hist[-1]["day"] == self.day:
            hist[-1] = snap
        else:
            hist.append(snap)

    def _reselect(self, team_id: str) -> None:
        new = self.manager.select_active(self.teams[team_id], self._roster_score(team_id), frozenset(self.locked()))
        old = self.actives[team_id]
        if [p.id for p in new.starters] == [p.id for p in old.starters] and [p.id for p in new.relievers] == [p.id for p in old.relievers] and [p.id for p in new.batters] == [p.id for p in old.batters] and new.relief_roles == old.relief_roles:
            return  # 変わらなければ記録しない
        self.set_active(team_id, new)

    def swap(self, team_id: str, out_id: str, in_id: str) -> None:
        """一軍の選手(out)と二軍の選手(in)を入れ替える(自球団の一軍登録。D-425)。投手は投手と、野手は野手と。
        一軍の最低人数(捕手)と外国人の上限を守る。外して日が浅い選手は戻せない。"""
        if self.is_over:
            raise ValueError("シーズンが終わっているので、一軍は変えられません")
        team = self.teams[team_id]
        active = self.actives[team_id]
        ids = {p.id for p in active.players}
        out = next((p for p in active.players if p.id == out_id), None)
        new = next((p for p in team.players if p.id == in_id), None)
        if out is None:
            raise ValueError("外す選手が一軍にいません")
        if new is None or new.id in ids:
            raise ValueError("上げる選手が二軍(支配下の一軍でない選手)にいません")
        if out.role != new.role:
            raise ValueError("投手は投手と、野手は野手と入れ替えてください")
        if new.id in self.locked():
            left = int(self.roster_settings()["lock_days"]) - (self.day - self.demoted[new.id])
            raise ValueError(f"{new.name} は一軍から外して日が浅いので、あと {left} 日は戻せません")
        ar = self.game_config["active_roster"]
        limit = ar.get("foreign_max") if hasattr(ar, "get") else None
        if limit is not None and new.foreign and not out.foreign and sum(1 for p in active.players if p.foreign) >= int(limit):
            raise ValueError(f"一軍に登録できる外国人は {int(limit)} 人までです")
        if out.position == "C" and new.position != "C" and sum(1 for p in active.batters if p.position == "C") <= int(ar["catchers"]):
            raise ValueError(f"一軍の捕手は {int(ar['catchers'])} 人より減らせません")
        rep = lambda xs: [new if p.id == out_id else p for p in xs]
        starters, relievers, batters = rep(active.starters), rep(active.relievers), rep(active.batters)
        roles = self.manager._relief_roles(relievers) if hasattr(self.manager, "_relief_roles") else dict(active.relief_roles)
        self.set_active(team_id, ActiveRoster(starters=starters, relievers=relievers, relief_roles=roles, batters=batters))

    def _review_rosters(self) -> None:
        """見直しの日(review_days ごと。打順の見直しと同じ日)に、AI に任せる球団の一軍を選び直す。"""
        if self.day == 0 or self.day % self.review_days() != 0:
            return
        for t in self.league.teams:
            if self.roster_auto.get(t.id, True):
                self._reselect(t.id)

    def active_on(self, team_id: str, day: int) -> ActiveRoster:
        """その日の試合の一軍(トレードした球団は記録から。D-378)。"""
        hist = self.active_history.get(team_id)
        if not hist:
            return self.actives[team_id]
        snap = [h for h in hist if h["day"] <= day][-1]
        pl = self.players
        return ActiveRoster(starters=[pl[x] for x in snap["starters"]], relievers=[pl[x] for x in snap["relievers"]], relief_roles=dict(snap["roles"]), batters=[pl[x] for x in snap["batters"]])

    # ---- 再現 ----

    def replay_game(self, number: int) -> GameResult:
        """行った試合を、試合前の状態とシーズンのシードから、もう一度計算する(シーズンの状態は変えない)。"""
        played = next(p for p in self.played if p.scheduled.number == number)
        g = played.scheduled
        teams = [self.teams[g.home_id], self.teams[g.away_id]]
        copied_teams, copied_actives = copy.deepcopy((teams, [self.active_on(t.id, g.day) for t in teams]))
        cfg = self.conditions.cfg
        for t, a in zip(copied_teams, copied_actives):
            for p in a.starters + a.relievers:
                p.state.fatigue = played.context.fatigue[p.id]
            for p in a.players:  # その日の調子(D-424)。調子を始める前の試合は、そのときの好調・不調(D-426)
                if g.day < self.conditions.start:
                    p.state.form = self.legacy_form.get(p.id, 0.0)
                else:
                    p.state.form = level_at(self.seed, p.id, g.day, self.conditions.start, cfg) * float(cfg["step"])
        rng = self.game_rng(number)
        setups = [
            self.manager.prepare(t, rng, played.context.rotation[t.id], a, played.context.lineups.get(t.id))[0] for t, a in zip(copied_teams, copied_actives)
        ]
        return simulate_game(setups[0], setups[1], rng, model=self.model, config=self.game_config, manager=self.manager, park=copied_teams[0].park, running=g.day >= self.running_start)

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

    def has_interleague(self) -> bool:
        return any(g.interleague for day in self.schedule.days for g in day)

    def interleague_standings(self) -> list[StandingRow]:
        """交流戦だけの順位表(12 球団。勝率 → 勝利数 → 抽選。D-437)。"""
        league_of = {t.id: t.league_index for t in self.league.teams}
        recs = {}
        for t in self.league.teams:
            w = losses = ties = 0
            for opp, (a, b, c) in self._records[t.id].vs.items():
                if league_of[opp] != t.league_index:
                    w, losses, ties = w + a, losses + b, ties + c
            recs[t.id] = (w, losses, ties)
        ordered = sorted(recs, key=lambda tid: (-_pct(recs[tid][0], recs[tid][1]), -recs[tid][0], derive_seed(self.seed, f"lot:il:{tid}")))
        rows = []
        lead = recs[ordered[0]]
        prev = None
        for i, tid in enumerate(ordered):
            w, losses, ties = recs[tid]
            gb = ((lead[0] - w) + (losses - lead[1])) / 2
            gbp = 0.0 if prev is None else ((prev[0] - w) + (losses - prev[1])) / 2
            rows.append(StandingRow(tid, self.teams[tid].name, w, losses, ties, float(_pct(w, losses)), gb, gbp, i + 1))
            prev = recs[tid]
        return rows

    def league_indexes(self) -> list[int]:
        return sorted({t.league_index for t in self.league.teams})

    # ---- 結果 ----

    def result(self) -> SeasonResult:
        standings = {i: self.standings(i) for i in self.league_indexes()}
        champions = {i: [row.team_id for row in rows if row.rank == 1] for i, rows in standings.items()}
        return SeasonResult(standings, champions, list(self.played), self.schedule)
