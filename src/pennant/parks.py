"""球場の倍率の割り当て(第2弾②a。D-064、D-136、D-137)。

球場ごとに、本塁打の倍率と BABIP の倍率(整数の千分率)を持つ。
  - リーグのシードから SHA-256 で導いた別のシードの random.Random を使う(既存の選手・球団の生成の乱数は変えない)。
  - 乱数は random() だけを使い、あとは整数の計算だけ(環境による違いが出ない)。
  - リーグごとに6つを範囲内で一様に引き、合計が 6000(平均 1.000)になるよう差を均等に配る。
    範囲に収まらなければ、決まった手順で引き直す。同じシードなら同じ倍率になる。
"""

from __future__ import annotations

import hashlib
import random

from .config import GenerationConfig
from .models import League, ParkFactors

SCALE = 1000
MAX_REDRAWS = 10_000


def park_seed(league_seed: int) -> int:
    """倍率を作る乱数のシード(リーグのシードから、環境に依存しない方法で決める)。"""
    digest = hashlib.sha256(f"pennant:{league_seed}:parks".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def thousandths(value: float) -> int:
    return int(round(value * SCALE))


def draw_balanced(rng: random.Random, n: int, low: int, high: int) -> list[int]:
    """n 個の整数を [low, high] から一様に引き、合計が n × 1000 になるよう差を均等に配る。範囲に収まるまで引き直す。"""
    if not low <= SCALE <= high:
        raise ValueError(f"倍率の範囲 {low}〜{high} に 1000(= 1.0)が入っていません")
    for _ in range(MAX_REDRAWS):
        values = [low + int(rng.random() * (high - low + 1)) for _ in range(n)]
        values = [min(v, high) for v in values]
        q, r = divmod(n * SCALE - sum(values), n)
        values = [v + q + (1 if i < r else 0) for i, v in enumerate(values)]
        if all(low <= v <= high for v in values):
            return values
    raise RuntimeError("球場の倍率を範囲内に収められませんでした(範囲が狭すぎます)")


def park_ranges(config: GenerationConfig) -> dict[str, tuple[int, int]]:
    """設定ファイルの範囲(小数)を、千分率の整数にする。設定がなければ倍率なし(1000〜1000)。"""
    parks = config.data.get("parks")
    if not parks:
        return {"home_run": (SCALE, SCALE), "babip": (SCALE, SCALE)}
    return {key: (thousandths(parks[f"{key}_range"][0]), thousandths(parks[f"{key}_range"][1])) for key in ("home_run", "babip")}


def assign_parks(league: League, config: GenerationConfig) -> None:
    """リーグの全球団に、本拠地の球場の倍率を割り当てる(リーグごとに平均 1.000)。"""
    rng = random.Random(park_seed(league.seed))
    ranges = park_ranges(config)
    groups: dict[int, list] = {}
    for t in league.teams:
        groups.setdefault(t.league_index, []).append(t)
    for index in sorted(groups):
        teams = groups[index]
        hr = draw_balanced(rng, len(teams), *ranges["home_run"])
        babip = draw_balanced(rng, len(teams), *ranges["babip"])
        for team, h, b in zip(teams, hr, babip):
            team.park = ParkFactors(h, b)


def expected_run_factors(league: League, model, values: dict) -> dict[str, float]:
    """真の倍率から求めた「1打席あたりの得点の出やすさ」の目安(開発者向け・テスト用。画面には出さない)。

    平均的な打者と投手の対戦の、打席の結果の確率(球場の倍率入り)に wOBA の重みを掛けて期待 wOBA を求め、
    (期待 wOBA − 倍率なしの期待 wOBA)÷ wOBA の尺度 で「1打席あたりの得点の増減」に直し、リーグの平均得点(lg_r_pa)に足して比にする。
    各リーグの平均が 1.0 になるよう割る。推定(parkfactors.py)の「得点」と比べるための真の値。
    """
    from .pa_stats import average_defense, average_player

    batter, pitcher, defense = average_player("batter"), average_player("pitcher"), average_defense()
    weights = {"walk": "w_bb", "hit_by_pitch": "w_hbp", "single": "w_1b", "double": "w_2b", "triple": "w_3b", "home_run": "w_hr"}

    def woba(park):
        probs = model.probabilities(batter, pitcher, defense, park=park)
        return sum(float(probs[k]) * float(values[v]) for k, v in weights.items())

    base = woba(ParkFactors())
    r0 = float(values["lg_r_pa"])
    out = {t.id: (r0 + (woba(t.park) - base) / float(values["woba_scale"])) / r0 for t in league.teams}
    groups: dict[int, list[str]] = {}
    for t in league.teams:
        groups.setdefault(t.league_index, []).append(t.id)
    for ids in groups.values():
        mean = sum(out[i] for i in ids) / len(ids)
        for i in ids:
            out[i] /= mean
    return out


def neutralize_parks(league: League) -> None:
    """すべての球場の倍率を 1.0 にする(回帰の確認・旧版のセーブデータ用)。"""
    for t in league.teams:
        t.park = ParkFactors()
