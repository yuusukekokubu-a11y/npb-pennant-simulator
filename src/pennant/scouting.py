"""スカウト評価(F3-1。D-199、D-200、D-206)。

評価 = 真の現在の能力 + 球団 × 選手ごとに固定のずれ(正規分布。標準偏差は球団ごとの値。既定は設定の小・中・大)。
  - 総合の推定値:真の総合値 + N(0, sd / √n)。乱数の最初の 1 本で決める(AI の判断はこれだけを使うので速い)
  - 項目の推定値:総合の推定値 + 項目ごとの偏り(平均 0 に正規化した N(0, sd))。項目のずれの標準偏差は sd になる
  - ふれ幅:sd × margin_z(1.28 なら真の値が入る確率が約 80%)。総合は sd / √n × margin_z
  - 天井:潜在能力の総合値 + N(0, sd) を、候補全体の分位点(cuts)で S〜D に分ける
乱数は derive_seed(手続きのシード, "scout:<球団>:<選手>") から作るので、同じ候補は何度見ても同じ値(D-199)。
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


def _rng(seed: int, team_id: str, player_id: str) -> random.Random:
    return random.Random(derive_seed(seed, f"scout:{team_id}:{player_id}"))


def _true_overall(player: Player, items) -> float:
    return sum(player.ratings[i] for i in items) / len(items)


def quick_overall(player: Player, team_id: str, seed: int, sd: float) -> float:
    """総合の推定値(AI の判断用。report() の overall と同じ値)。"""
    items = strength_items_for(player.role)
    rng = _rng(seed, team_id, player.id)
    return _true_overall(player, items) + rng.gauss(0, sd / math.sqrt(len(items)))


def quick_value(player: Player, team_id: str, seed: int, sd: float, cuts: list[float]) -> tuple[float, str]:
    """総合の推定値と天井(AI の判断用。乱数の最初の 2 本だけを使うので速い)。report() と同じ値。"""
    items = strength_items_for(player.role)
    rng = _rng(seed, team_id, player.id)
    quick = rng.gauss(0, sd / math.sqrt(len(items)))
    ceiling_noise = rng.gauss(0, sd)
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
    sd: float
    overall: float  # 総合の推定値
    margin: float  # 総合のふれ幅
    items: dict[str, float]  # 項目 → 推定値
    item_margin: float
    ceiling: str  # S〜D

    def to_public(self) -> dict:
        """画面に出す形(推定値は 20〜80 に丸める。真の値・隠し情報は含めない)。"""
        return {
            "team_id": self.team_id,
            "overall": _shown(self.overall),
            "margin": round(self.margin, 1),
            "overall_text": f"{_shown(self.overall)} ± {self.margin:.0f}",
            "items": [{"key": k, "label": ITEM_LABELS[k], "estimate": _shown(v), "margin": round(self.item_margin, 1), "text": f"{_shown(v)} ± {self.item_margin:.0f}"} for k, v in self.items.items()],
            "ceiling": self.ceiling,
            "sd": self.sd,
        }

    def to_dict(self) -> dict:
        return {"team_id": self.team_id, "sd": self.sd, "overall": round(self.overall, 3), "margin": round(self.margin, 3), "items": {k: round(v, 3) for k, v in self.items.items()}, "item_margin": round(self.item_margin, 3), "ceiling": self.ceiling}

    @classmethod
    def from_dict(cls, d: dict) -> "ScoutReport":
        return cls(str(d["team_id"]), float(d["sd"]), float(d["overall"]), float(d["margin"]), {k: float(v) for k, v in d["items"].items()}, float(d["item_margin"]), str(d["ceiling"]))


def _shown(v: float) -> int:
    return int(min(HIGH, max(LOW, round(v))))


def report(player: Player, team_id: str, seed: int, sd: float, cuts: list[float], margin_z: float) -> ScoutReport:
    """1 人分の評価(項目ごと)。quick_overall と同じ乱数の並びなので、総合の推定値は一致する。"""
    items = strength_items_for(player.role)
    n = len(items)
    rng = _rng(seed, team_id, player.id)
    quick = rng.gauss(0, sd / math.sqrt(n))
    ceiling_noise = rng.gauss(0, sd)
    devs = [rng.gauss(0, sd) for _ in items]
    mean_dev = sum(devs) / n
    est = {item: player.ratings[item] + quick + (d - mean_dev) for item, d in zip(items, devs)}
    return ScoutReport(
        team_id=team_id,
        sd=sd,
        overall=_true_overall(player, items) + quick,
        margin=sd / math.sqrt(n) * margin_z,
        items=est,
        item_margin=sd * margin_z,
        ceiling=grade_of(potential_overall(player) + ceiling_noise, cuts),
    )
