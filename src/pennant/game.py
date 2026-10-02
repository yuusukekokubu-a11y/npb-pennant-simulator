"""1試合の進行(段階1・第1弾の実装 ③。D-059〜D-062)。

1つのチームどうしの試合を、最初から最後まで進める。
  - 9回まで。9回裏はホームがリードしていれば行わない。サヨナラはその場で終了。
  - 延長は上限まで。同点なら引き分け(設定で変更可)。
  - 走者の進塁は baserunning.py、打席の結果は plate_appearance.py、采配は manager.py。
  - 投手は打席に立たない(指名打者制。D-046)。

試合は入力の選手を書き換えない(疲労の加算は、試合後に fatigue.apply_game_fatigue で行う)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from .baserunning import HOME, Baserunning, RunnerMove
from .game_config import GameConfig, load_game_config
from .manager import Manager, PitchingSituation, SimpleManager, TeamSetup
from .models import Player
from .plate_appearance import BaseOutState, PlateAppearance, PlateAppearanceModel, default_model

TOP, BOTTOM = "top", "bottom"


@dataclass
class GamePlateAppearance:
    """試合の中の1打席の記録(打席ログの1行)。"""

    inning: int
    half: str  # top(表)/ bottom(裏)
    batting_team_id: str
    fielding_team_id: str
    lineup_slot: int  # 打順(1〜9)
    batter_id: str
    pitcher_id: str
    score_diff: int  # 打席の前の点差(攻撃側 − 守備側)
    base_out: BaseOutState  # 打席の前の走者・アウト状況
    pa: PlateAppearance
    moves: list[RunnerMove]  # 各走者(打者を含む)の動き。出塁させた投手・失策の有無を含む
    runs: int
    outs_made: int
    double_play: bool = False
    sac_fly: bool = False
    walkoff: bool = False


@dataclass
class PitcherLine:
    """投手1人の登板の記録。"""

    pitcher_id: str
    team_id: str
    role: str  # starter(先発)/ reliever(救援)
    entered_inning: int
    batters_faced: int = 0
    outs: int = 0
    runs: int = 0  # この投手が出塁させた走者が生還した数(自責点の判定は後で行う)
    exit_reason: str | None = None  # batters_limit / run_limit / inning_end / game_end
    forced_extra_innings: int = 0  # 交代すべきだったが、登板できる投手がいなかったため続投したイニング数

    @property
    def innings_pitched(self) -> float:
        return self.outs / 3


@dataclass
class GameResult:
    home_team_id: str
    away_team_id: str
    home_runs: int
    away_runs: int
    line: dict[str, list[int | None]]  # イニングごとの得点。行わなかった裏は None
    innings: int  # 行ったイニング数
    extra_innings: bool
    tie: bool
    walkoff: bool
    log: list[GamePlateAppearance]
    pitchers: list[PitcherLine]
    lineups: dict[str, list[tuple[int, str, str, str | None]]]  # 打順・選手ID・守備位置・休んだ主力のID

    @property
    def winner(self) -> str | None:
        if self.home_runs == self.away_runs:
            return None
        return self.home_team_id if self.home_runs > self.away_runs else self.away_team_id

    def batters_faced(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for line in self.pitchers:
            out[line.pitcher_id] = out.get(line.pitcher_id, 0) + line.batters_faced
        return out


@dataclass
class _Side:
    setup: TeamSetup
    order_index: int = 0
    runs: int = 0
    line: list[int | None] = field(default_factory=list)
    pitcher: Player | None = None
    pitcher_line: PitcherLine | None = None
    used_ids: set[str] = field(default_factory=set)
    needs_new_pitcher: bool = False


class _Game:
    def __init__(self, home, away, rng, model, config, manager, baserunning):
        self.home = _Side(home)
        self.away = _Side(away)
        self.rng = rng
        self.model = model
        self.config = config
        self.manager = manager
        self.baserunning = baserunning
        self.log: list[GamePlateAppearance] = []
        self.pitchers: list[PitcherLine] = []
        self.walkoff = False
        self.lines_by_pitcher: dict[str, PitcherLine] = {}
        for side in (self.home, self.away):
            self._put_in(side, side.setup.starter, "starter", 1)

    # ---- 投手 ----

    def _put_in(self, side: _Side, pitcher: Player, role: str, inning: int) -> None:
        if side.pitcher_line is not None and side.pitcher_line.exit_reason is None:
            side.pitcher_line.exit_reason = "inning_end"
        line = PitcherLine(pitcher.id, side.setup.team_id, role, inning)
        side.pitcher, side.pitcher_line = pitcher, line
        side.used_ids.add(pitcher.id)
        self.pitchers.append(line)
        self.lines_by_pitcher[pitcher.id] = line
        side.needs_new_pitcher = False

    def _bring_reliever(self, fielding: _Side, batting: _Side, inning: int) -> bool:
        situation = PitchingSituation(inning=inning, lead=fielding.runs - batting.runs, used_ids=set(fielding.used_ids))
        reliever = self.manager.choose_reliever(fielding.setup.active, situation)
        if reliever is None:
            return False  # 登板できる救援がいなければ、今の投手が続投する
        self._put_in(fielding, reliever, "reliever", inning)
        return True

    # ---- 試合 ----

    def play(self) -> GameResult:
        rules = self.config["rules"]
        last_regular = rules["innings"]
        limit = rules["max_innings"] if rules["allow_tie"] else rules["max_innings_without_tie"]
        inning = 0
        while True:
            inning += 1
            self._half_inning(inning, self.away, self.home, TOP)
            if inning >= last_regular and self.home.runs > self.away.runs:
                self.home.line.append(None)  # 9回裏(以降)は行わない
                break
            self._half_inning(inning, self.home, self.away, BOTTOM)
            if self.walkoff:
                break
            if inning >= last_regular and self.home.runs != self.away.runs:
                break
            if inning >= limit:
                break
        for side in (self.home, self.away):
            if side.pitcher_line.exit_reason is None:
                side.pitcher_line.exit_reason = "game_end"
        return GameResult(
            home_team_id=self.home.setup.team_id,
            away_team_id=self.away.setup.team_id,
            home_runs=self.home.runs,
            away_runs=self.away.runs,
            line={self.home.setup.team_id: self.home.line, self.away.setup.team_id: self.away.line},
            innings=inning,
            extra_innings=inning > last_regular,
            tie=self.home.runs == self.away.runs,
            walkoff=self.walkoff,
            log=self.log,
            pitchers=self.pitchers,
            lineups={
                s.setup.team_id: [(x.order, x.player.id, x.position, x.replaced_id) for x in s.setup.lineup.slots]
                for s in (self.home, self.away)
            },
        )

    def _half_inning(self, inning: int, batting: _Side, fielding: _Side, half: str) -> None:
        rules = self.config["rules"]
        if fielding.needs_new_pitcher:
            if not self._bring_reliever(fielding, batting, inning):
                fielding.needs_new_pitcher = False
                fielding.pitcher_line.forced_extra_innings += 1  # 代わりがいないので続投(例外)
        outs = 0
        bases: tuple = (None, None, None)
        runs_this_inning = 0
        defense = fielding.setup.lineup.defense()
        slots = batting.setup.lineup.slots
        while outs < 3:
            slot = slots[batting.order_index]
            batting.order_index = (batting.order_index + 1) % len(slots)
            batter, pitcher = slot.player, fielding.pitcher
            base_out = BaseOutState(outs, bases[0] is not None, bases[1] is not None, bases[2] is not None)
            diff_before = batting.runs - fielding.runs
            pa = self.model.resolve(batter, pitcher, defense, base_out, self.rng)
            fielding_ratings = self.model.fielding(pa.fielder, pitcher, defense) if pa.fielder else None
            play = self.baserunning.advance(pa, batter, pitcher.id, bases, outs, fielding_ratings, self.rng)

            # サヨナラ:勝ち越した時点で試合終了。本塁打以外は、勝ち越しに必要な分だけ得点を数える
            walkoff = half == BOTTOM and inning >= rules["innings"] and diff_before + play.runs > 0
            moves = play.moves
            if walkoff and pa.result != "home_run":
                moves = _truncate_walkoff(moves, needed=-diff_before + 1)
            runs = sum(1 for m in moves if m.scored)

            line = fielding.pitcher_line
            line.batters_faced += 1
            line.outs += play.outs
            for m in moves:
                if m.scored:
                    self.lines_by_pitcher[m.responsible_pitcher_id].runs += 1
            outs += play.outs
            bases = play.bases
            batting.runs += runs
            runs_this_inning += runs
            self.log.append(
                GamePlateAppearance(
                    inning=inning,
                    half=half,
                    batting_team_id=batting.setup.team_id,
                    fielding_team_id=fielding.setup.team_id,
                    lineup_slot=slot.order,
                    batter_id=batter.id,
                    pitcher_id=pitcher.id,
                    score_diff=diff_before,
                    base_out=base_out,
                    pa=pa,
                    moves=moves,
                    runs=runs,
                    outs_made=play.outs,
                    double_play=play.double_play,
                    sac_fly=play.sac_fly,
                    walkoff=walkoff,
                )
            )
            if walkoff:
                self.walkoff = True
                break
            # 失点の上限に達した先発は、イニングの途中でも降板する(引き継いだ走者の責任は先発に残る)
            starter_line = fielding.pitcher_line
            if outs < 3 and starter_line.role == "starter" and starter_line.runs >= self.manager.run_limit():
                starter_line.exit_reason = "run_limit"
                self._bring_reliever(fielding, batting, inning)
        batting.line.append(runs_this_inning)

        # イニングの終わり:先発は打者数の上限で降板、救援は1イニングで交代
        line = fielding.pitcher_line
        if line.role == "starter":
            if line.batters_faced >= self.manager.starter_limit(fielding.pitcher):
                line.exit_reason = "batters_limit"
                fielding.needs_new_pitcher = True
        else:
            fielding.needs_new_pitcher = True


def _truncate_walkoff(moves: list[RunnerMove], needed: int) -> list[RunnerMove]:
    """サヨナラで、勝ち越しに必要な分を超えた生還を取り消す(本塁打以外)。

    取り消した走者は3塁で止まったものとし、後ろの走者は前の走者を追い越さない位置で止める
    (同じ塁に2人いないように)。試合はこの打席で終わるので、止まった位置は記録のためだけに使う。
    """
    kept = 0
    wanted: dict[int, int] = {}  # 動きの位置 → 止まる塁(取り消しを反映した希望の塁)
    for i, m in enumerate(moves):
        if m.scored:
            if kept < needed:
                kept += 1
                continue
            wanted[i] = 3
        elif not m.out:
            wanted[i] = m.end
    adjusted = list(moves)
    limit = HOME  # 前の走者がいる塁(後ろの走者はここより手前で止まる)
    for i in sorted(wanted, key=lambda i: -moves[i].start):  # 前の走者から順に
        end = min(wanted[i], limit - 1)
        if end != moves[i].end:
            adjusted[i] = replace(moves[i], end=end)
        limit = end
    return adjusted


def simulate_game(
    home: TeamSetup,
    away: TeamSetup,
    rng: random.Random,
    model: PlateAppearanceModel | None = None,
    config: GameConfig | None = None,
    manager: Manager | None = None,
) -> GameResult:
    """1試合を進める。rng は random.Random(シード)。同じシード・同じ入力なら同じ結果になる。"""
    config = config or load_game_config()
    model = model or default_model()
    manager = manager or SimpleManager(config)
    baserunning = Baserunning(config, model.rating)
    return _Game(home, away, rng, model, config, manager, baserunning).play()
