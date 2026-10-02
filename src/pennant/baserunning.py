"""走者の進塁・併殺・犠牲フライ(D-045、D-059)。

固定ルール:四球・死球は押し出し、本塁打・三塁打は全員生還、三振・ライナーアウトは進塁なし。
確率で決めるもの:単打・二塁打での余分な進塁、ゴロでの併殺と進塁、フライでの犠牲フライ、失策での進塁。
確率は「確率表の値」に、能力の倍率をオッズ比法で掛けて決める(打席の計算と同じ方式)。
走塁中のアウト(走塁死)は第1弾では入れない。

各走者の動きを RunnerMove として記録する(どの塁からどの塁へ、アウトか、出塁させた投手、失策が関係したか)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Mapping

from .game_config import GameConfig
from .models import Player
from .plate_appearance import PlateAppearance, ratio_multiplier

HOME = 4
OUTFIELD = ("LF", "CF", "RF")


@dataclass(frozen=True)
class Runner:
    """塁上の走者。"""

    player: Player
    responsible_pitcher_id: str  # この走者を出塁させた投手(自責点・防御率の計算用)
    reached_on_error: bool = False  # 失策で出塁したか


@dataclass(frozen=True)
class RunnerMove:
    """1人の走者(打者を含む)の動き。start=0 は打者。end は 1〜3(塁)、4(生還)、None(アウト)。"""

    player_id: str
    start: int
    end: int | None
    responsible_pitcher_id: str
    reached_on_error: bool = False  # この走者が失策で出塁していたか(打者が失策で出塁した場合も True)
    advanced_on_error: bool = False  # この動きに失策が関係したか

    @property
    def out(self) -> bool:
        return self.end is None

    @property
    def scored(self) -> bool:
        return self.end == HOME


@dataclass
class PlayOutcome:
    bases: tuple  # 打席後の走者 (1塁, 2塁, 3塁)。いなければ None
    outs: int  # この打席で増えたアウト数
    moves: list[RunnerMove] = field(default_factory=list)
    double_play: bool = False
    sac_fly: bool = False

    @property
    def runs(self) -> int:
        return sum(1 for m in self.moves if m.scored)


def adjust_probability(p: float, multiplier: float) -> float:
    """確率に倍率をオッズで掛ける(オッズ比法。D-036)。"""
    odds = p / (1 - p) * multiplier
    return odds / (1 + odds)


class _Play:
    """1打席分の進塁を組み立てる作業用の入れ物。"""

    def __init__(self, bases, batter: Player, pitcher_id: str):
        self.before = list(bases)
        self.after: list[Runner | None] = [None, None, None]
        self.moves: list[RunnerMove] = []
        self.batter = batter
        self.pitcher_id = pitcher_id

    def move(self, start: int, end: int | None, *, on_error: bool = False, batter_on_error: bool = False) -> None:
        if start == 0:
            runner = Runner(self.batter, self.pitcher_id, reached_on_error=batter_on_error)
        else:
            runner = self.before[start - 1]
        self.moves.append(
            RunnerMove(
                player_id=runner.player.id,
                start=start,
                end=end,
                responsible_pitcher_id=runner.responsible_pitcher_id,
                reached_on_error=runner.reached_on_error,
                advanced_on_error=on_error,
            )
        )
        if end is not None and end != HOME:
            if self.after[end - 1] is not None:
                raise AssertionError("同じ塁に走者が2人になりました")
            self.after[end - 1] = runner

    def free(self, base: int) -> bool:
        return self.after[base - 1] is None


class Baserunning:
    """走者の進塁を決める。確率表と能力の効きは設定ファイル(game.json の advancement)から読む。"""

    def __init__(self, config: GameConfig, rating):
        self.table = config["advancement"]
        self.rating = rating  # (選手, 能力名) → 能力値(好調・不調を含む)

    def probability(
        self,
        key: str,
        runner: Player | None = None,
        batter: Player | None = None,
        fielding: Mapping[str, float] | None = None,
    ) -> float:
        entry = self.table[key]
        m = 1.0
        for item, ratio in entry.get("runner", {}).items():
            if runner is not None:
                m *= ratio_multiplier(self.rating(runner, item), ratio)
        for item, ratio in entry.get("batter", {}).items():
            if batter is not None:
                m *= ratio_multiplier(self.rating(batter, item), ratio)
        for item, ratio in entry.get("fielder", {}).items():
            if fielding is not None:
                m *= ratio_multiplier(fielding[item], ratio)
        return adjust_probability(entry["base"], m)

    def _chance(self, rng: random.Random, key: str, **kwargs) -> bool:
        return rng.random() < self.probability(key, **kwargs)

    def advance(
        self,
        pa: PlateAppearance,
        batter: Player,
        pitcher_id: str,
        bases,
        outs: int,
        fielding: Mapping[str, float] | None,
        rng: random.Random,
    ) -> PlayOutcome:
        """打席の結果から、走者・打者の動きを決める。bases は (1塁, 2塁, 3塁) の Runner または None。"""
        play = _Play(bases, batter, pitcher_id)
        r1, r2, r3 = play.before
        result = pa.result
        double_play = sac_fly = False
        outs_added = 0

        def stay(*starts: int) -> None:
            for s in starts:
                if play.before[s - 1] is not None:
                    play.move(s, s)

        if result in ("strikeout", "line_out"):
            play.move(0, None)
            outs_added = 1
            stay(3, 2, 1)

        elif result in ("walk", "hit_by_pitch"):
            # 押し出し:後ろから詰まっている走者だけが進む
            forced1 = r1 is not None
            forced2 = forced1 and r2 is not None
            forced3 = forced2 and r3 is not None
            if r3 is not None:
                play.move(3, HOME if forced3 else 3)
            if r2 is not None:
                play.move(2, 3 if forced2 else 2)
            if r1 is not None:
                play.move(1, 2)
            play.move(0, 1)

        elif result == "home_run":
            for s in (3, 2, 1):
                if play.before[s - 1] is not None:
                    play.move(s, HOME)
            play.move(0, HOME)

        elif result == "triple":
            for s in (3, 2, 1):
                if play.before[s - 1] is not None:
                    play.move(s, HOME)
            play.move(0, 3)

        elif result == "double":
            if r3 is not None:
                play.move(3, HOME)
            if r2 is not None:
                play.move(2, HOME)
            if r1 is not None:
                scores = self._chance(rng, "double_runner_from_1b_scores", runner=r1.player, fielding=fielding)
                play.move(1, HOME if scores else 3)
            play.move(0, 2)

        elif result == "single":
            outfield = pa.fielder in OUTFIELD
            if r3 is not None:
                play.move(3, HOME)
            if r2 is not None:
                scores = outfield and self._chance(rng, "single_runner_from_2b_scores", runner=r2.player, fielding=fielding)
                play.move(2, HOME if scores else 3)
            if r1 is not None:
                to_third = (
                    outfield
                    and play.free(3)
                    and self._chance(rng, "single_runner_from_1b_to_3b", runner=r1.player, fielding=fielding)
                )
                play.move(1, 3 if to_third else 2)
            play.move(0, 1)

        elif result == "error":
            # 打者は失策で出塁。走者は1つずつ進み、さらに余分に進むこともある
            for s in (3, 2, 1):
                runner = play.before[s - 1]
                if runner is None:
                    continue
                end = s + 1
                if end < HOME:
                    nxt = end + 1
                    if (nxt == HOME or play.free(nxt)) and self._chance(
                        rng, "error_extra_base", runner=runner.player, fielding=fielding
                    ):
                        end = nxt
                play.move(s, end, on_error=True)
            play.move(0, 1, on_error=True, batter_on_error=True)

        elif result == "ground_out":
            if outs >= 2:
                play.move(0, None)
                outs_added = 1
                stay(3, 2, 1)
            elif r1 is not None and self._chance(
                rng, "double_play", runner=r1.player, batter=batter, fielding=fielding
            ):
                double_play = True
                outs_added = 2
                if outs == 0:
                    # 併殺の間に、ほかの走者は1つ進む
                    if r3 is not None:
                        play.move(3, HOME)
                    if r2 is not None:
                        play.move(2, 3)
                else:
                    stay(3, 2)  # 3アウトでイニング終了。得点は入らない
                play.move(1, None)
                play.move(0, None)
            else:
                outs_added = 1
                forced2 = r1 is not None and r2 is not None
                forced3 = forced2 and r3 is not None
                if r3 is not None:
                    scores = forced3 or self._chance(
                        rng, "ground_out_runner_from_3b_scores", runner=r3.player, fielding=fielding
                    )
                    play.move(3, HOME if scores else 3)
                if r2 is not None:
                    to_third = forced2 or (
                        play.free(3)
                        and self._chance(rng, "ground_out_runner_from_2b_to_3b", runner=r2.player, fielding=fielding)
                    )
                    play.move(2, 3 if to_third else 2)
                if r1 is not None:
                    play.move(1, 2)
                play.move(0, None)

        elif result == "fly_out":
            outs_added = 1
            tag = outs < 2 and pa.fielder in OUTFIELD
            if r3 is not None:
                scores = tag and self._chance(rng, "fly_out_runner_from_3b_scores", runner=r3.player, fielding=fielding)
                sac_fly = scores
                play.move(3, HOME if scores else 3)
            if r2 is not None:
                to_third = (
                    tag
                    and play.free(3)
                    and self._chance(rng, "fly_out_runner_from_2b_to_3b", runner=r2.player, fielding=fielding)
                )
                play.move(2, 3 if to_third else 2)
            stay(1)
            play.move(0, None)

        else:
            raise ValueError(f"知らない打席の結果です: {result}")

        return PlayOutcome(
            bases=tuple(play.after), outs=outs_added, moves=play.moves, double_play=double_play, sac_fly=sac_fly
        )
