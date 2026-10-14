"""スカウト評価(F3-1。2 層の見誤り:D-212。作り直し③で見せ方を「A〜E の評価 + スカウトの一言」に。D-368)。

内部の推定値 = 真の現在の見せる能力 + 球団 × 選手ごとに固定のずれ。ずれは 2 層(D-212):
  - 共通の見誤り(選手 × 球団ごとに 1 つ。全項目に同じ値):N(0, common)
  - 項目ごとの見誤り(項目ごとに独立):N(0, item)
  sd = {"common": …, "item": …}(球団ごとの値。既定は設定の小・中・大)。
  - 総合(項目の平均)の誤差 = √(common² + item²/n)
画面に出すのは 2 つだけ(能力の数値・ふれ幅は出さない。D-368):
  - 評価(A〜E):ピークまでの伸びを見込んだ総合値(stats.projected_overall)+ N(0, 総合の誤差の標準偏差)(別の乱数)を、
    候補全体の分位点(cuts)で A〜E に分ける。見誤りがあるので少し外れる。
  - スカウトの一言:項目ごとの推定値を、同じ役割の候補の平均と標準偏差(item_stats)で標準化し、目立つ項目を 1〜2 個、設定の言葉にする。
乱数は derive_seed(手続きのシード, "scout:<球団>:<選手>") から作るので、同じ候補は何度見ても同じ。
乱数の並び:①共通の見誤り、②項目ごとの見誤りの平均 N(0, item/√n)、③評価の見誤り、④項目ごとの偏り × n(平均 0 に正規化)。
総合の推定値 = 真の総合値 + ① + ②。AI の判断(quick_value)は ①〜③ だけを引くので速く、report() と同じ値になる。
隠し情報(成長タイプ・伸びやすさ・型)は出さない。
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field

from .abilities import abilities_for
from .models import Player
from .season import derive_seed
from .stats import projected_overall

GRADES = ("A", "B", "C", "D", "E")
SCOUT_METHOD = 3  # 評価方式の版(3:A〜E の評価と一言。D-368。版 15 以前の保存データは読まない)


def _rng(seed: int, team_id: str, player_id: str) -> random.Random:
    return random.Random(derive_seed(seed, f"scout:{team_id}:{player_id}"))


def _true_overall(player: Player, items) -> float:
    return sum(player.abilities[i] for i in items) / len(items)


# ---- ずれの大きさ ----

def item_sd(sd: dict) -> float:
    """項目の推定値の誤差の標準偏差(共通 + 項目ごと)。"""
    return math.sqrt(float(sd["common"]) ** 2 + float(sd["item"]) ** 2)


def overall_sd(sd: dict, n: int) -> float:
    """総合の推定値(n 項目の平均)の誤差の標準偏差。"""
    return math.sqrt(float(sd["common"]) ** 2 + float(sd["item"]) ** 2 / n)


def _draws(player: Player, team_id: str, seed: int, sd: dict):
    """乱数の最初の 3 本(総合の見誤り、評価の見誤り)と、残りの乱数。"""
    items = abilities_for(player.role)
    n = len(items)
    rng = _rng(seed, team_id, player.id)
    common = rng.gauss(0, float(sd["common"]))
    mean_item = rng.gauss(0, float(sd["item"]) / math.sqrt(n))
    grade_noise = rng.gauss(0, overall_sd(sd, n))
    return items, rng, common + mean_item, grade_noise


def quick_overall(player: Player, team_id: str, seed: int, sd: dict) -> float:
    """総合の推定値(AI の判断用。report() の overall と同じ値)。"""
    items, _, quick, _ = _draws(player, team_id, seed, sd)
    return _true_overall(player, items) + quick


def quick_value(player: Player, team_id: str, seed: int, sd: dict, cuts: list[float]) -> tuple[float, str]:
    """総合の推定値と評価(A〜E。AI の判断用。乱数の最初の数本だけを使うので速い)。report() と同じ値。"""
    items, _, quick, grade_noise = _draws(player, team_id, seed, sd)
    return _true_overall(player, items) + quick, grade_of(projected_overall(player) + grade_noise, cuts)


def grade_cuts(pool: list[Player], shares: dict[str, float]) -> list[float]:
    """評価の段階の境目(A/B、B/C、C/D、D/E の順。候補全体のピークの見込みの総合値の分位点)。"""
    values = sorted((projected_overall(p) for p in pool), reverse=True)
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


def item_stats(pool: list[Player]) -> dict[str, dict[str, list[float]]]:
    """役割 → 項目 → [平均, 標準偏差](候補の真の能力。一言で目立つ項目を選ぶ物差し)。手続きの開始時に作って保存する。"""
    out: dict[str, dict[str, list[float]]] = {}
    for role in sorted({p.role for p in pool}):
        group = [p for p in pool if p.role == role]
        out[role] = {}
        for item in abilities_for(role):
            values = [float(p.abilities[item]) for p in group]
            sd = statistics.pstdev(values) if len(values) > 1 else 0.0
            out[role][item] = [round(statistics.fmean(values), 4), round(max(sd, 1.0), 4)]
    return out


def strengths(estimates: dict[str, float], stats: dict[str, list[float]], comment: dict) -> list[str]:
    """目立つ項目(1〜2 個。なければ空)。1 つ目は標準化した値が min_z 以上、2 つ目は min_z 以上で 1 つ目との差が second_gap 以内。"""
    zs = sorted((((v - stats[k][0]) / stats[k][1], k) for k, v in estimates.items() if k in stats), key=lambda x: (-x[0], x[1]))
    min_z = float(comment["min_z"])
    if not zs or zs[0][0] < min_z:
        return []
    out = [zs[0][1]]
    if len(zs) > 1 and zs[1][0] >= min_z and zs[1][0] >= zs[0][0] - float(comment["second_gap"]):
        out.append(zs[1][1])
    return out


def comment_text(keys: list[str], comment: dict) -> str:
    if not keys:
        return str(comment["none"])
    return "、".join(str(comment["phrases"][k]) for k in keys)


@dataclass
class ScoutReport:
    team_id: str
    sd: dict  # {"common", "item"}
    overall: float  # 総合の推定値(内部。画面には出さない)
    items: dict[str, float]  # 項目 → 推定値(内部。画面には出さない)
    grade: str  # A〜E
    strengths: list[str] = field(default_factory=list)  # 一言にした項目
    comment: str = ""  # スカウトの一言
    method: int = SCOUT_METHOD

    def to_public(self) -> dict:
        """画面に出す形(評価と一言だけ。推定値・真の値・隠し情報は含めない)。"""
        return {"team_id": self.team_id, "grade": self.grade, "comment": self.comment}

    def to_dict(self) -> dict:
        return {"team_id": self.team_id, "sd": {k: round(float(v), 3) for k, v in self.sd.items()}, "overall": round(self.overall, 3), "items": {k: round(v, 3) for k, v in self.items.items()}, "grade": self.grade, "strengths": list(self.strengths), "comment": self.comment, "method": self.method}

    @classmethod
    def from_dict(cls, d: dict) -> "ScoutReport":
        return cls(str(d["team_id"]), {k: float(v) for k, v in d["sd"].items()}, float(d["overall"]), {k: float(v) for k, v in d["items"].items()}, str(d["grade"]), [str(x) for x in d.get("strengths", [])], str(d.get("comment", "")), int(d.get("method", SCOUT_METHOD)))


def report(player: Player, team_id: str, seed: int, sd: dict, cuts: list[float], stats: dict, comment: dict) -> ScoutReport:
    """1 人分の評価。quick_value と同じ乱数の並びなので、総合の推定値と評価は一致する。
    stats は item_stats の結果(役割 → 項目 → [平均, 標準偏差])、comment は設定(draft.json の scouting.comment)。"""
    items, rng, quick, grade_noise = _draws(player, team_id, seed, sd)
    n = len(items)
    devs = [rng.gauss(0, float(sd["item"])) for _ in items]
    mean_dev = sum(devs) / n
    est = {item: player.abilities[item] + quick + (d - mean_dev) for item, d in zip(items, devs)}
    keys = strengths(est, (stats or {}).get(player.role, {}), comment)
    return ScoutReport(
        team_id=team_id,
        sd={k: float(v) for k, v in sd.items()},
        overall=_true_overall(player, items) + quick,
        items=est,
        grade=grade_of(projected_overall(player) + grade_noise, cuts),
        strengths=keys,
        comment=comment_text(keys, comment),
    )
