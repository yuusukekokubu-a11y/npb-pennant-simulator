"""1試合の進行(段階1・第1弾の実装 ③。D-059〜D-062)。

1つのチームどうしの試合を、最初から最後まで進める。
  - 9回まで。9回裏はホームがリードしていれば行わない。サヨナラはその場で終了。
  - 延長は上限まで。同点なら引き分け(設定で変更可)。
  - 走者の進塁は baserunning.py、打席の結果は plate_appearance.py、采配は manager.py。
  - 投手は打席に立たない(指名打者制。D-046)。
  - 打席の前に、暴投・捕逸・盗塁が起きることがある(D-427)。打席の後の出来事として、その前の打席の記録(events)に残す。
  - 無死か一死で走者が一塁・二塁にいれば、監督の判断で送りバントをすることがある(D-458)。打席の記録に bunt・sac_bunt の印を付ける。

試合は入力の選手を書き換えない(疲労の加算は、試合後に fatigue.apply_game_fatigue で行う)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from . import skills as skill_mod
from .abilities import PITCHER_ITEMS
from .baserunning import HOME, Baserunning, Runner, RunnerMove, adjust_probability
from .game_config import GameConfig, load_game_config
from .manager import BuntSituation, Manager, PitchingSituation, SimpleManager, TeamSetup
from .models import NEUTRAL_PARK, ParkFactors, Player
from .plate_appearance import BaseOutState, PlateAppearance, PlateAppearanceModel, default_model, ratio_multiplier

TOP, BOTTOM = "top", "bottom"
STEAL, CAUGHT, WILD_PITCH, PASSED_BALL = "steal", "caught_stealing", "wild_pitch", "passed_ball"
EVENT_KINDS = (STEAL, CAUGHT, WILD_PITCH, PASSED_BALL)
LEAD_ITEMS = ("strikeout", "control", "stuff", "contact_suppression")  # 捕手のリードが効く投手の能力(D-428)


@dataclass
class RunEvent:
    """打席と打席の間の出来事(盗塁・盗塁死・暴投・捕逸。D-427)。次の打者の打席の前に起きる。"""

    kind: str
    inning: int
    half: str
    batting_team_id: str
    fielding_team_id: str
    pitcher_id: str
    catcher_id: str
    score_diff: int  # 出来事の前の点差(攻撃側 − 守備側)
    base_out: BaseOutState  # 出来事の前の走者・アウト状況
    moves: list[RunnerMove]  # 走者の動き(盗塁は 1 人、暴投・捕逸は全員)
    runs: int = 0
    outs_made: int = 0
    walkoff: bool = False


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
    walkoff_truncated: bool = False
    fielder_id: str | None = None  # 打球を担当した野手の選手 ID(投手が担当なら投手。第3弾の守備の計算用)  # サヨナラで、勝ち越しに必要な分を超えた得点を打ち切った打席(D-087。公式記録の数え直しは実装⑤)
    events: list[RunEvent] = field(default_factory=list)  # この打席の後、次の打席の前に起きた盗塁・暴投・捕逸(D-427)
    bunt: bool = False  # 送りバントの打席(D-458)
    sac_bunt: bool = False  # 犠打(成功した送りバント。D-459)

    @property
    def all_runs(self) -> int:
        """この打席と、その後の盗塁・暴投・捕逸の得点の合計。"""
        return self.runs + sum(e.runs for e in self.events)

    @property
    def all_outs(self) -> int:
        return self.outs_made + sum(e.outs_made for e in self.events)

    @property
    def ends_walkoff(self) -> bool:
        return self.walkoff or any(e.walkoff for e in self.events)


def steps(result: "GameResult"):
    """打席と、その後の盗塁・暴投・捕逸を、起きた順に並べる(どちらも pitcher_id・batting_team_id・runs・moves・base_out を持つ)。"""
    for x in result.log:
        yield x
        yield from x.events


def state_after(pas: list, k: int):
    """半イニングの打席の並び pas で、打席 k の直後の状況(盗塁などの前)。イニングの終わりなら None。"""
    x = pas[k]
    if x.events:
        return x.events[0].base_out
    return pas[k + 1].base_out if k + 1 < len(pas) else None


def event_after(pas: list, k: int, j: int):
    """打席 k の後の j 番目の出来事の直後の状況。イニングの終わりなら None。"""
    events = pas[k].events
    if j + 1 < len(events):
        return events[j + 1].base_out
    return pas[k + 1].base_out if k + 1 < len(pas) else None


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
    pull: dict | None = None  # 監督の采配の腕による先発の代え方(staff.pull_rules。D-470)。None は標準


class _Game:
    def __init__(self, home, away, rng, model, config, manager, baserunning, park=None, running=True, bunting=True):
        self.park = park or NEUTRAL_PARK  # その試合の球場(ホームチームの本拠地)の倍率(D-136)
        self.bunt = config.get("bunt") if bunting and hasattr(model, "resolve_bunt") and hasattr(manager, "bunt_probability") else None  # 送りバント(D-458)
        self.steal = config.get("steal") if running else None  # 盗塁(D-427)。設定がなければ起きない
        self.battery = config.get("battery") if running else None  # 暴投・捕逸
        self._lead_cache: dict[tuple[str, str], Player] = {}
        self.home = _Side(home)
        self.away = _Side(away)
        for side in (self.home, self.away):  # 監督の采配の腕(D-470)。0 なら今までと同じ
            level = int(getattr(side.setup, "manager_level", 0) or 0)
            if level:
                from .staff import pull_rules

                side.pull = pull_rules(level)
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

    def _situation(self, fielding: _Side, batting: _Side, inning: int) -> PitchingSituation:
        prefer = bool(fielding.pull and fielding.pull["prefer_overall"])  # 腕のいい監督は救援を総合で選ぶ(D-470)
        return PitchingSituation(inning=inning, lead=fielding.runs - batting.runs, used_ids=set(fielding.used_ids), prefer_overall=prefer)

    def _bring_reliever(self, fielding: _Side, batting: _Side, inning: int) -> bool:
        situation = self._situation(fielding, batting, inning)
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
            if any(b is not None for b in bases) and (self.steal or self.battery):  # 打席の前の盗塁・暴投・捕逸(D-427)
                outs, bases, extra = self._between(inning, half, batting, fielding, outs, bases, defense)
                runs_this_inning += extra
                if self.walkoff or outs >= 3:
                    break  # 盗塁死で 3 アウトなら、この打者は次の回の先頭
            slot = slots[batting.order_index]
            batting.order_index = (batting.order_index + 1) % len(slots)
            batter, pitcher = slot.player, fielding.pitcher
            base_out = BaseOutState(outs, bases[0] is not None, bases[1] is not None, bases[2] is not None)
            diff_before = batting.runs - fielding.runs
            bunt = self._wants_bunt(inning, batting, base_out, diff_before, slot.order, batter)
            if bunt:
                bp = self.model.resolve_bunt(batter, pitcher, base_out, self.rng, self.bunt)
                pa = bp.pa
                play = self.baserunning.bunt(bp.kind, batter, pitcher.id, bases)
            else:
                pa = self.model.resolve(batter, self._with_lead(pitcher, defense), defense, base_out, self.rng, home=half == BOTTOM, park=self.park)
                fielding_ratings = self.model.fielding(pa.fielder, pitcher, defense) if pa.fielder else None
                play = self.baserunning.advance(pa, batter, pitcher.id, bases, outs, fielding_ratings, self.rng)

            # サヨナラ:勝ち越した時点で試合終了。本塁打以外は、勝ち越しに必要な分だけ得点を数える
            walkoff = half == BOTTOM and inning >= rules["innings"] and diff_before + play.runs > 0
            moves = play.moves
            if walkoff and pa.result != "home_run":
                moves = _truncate_walkoff(moves, needed=-diff_before + 1)
            runs = sum(1 for m in moves if m.scored)
            truncated = runs < play.runs

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
                    walkoff_truncated=truncated,
                    fielder_id=None if not pa.fielder else pitcher.id if pa.fielder == "P" else defense[pa.fielder].id,
                    bunt=bunt,
                    sac_bunt=bunt and bp.kind == "sacrifice",
                )
            )
            if walkoff:
                self.walkoff = True
                break
            # 失点の上限に達した先発は、イニングの途中でも降板する(引き継いだ走者の責任は先発に残る)
            starter_line = fielding.pitcher_line
            if outs < 3 and starter_line.role == "starter" and starter_line.runs >= self.manager.run_limit() + (fielding.pull["run_shift"] if fielding.pull else 0):
                starter_line.exit_reason = "run_limit"
                self._bring_reliever(fielding, batting, inning)
        batting.line.append(runs_this_inning)

        # イニングの終わり:先発は打者数の上限で降板、救援は1イニングで交代
        line = fielding.pitcher_line
        if line.role == "starter":
            limit = self.manager.starter_limit(fielding.pitcher)
            pull = fielding.pull
            if pull and not pull["early"]:
                limit *= pull["share"]  # 腕の低い監督は先発を引っ張る(D-470)
            if line.batters_faced >= limit:
                line.exit_reason = "batters_limit"
                fielding.needs_new_pitcher = True
            elif pull and pull["early"] and self._pull_early(fielding, batting, inning, limit * pull["share"]):
                line.exit_reason = "batters_limit"  # 腕のいい監督は、終盤の接戦で疲れた先発を引っ張らない(D-470)
                fielding.needs_new_pitcher = True
        else:
            fielding.needs_new_pitcher = True

    def _pull_early(self, fielding: _Side, batting: _Side, inning: int, share_limit: float) -> bool:
        """終盤(6 回から)の接戦(2 点差以内)で、先発の打者数が上限の決まった割合を超え、次に出せる救援の総合が先発以上なら代える(D-470)。"""
        pull = fielding.pull
        line = fielding.pitcher_line
        if inning < pull["from_inning"] or abs(fielding.runs - batting.runs) > pull["margin"] or line.batters_faced < share_limit:
            return False
        nxt = self.manager.choose_reliever(fielding.setup.active, self._situation(fielding, batting, inning + 1))
        if nxt is None:
            return False
        from .stats import overall

        return overall(nxt) >= overall(fielding.pitcher)


    def _wants_bunt(self, inning: int, batting: _Side, base_out: BaseOutState, lead: int, order: int, batter: Player) -> bool:
        """送りバントをするか(D-458)。三塁に走者がいない・無死か一死・一塁か二塁に走者がいるときだけ、監督の確率で決める(乱数はそのときだけ使う)。"""
        if self.bunt is None or base_out.outs > 1 or base_out.third or not (base_out.first or base_out.second):
            return False
        situation = BuntSituation(inning, base_out.outs, base_out.first, base_out.second, lead, order)
        p = self.manager.bunt_probability(situation, batter, getattr(batting.setup, "bunt", "normal"))
        return p > 0 and self.rng.random() < p

    # ---- 盗塁・暴投・捕逸(D-427、D-428) ----

    def _with_lead(self, pitcher: Player, defense) -> Player:
        """捕手のリード(キャッチャー◯/×)を足した投手の写し。効きがなければそのまま。"""
        catcher = defense.get("C") if hasattr(defense, "get") else None
        if catcher is None or not catcher.skills:
            return pitcher
        key = (pitcher.id, catcher.id)
        if key not in self._lead_cache:
            lead = skill_mod.lead_bonus(catcher)
            self._lead_cache[key] = skill_mod.shifted(pitcher, {i: lead for i in LEAD_ITEMS}) if lead else pitcher
        return self._lead_cache[key]

    def _prob(self, entry: dict, runner: Player | None = None, catcher: Player | None = None, pitcher: Player | None = None) -> float:
        m = 1.0
        if runner is not None:
            extra = skill_mod.steal_effects(runner)
            for item, ratio in entry.get("runner", {}).items():
                m *= ratio_multiplier(self.model.rating(runner, item) + extra.get(item, 0.0), ratio)
        if catcher is not None:
            extra = skill_mod.catching_effects(catcher)
            for item, ratio in entry.get("catcher", {}).items():
                m *= ratio_multiplier(self.model.rating(catcher, item) + extra.get(item, 0.0), ratio)
        if pitcher is not None:
            for item, ratio in entry.get("pitcher", {}).items():
                m *= ratio_multiplier(self.model.rating(pitcher, item), ratio)
        return adjust_probability(float(entry["base"]), m)

    def _between(self, inning: int, half: str, batting: _Side, fielding: _Side, outs: int, bases: tuple, defense):
        """次の打者の打席の前:暴投・捕逸、次に盗塁。出来事は直前の打席の記録に足す。戻り値は(アウト数, 走者, 得点)。"""
        rules = self.config["rules"]
        catcher = defense["C"]
        pitcher = fielding.pitcher
        prev = self.log[-1]
        total = 0

        def record(kind: str, moves: list[RunnerMove], new_bases: tuple, outs_made: int) -> None:
            nonlocal outs, bases, total
            diff = batting.runs - fielding.runs
            runs = sum(1 for m in moves if m.scored)
            walkoff = half == BOTTOM and inning >= rules["innings"] and diff + runs > 0
            before = BaseOutState(outs, bases[0] is not None, bases[1] is not None, bases[2] is not None)
            prev.events.append(RunEvent(kind, inning, half, batting.setup.team_id, fielding.setup.team_id, pitcher.id, catcher.id, diff, before, moves, runs, outs_made, walkoff))
            fielding.pitcher_line.outs += outs_made
            for m in moves:
                if m.scored:
                    self.lines_by_pitcher[m.responsible_pitcher_id].runs += 1
            batting.runs += runs
            total += runs
            outs += outs_made
            bases = new_bases
            if walkoff:
                self.walkoff = True

        def move(runner: Runner, start: int, end: int | None, on_error: bool = False) -> RunnerMove:
            return RunnerMove(runner.player.id, start, end, runner.responsible_pitcher_id, runner.reached_on_error, on_error)

        if self.battery:
            p_wp = self._prob(self.battery["wild_pitch"], pitcher=pitcher)
            p_pb = self._prob(self.battery["passed_ball"], catcher=catcher)
            r = self.rng.random()
            kind = WILD_PITCH if r < p_wp else PASSED_BALL if r < p_wp + p_pb else None
            if kind is not None:
                moves, after = [], [None, None, None]
                for start in (3, 2, 1):
                    runner = bases[start - 1]
                    if runner is None:
                        continue
                    end = start + 1 if start < 3 else HOME
                    moves.append(move(runner, start, end, on_error=kind == PASSED_BALL))
                    if end != HOME:
                        after[end - 1] = runner
                record(kind, moves, tuple(after), 0)
                if self.walkoff:
                    return outs, bases, total

        st = self.steal
        if st and outs < 3:
            start = 1 if bases[0] is not None and bases[1] is None else 2 if bases[1] is not None and bases[0] is None and bases[2] is None else 0
            diff = batting.runs - fielding.runs
            if start and -diff < int(st["behind_limit"]) and diff < int(st["ahead_limit"]):
                runner = bases[start - 1]
                strategy = st["strategies"].get(getattr(batting.setup, "steal", "normal"), st["strategies"]["normal"])
                key = "2b" if start == 1 else "3b"
                p_success = self._prob(st[f"success_{key}"], runner.player, catcher, pitcher)
                if p_success >= float(strategy["min_success"]):
                    p_attempt = min(0.95, self._prob(st[f"attempt_{key}"], runner.player, catcher, pitcher) * float(strategy["attempt"]))
                    if self.rng.random() < p_attempt:
                        after = list(bases)
                        after[start - 1] = None
                        if self.rng.random() < p_success:
                            after[start] = runner
                            record(STEAL, [move(runner, start, start + 1)], tuple(after), 0)
                        else:
                            record(CAUGHT, [move(runner, start, None)], tuple(after), 1)
        return outs, bases, total


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
    park: ParkFactors | None = None,
    running: bool = True,
    bunting: bool = True,
) -> GameResult:
    """1試合を進める。rng は random.Random(シード)。同じシード・同じ入力なら同じ結果になる。
    park はその試合の球場(ホームチームの本拠地)の倍率(省略時は 1.0。D-136)。
    running が False なら、盗塁・暴投・捕逸を起こさない(それらを入れる前の試合の再現。D-433)。
    bunting が False なら、送りバントをしない(入れる前の試合の再現。D-461)。"""
    config = config or load_game_config()
    model = model or default_model()
    manager = manager or SimpleManager(config)
    baserunning = Baserunning(config, model.rating)
    return _Game(home, away, rng, model, config, manager, baserunning, park, running, bunting).play()
