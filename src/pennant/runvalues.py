"""打撃・走塁・守備の得点(第3弾②。D-160〜D-166)。

試合の結果(打席ログ)と基準値(RE24・線形加重・wOBA の重み)から、選手 × シーズンの得点を分数で求める。
各得点は「平均的な選手に比べて、チームの得点(守備は失点)を何点増やした(減らした)か」。
  - 打撃:((wOBA − リーグ平均の wOBA)÷ 目盛り +(1 − 球場補正)× 1打席あたりの得点)× 打席数(D-162)
  - 走塁:打席ごとの得点期待値の変化から「同じ状況」の平均を引いた差を、選択の余地があった走者に等分(D-163)
  - 守備:(アウトにしたか − ポジション × 打球の種類のリーグ平均のアウト率)× アウト1つの得点の価値(D-164)
試合の計算と乱数の消費は変えない。記録の項目も足さない(D-165)。画面には出さない(③で出す)。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Iterable, Mapping

from .baselines import END, WOBA_EVENTS, Baselines, _WEIGHT_NAME, event_of, state_index
from .baserunning import OUTFIELD
from .game import GameResult
from .records import Records, season_records

DH = "DH"
BATTED_BALLS = ("ground", "line", "fly")
FIELDED_OUTS = ("ground_out", "fly_out", "line_out")  # 野手が処理してアウトにした結果
_NON_OUT_EVENT = {"single": "B1", "double": "B2", "triple": "B3", "error": "ROE"}  # 処理できる打球で、アウトにならなかった結果


@dataclass
class PlayerRuns:
    """選手 × シーズンの得点(分数)と、守備の機会。"""

    batting: Fraction = Fraction(0)
    baserunning: Fraction = Fraction(0)
    fielding: Fraction = Fraction(0)
    fielding_by_position: dict[str, Fraction] = field(default_factory=dict)
    chances_by_position: dict[str, int] = field(default_factory=dict)  # 担当した打球の数(処理できる打球)
    outs_by_position: dict[str, int] = field(default_factory=dict)  # 守備アウト数(守備イニング × 3)
    plate_appearances: int = 0  # wOBA の分母(打数 + 四球 + 死球 + 犠飛)

    def to_dict(self) -> dict:
        return {
            "batting": str(self.batting),
            "baserunning": str(self.baserunning),
            "fielding": str(self.fielding),
            "fielding_by_position": {k: str(v) for k, v in sorted(self.fielding_by_position.items())},
            "chances_by_position": dict(sorted(self.chances_by_position.items())),
            "outs_by_position": dict(sorted(self.outs_by_position.items())),
            "plate_appearances": self.plate_appearances,
        }

    def add(self, other: "PlayerRuns") -> None:
        self.batting += other.batting
        self.baserunning += other.baserunning
        self.fielding += other.fielding
        for mine, theirs in (
            (self.fielding_by_position, other.fielding_by_position),
            (self.chances_by_position, other.chances_by_position),
            (self.outs_by_position, other.outs_by_position),
        ):
            for k, v in theirs.items():
                mine[k] = mine.get(k, 0) + v
        self.plate_appearances += other.plate_appearances


# ---- 打席ごとの見方 ----

def discretionary_runners(x) -> list[str]:
    """選択の余地があった走者(確率で進塁が決まった走者)の選手 ID(D-163)。打者は含めない。"""
    result = x.pa.result
    outs = x.base_out.outs
    on = {m.start: m.player_id for m in x.moves if m.start > 0}
    r1, r2, r3 = on.get(1), on.get(2), on.get(3)
    outfield = x.pa.fielder in OUTFIELD
    picked: list[str | None] = []
    if result == "single" and outfield:
        picked = [r2, r1]
    elif result == "double":
        picked = [r1]
    elif result == "ground_out" and outs < 2:
        forced2 = r1 is not None and r2 is not None
        forced3 = forced2 and r3 is not None
        picked = [r1, None if forced2 else r2, None if forced3 else r3]
    elif result == "fly_out" and outs < 2 and outfield:
        picked = [r3, r2]
    elif result == "error":
        picked = [r3, r2, r1]
    return [r for r in picked if r is not None]


def situation_key(x) -> tuple:
    """「同じ状況」の鍵:結果の種類 × 打席の前の状況 × 外野への打球か(単打・フライアウトのとき。D-163)。"""
    outfield = x.pa.fielder in OUTFIELD if x.pa.result in ("single", "fly_out") else None
    return (x.pa.result, state_index(x.base_out), outfield)


def _half_innings(result: GameResult) -> list[list]:
    halves: dict[tuple, list] = {}
    for x in result.log:
        halves.setdefault((x.inning, x.half), []).append(x)
    return list(halves.values())


def _delta_re(re24, pas: list, k: int) -> Fraction | None:
    """打席 k の得点期待値の変化(後 − 前 + 得点)。期待値が分からない状況は None。"""
    x = pas[k]
    before = re24[state_index(x.base_out)]
    after = Fraction(0) if k + 1 >= len(pas) else re24[state_index(pas[k + 1].base_out)]
    if before is None or after is None:
        return None
    return after - before + x.runs


# ---- 今シーズンの記録からの集計(状況の平均、アウト率、アウトの価値) ----

@dataclass
class SeasonContext:
    """走塁・守備の期待値の元になる、今シーズンの記録の集計。"""

    situation_total: dict[tuple, Fraction] = field(default_factory=dict)  # 状況 → 期待値の変化の合計
    situation_count: Counter = field(default_factory=Counter)  # 状況 → 打席数
    fielding_outs: Counter = field(default_factory=Counter)  # (ポジション, 打球の種類) → アウトにした数
    fielding_chances: Counter = field(default_factory=Counter)  # (ポジション, 打球の種類) → 担当した打球の数
    non_out_events: dict[str, Counter] = field(default_factory=lambda: {b: Counter() for b in BATTED_BALLS})  # 打球の種類 → アウトにならなかった結果 → 回数

    def situation_mean(self, key: tuple) -> Fraction | None:
        n = self.situation_count.get(key, 0)
        return self.situation_total[key] / n if n else None

    def out_rate(self, position: str, batted_ball: str) -> Fraction | None:
        n = self.fielding_chances.get((position, batted_ball), 0)
        return Fraction(self.fielding_outs.get((position, batted_ball), 0), n) if n else None

    def out_value(self, batted_ball: str, baselines: Baselines) -> Fraction:
        """アウト1つの得点の価値 =(アウトにならなかった結果の線形加重の平均)−(アウトの線形加重)(D-164)。"""
        lw = baselines.linear_weights
        out = lw.get("OUT")
        counts = self.non_out_events[batted_ball]
        usable = {e: n for e, n in counts.items() if lw.get(e) is not None}
        total = sum(usable.values())
        if out is None or not total:
            return Fraction(0)
        return sum((lw[e] * n for e, n in usable.items()), Fraction(0)) / total - out


def season_context(results: Iterable[GameResult], baselines: Baselines) -> SeasonContext:
    ctx = SeasonContext()
    re24 = baselines.re24
    for result in results:
        for pas in _half_innings(result):
            for k, x in enumerate(pas):
                if x.walkoff:
                    continue  # サヨナラで打ち切った打席は、期待値の変化が正しく測れない
                if discretionary_runners(x):
                    d = _delta_re(re24, pas, k)
                    if d is not None:
                        key = situation_key(x)
                        ctx.situation_total[key] = ctx.situation_total.get(key, Fraction(0)) + d
                        ctx.situation_count[key] += 1
        for x in result.log:
            pa = x.pa
            if pa.fielder and not pa.unfieldable and x.fielder_id and pa.batted_ball:
                key = (pa.fielder, pa.batted_ball)
                ctx.fielding_chances[key] += 1
                if pa.result in FIELDED_OUTS:
                    ctx.fielding_outs[key] += 1
                elif pa.result in _NON_OUT_EVENT:
                    ctx.non_out_events[pa.batted_ball][_NON_OUT_EVENT[pa.result]] += 1
    return ctx


# ---- 選手 × シーズンの得点 ----

def woba_of(counts: Mapping[str, int], values: Mapping[str, Fraction]) -> tuple[Fraction | None, int]:
    """wOBA と、その分母(打数 + 四球 + 死球 + 犠飛)。"""
    denom = counts.get("AB", 0) + counts.get("BB", 0) + counts.get("HBP", 0) + counts.get("SF", 0)
    if not denom:
        return None, 0
    keys = {"BB": "BB", "HBP": "HBP", "B1": "B1", "B2": "B2", "B3": "B3", "HR": "HR"}
    total = sum((values[_WEIGHT_NAME[e]] * counts.get(keys[e], 0) for e in WOBA_EVENTS), Fraction(0))
    return total / denom, denom


def batting_runs(counts: Mapping[str, int], values: Mapping[str, Fraction], park_factor: Fraction = Fraction(1)) -> tuple[Fraction, int]:
    """打撃の得点(D-162)と wOBA の分母。"""
    woba, denom = woba_of(counts, values)
    if woba is None:
        return Fraction(0), 0
    per_pa = (woba - values["lg_woba"]) / values["woba_scale"] + (1 - park_factor) * values["lg_r_pa"]
    return per_pa * denom, denom


def season_player_runs(
    results: list[GameResult],
    baselines: Baselines,
    park_factor_of: Callable[[str], Fraction] | None = None,
    records: Records | None = None,
    context: SeasonContext | None = None,
) -> dict[str, PlayerRuns]:
    """シーズンの試合の結果から、選手ごとの打撃・走塁・守備の得点と守備の機会を求める。

    park_factor_of は選手 ID → 球場補正(wRC+ と同じ値。省略時は 1.0)。
    """
    rec = records if records is not None else season_records(results)
    ctx = context if context is not None else season_context(results, baselines)
    out: dict[str, PlayerRuns] = {}

    def of(pid: str) -> PlayerRuns:
        return out.setdefault(pid, PlayerRuns())

    # 打撃
    for pid, counts in rec.batters.items():
        pf = park_factor_of(pid) if park_factor_of is not None else Fraction(1)
        runs, denom = batting_runs(counts, baselines.values, pf)
        p = of(pid)
        p.batting += runs
        p.plate_appearances += denom

    re24 = baselines.re24
    out_values = {b: ctx.out_value(b, baselines) for b in BATTED_BALLS}
    for result in results:
        # 走塁
        for pas in _half_innings(result):
            for k, x in enumerate(pas):
                if x.walkoff:
                    continue
                runners = discretionary_runners(x)
                if not runners:
                    continue
                d = _delta_re(re24, pas, k)
                mean = ctx.situation_mean(situation_key(x))
                if d is None or mean is None:
                    continue
                share = (d - mean) / len(runners)
                for pid in runners:
                    of(pid).baserunning += share
        # 守備(担当した打球)
        for x in result.log:
            pa = x.pa
            if not (pa.fielder and not pa.unfieldable and x.fielder_id and pa.batted_ball):
                continue
            rate = ctx.out_rate(pa.fielder, pa.batted_ball)
            if rate is None:
                continue
            made = 1 if pa.result in FIELDED_OUTS else 0
            value = (made - rate) * out_values[pa.batted_ball]
            p = of(x.fielder_id)
            p.fielding += value
            p.fielding_by_position[pa.fielder] = p.fielding_by_position.get(pa.fielder, Fraction(0)) + value
            p.chances_by_position[pa.fielder] = p.chances_by_position.get(pa.fielder, 0) + 1
        # 守備アウト数(打順表の守備位置 × 守備側として記録したアウト数。D-165)
        outs_by_team = Counter()
        for x in result.log:
            outs_by_team[x.fielding_team_id] += x.outs_made
        for team_id, slots in result.lineups.items():
            for _, pid, position, _ in slots:
                if position != DH:
                    p = of(pid)
                    p.outs_by_position[position] = p.outs_by_position.get(position, 0) + outs_by_team[team_id]
    return out


def league_totals(runs: Mapping[str, PlayerRuns]) -> dict[str, Fraction]:
    """リーグ全体の合計(走塁・守備はちょうど 0、打撃は球場補正なしならちょうど 0 になるはず)。"""
    return {
        "batting": sum((r.batting for r in runs.values()), Fraction(0)),
        "baserunning": sum((r.baserunning for r in runs.values()), Fraction(0)),
        "fielding": sum((r.fielding for r in runs.values()), Fraction(0)),
    }


def player_park_factors(results: Iterable[GameResult], estimates) -> dict[str, Fraction]:
    """選手ごとの球場補正(前のシーズンまでの推定を、立った球場ごとの打席数で重みづけ。D-142)。estimates が None なら全員 1.0。"""
    from .parkfactors import player_park_factor

    park_pa: dict[str, Counter] = {}
    for result in results:
        for x in result.log:
            park_pa.setdefault(x.batter_id, Counter())[result.home_team_id] += 1
    return {pid: player_park_factor(estimates, pa) for pid, pa in park_pa.items()}


def runs_record(runs: Mapping[str, PlayerRuns]) -> dict:
    """指紋 (k) 用:選手 ID → 3つの得点(分数を文字に)。"""
    return {pid: {"batting": str(r.batting), "baserunning": str(r.baserunning), "fielding": str(r.fielding)} for pid, r in sorted(runs.items())}
