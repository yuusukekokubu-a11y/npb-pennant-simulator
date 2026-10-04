"""スカウト評価(F3-1。D-199、D-200、D-206。2 層化:D-212〜D-215)。

評価 = 真の現在の能力 + 球団 × 選手ごとに固定のずれ。ずれは 2 層(方式の版 2。D-212):
  - 共通の見誤り(選手 × 球団ごとに 1 つ。全項目に同じ値):N(0, common)
  - 項目ごとの見誤り(項目ごとに独立):N(0, item)
  sd = {"common": …, "item": …}(球団ごとの値。既定は設定の小・中・大)。
  - 項目の誤差の標準偏差 = √(common² + item²)、総合(項目の平均)の誤差 = √(common² + item²/n)
  - ふれ幅はそれぞれの標準偏差 × margin_z(1.28 なら真の値が入る確率が約 80%。D-213)
  - 天井:潜在能力の総合値 + N(0, 総合の誤差の標準偏差)(別の乱数。D-214)を、候補全体の分位点(cuts)で S〜D に分ける
乱数は derive_seed(手続きのシード, "scout:<球団>:<選手>") から作るので、同じ候補は何度見ても同じ値(D-199)。
乱数の並び:①共通の見誤り、②項目ごとの見誤りの平均 N(0, item/√n)、③天井の見誤り、④項目ごとの偏り × n(平均 0 に正規化)。
総合の推定値 = 真の総合値 + ① + ②。AI の判断(quick_value)は ①〜③ だけを引くので速く、report() と同じ値になる。
旧方式(版 1。F3-1 の形)は method=1 として残す(読み込んだ時点で進行中だった手続きだけが使う。D-215)。
隠し情報(潜在能力そのもの・成長タイプ・型)は出さない。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .abilities import ITEM_LABELS, strength_items_for
from .models import Player
from .season import derive_seed
from .stats import potential_overall

GRADES = ("S", "A", "B", "C", "D")
LOW, HIGH = 20, 80
SCOUT_METHOD = 2  # 評価方式の版(1:F3-1 の形、2:2 層の見誤り。D-215)


def _rng(seed: int, team_id: str, player_id: str) -> random.Random:
    return random.Random(derive_seed(seed, f"scout:{team_id}:{player_id}"))


def _true_overall(player: Player, items) -> float:
    return sum(player.ratings[i] for i in items) / len(items)


# ---- ずれの大きさ ----

def item_sd(sd: dict) -> float:
    """項目の推定値の誤差の標準偏差(共通 + 項目ごと)。"""
    return math.sqrt(float(sd["common"]) ** 2 + float(sd["item"]) ** 2)


def overall_sd(sd: dict, n: int) -> float:
    """総合の推定値(n 項目の平均)の誤差の標準偏差。"""
    return math.sqrt(float(sd["common"]) ** 2 + float(sd["item"]) ** 2 / n)


def total_from_single(value: float) -> dict:
    """1 つの数(旧方式の標準偏差)を 2 層の形に直す(共通 0.8・項目ごと 0.6。合計は元の値のまま。版 8 の変換に使う)。"""
    return {"common": round(0.8 * float(value), 3), "item": round(0.6 * float(value), 3)}


def _draws(player: Player, team_id: str, seed: int, sd: dict, method: int):
    """乱数の最初の 3 本(総合の見誤り、天井の見誤り)と、残りの乱数。"""
    items = strength_items_for(player.role)
    n = len(items)
    rng = _rng(seed, team_id, player.id)
    if method == 1:
        s = item_sd(sd)
        quick = rng.gauss(0, s / math.sqrt(n))
        ceiling_noise = rng.gauss(0, s)
    else:
        common = rng.gauss(0, float(sd["common"]))
        mean_item = rng.gauss(0, float(sd["item"]) / math.sqrt(n))
        quick = common + mean_item
        ceiling_noise = rng.gauss(0, overall_sd(sd, n))
    return items, rng, quick, ceiling_noise


def quick_overall(player: Player, team_id: str, seed: int, sd: dict, method: int = SCOUT_METHOD) -> float:
    """総合の推定値(AI の判断用。report() の overall と同じ値)。"""
    items, _, quick, _ = _draws(player, team_id, seed, sd, method)
    return _true_overall(player, items) + quick


def quick_value(player: Player, team_id: str, seed: int, sd: dict, cuts: list[float], method: int = SCOUT_METHOD) -> tuple[float, str]:
    """総合の推定値と天井(AI の判断用。乱数の最初の数本だけを使うので速い)。report() と同じ値。"""
    items, _, quick, ceiling_noise = _draws(player, team_id, seed, sd, method)
    return _true_overall(player, items) + quick, grade_of(potential_overall(player) + ceiling_noise, cuts)


def ceiling_cuts(pool: list[Player], shares: dict[str, float]) -> list[float]:
    """天井の段階の境目(S/A、A/B、B/C、C/D の順。候補全体の潜在能力の総合値の分位点)。"""
    values = sorted((potential_overall(p) for p in pool), reverse=True)
    if not values:
        return [70.0, 60.0, 50.0, 40.0]
    cuts = []
    acc = 0.0
    for g in GRADES[:-1]:
        acc += shares[g]
        k = min(len(values) - 1, max(0, int(round(acc * len(values))) - 1))
        cuts.append(values[k])
    return cuts


def grade_of(value: float, cuts: list[float]) -> str:
    for g, cut in zip(GRADES, cuts):
        if value >= cut:
            return g
    return GRADES[-1]


@dataclass
class ScoutReport:
    team_id: str
    sd: dict  # {"common", "item"}(版 1 は {"common": 0, "item": 旧の標準偏差} ではなく、合計を item に入れた形)
    overall: float  # 総合の推定値
    margin: float  # 総合のふれ幅
    items: dict[str, float]  # 項目 → 推定値
    item_margin: float
    ceiling: str  # S〜D
    method: int = SCOUT_METHOD  # 評価方式の版(D-215)
    cuts: list[float] | None = None  # 天井の区切り(振り返りで実際の天井を出すため。D-216)

    def to_public(self) -> dict:
        """画面に出す形(推定値は 20〜80 に丸める。真の値・隠し情報は含めない)。"""
        return {
            "team_id": self.team_id,
            "overall": _shown(self.overall),
            "margin": round(self.margin, 1),
            "overall_text": f"{_shown(self.overall)} ± {self.margin:.0f}",
            "items": [{"key": k, "label": ITEM_LABELS[k], "estimate": _shown(v), "margin": round(self.item_margin, 1), "text": f"{_shown(v)} ± {self.item_margin:.0f}"} for k, v in self.items.items()],
            "ceiling": self.ceiling,
            "sd": {k: float(v) for k, v in self.sd.items()},
            "method": self.method,
        }

    def to_dict(self) -> dict:
        d = {"team_id": self.team_id, "sd": {k: round(float(v), 3) for k, v in self.sd.items()}, "overall": round(self.overall, 3), "margin": round(self.margin, 3), "items": {k: round(v, 3) for k, v in self.items.items()}, "item_margin": round(self.item_margin, 3), "ceiling": self.ceiling, "method": self.method}
        if self.cuts is not None:
            d["cuts"] = [round(c, 4) for c in self.cuts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ScoutReport":
        sd = d["sd"]
        if not isinstance(sd, dict):  # 版 8(方式 1)の入団時の評価は 1 つの数
            sd = {"common": 0.0, "item": float(sd)}
        cuts = d.get("cuts")
        return cls(str(d["team_id"]), {k: float(v) for k, v in sd.items()}, float(d["overall"]), float(d["margin"]), {k: float(v) for k, v in d["items"].items()}, float(d["item_margin"]), str(d["ceiling"]), int(d.get("method", 1)), None if cuts is None else [float(c) for c in cuts])


def _shown(v: float) -> int:
    return int(min(HIGH, max(LOW, round(v))))


def report(player: Player, team_id: str, seed: int, sd: dict, cuts: list[float], margin_z: float, method: int = SCOUT_METHOD) -> ScoutReport:
    """1 人分の評価(項目ごと)。quick_value と同じ乱数の並びなので、総合の推定値と天井は一致する。"""
    items, rng, quick, ceiling_noise = _draws(player, team_id, seed, sd, method)
    n = len(items)
    spread = item_sd(sd) if method == 1 else float(sd["item"])
    devs = [rng.gauss(0, spread) for _ in items]
    mean_dev = sum(devs) / n
    est = {item: player.ratings[item] + quick + (d - mean_dev) for item, d in zip(items, devs)}
    if method == 1:
        margin, item_margin = item_sd(sd) / math.sqrt(n) * margin_z, item_sd(sd) * margin_z
    else:
        margin, item_margin = overall_sd(sd, n) * margin_z, item_sd(sd) * margin_z
    return ScoutReport(
        team_id=team_id,
        sd={k: float(v) for k, v in sd.items()},
        overall=_true_overall(player, items) + quick,
        margin=margin,
        items=est,
        item_margin=item_margin,
        ceiling=grade_of(potential_overall(player) + ceiling_noise, cuts),
        method=method,
        cuts=list(cuts),
    )
