"""FA で選手が提示を選ぶときの点数(作り直し③。D-365。選手ごとの志望はなくした)と、その材料(出場機会・前年の順位)。

- 点数 = 年俸 ×(元の球団の提示なら 1 + 愛着)÷(算定年俸 × 期待の倍率)− 1
         + 年数の加点 + 出場機会の重み × 出場機会 + 勝てるチームの重み × 勝てるチーム + 乱数
  - 出場機会(−1〜1):1 − r ÷ k。r は提示した球団の同じポジションで自分より評価の高い選手の数、k はそのポジションの一軍の枠の目安
  - 勝てるチーム(−1〜1):提示した球団の前年のリーグ内の順位(1 位 +1 〜 最下位 −1。順位がなければ 0)
  - 年数の加点:2 年目から 1 年ごとに per_year、age_start 歳を超えると 1 歳ごとに per_age_year ずつ大きく
- 選手は点数が最も高い提示を選ぶ。最も高い点数が accept 未満なら選ばない(fa.close_round)。値は data/negotiation.json の choice(全選手に共通)。
- 年俸の算定(contracts.py)は点数に依存しない。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json


@dataclass(frozen=True)
class NegotiationSettings:
    data: dict
    source: str

    @property
    def choice(self) -> dict:
        return self.data["choice"]


def load_negotiation_settings(path: str | Path | None = None) -> NegotiationSettings:
    data, source = _read_json(path, "negotiation.json")
    return validate_negotiation_settings(data, source)


def validate_negotiation_settings(root, source: str = "negotiation.json") -> NegotiationSettings:
    c = _Checker()
    if not isinstance(root, dict):
        raise ConfigError(source, ["全体がまとまり({ })ではありません"])
    ch = c.section(c.get(root, "choice", ""), "choice")
    c.number(c.get(ch, "attachment", "choice"), "choice.attachment", 0, 1)
    c.number(c.get(ch, "playing_time", "choice"), "choice.playing_time", 0, 5)
    c.number(c.get(ch, "winning", "choice"), "choice.winning", 0, 5)
    c.number(c.get(ch, "accept", "choice"), "choice.accept", -5, 5)
    c.number(c.get(ch, "noise_sd", "choice"), "choice.noise_sd", 0, 5)
    y = c.section(c.get(ch, "years", "choice"), "choice.years")
    c.number(c.get(y, "per_year", "choice.years"), "choice.years.per_year", 0, 1)
    c.integer(c.get(y, "age_start", "choice.years"), "choice.years.age_start", 18, 60)
    c.number(c.get(y, "per_age_year", "choice.years"), "choice.years.per_age_year", 0, 1)
    from .fa import validate_fa

    validate_fa(c, root)
    if c.problems:
        raise ConfigError(source, c.problems)
    return NegotiationSettings(copy.deepcopy(root), source)


# ---- 材料 ----

def depth_slots(config) -> dict[str, float]:
    """ポジションごとの一軍の枠の目安 = 一軍の人数 × ポジションの人数の目安の比率(投手・野手それぞれ)。"""
    out = {}
    for group, ft_key in (("pitchers", "pitchers"), ("fielders", "fielders")):
        roster = {pos: float(n) for pos, n in config["roster"][group].items()}
        total = sum(roster.values()) or 1.0
        n = float(config["first_team"][ft_key])
        for pos, v in roster.items():
            out[pos] = max(0.5, n * v / total)
    return out


def standing_ranks(teams, records: dict[str, tuple[int, int]] | None) -> dict[str, int]:
    """リーグ内の順位(勝率の高い順。同率は ID の順)。成績がなければ空。"""
    if not records:
        return {}
    out = {}
    leagues: dict[int, list] = {}
    for t in teams:
        leagues.setdefault(t.league_index, []).append(t.id)

    def pct(tid):
        w, l = records.get(tid, (0, 0))
        return w / (w + l) if w + l else 0.5

    for ids in leagues.values():
        for i, tid in enumerate(sorted(ids, key=lambda t: (-pct(t), t)), start=1):
            out[tid] = i
    return out


def playing_time(context: dict) -> float:
    """出場機会(−1〜1):1 − 自分より評価の高い選手の数 ÷ 一軍の枠の目安。"""
    k = float(context.get("slots") or 1.0)
    return max(-1.0, min(1.0, 1.0 - float(context.get("rank", 0)) / k))


def winning(context: dict) -> float:
    """勝てるチーム(−1〜1):前年のリーグ内の順位(1 位 +1 〜 最下位 −1)。順位がなければ 0。"""
    rank = context.get("standing")
    n = int(context.get("league_size") or 0)
    if not rank or n <= 1:
        return 0.0
    return max(-1.0, min(1.0, ((n + 1) / 2.0 - float(rank)) / ((n - 1) / 2.0)))


def years_bonus(settings: NegotiationSettings, years: int, age: int) -> float:
    y = settings.choice["years"]
    extra = max(0, int(years) - 1)
    return extra * (float(y["per_year"]) + float(y["per_age_year"]) * max(0, int(age) - int(y["age_start"])))


# ---- 点数 ----

def offer_score(settings: NegotiationSettings, salary: int, calc: int, expect: float, former: bool, years: int, age: int, context: dict, noise: float) -> float:
    """提示の点数(D-365)。隠し情報(画面には出さない)。expect は期待の倍率(D-322)、former は元の球団の提示か(愛着)。"""
    ch = settings.choice
    base = max(1.0, float(calc) * float(expect))
    money = float(salary) * (1.0 + float(ch["attachment"]) if former else 1.0) / base - 1.0
    return money + years_bonus(settings, years, age) + float(ch["playing_time"]) * playing_time(context) + float(ch["winning"]) * winning(context) + float(noise)


def accepts(settings: NegotiationSettings, score: float) -> bool:
    return float(score) >= float(settings.choice["accept"])
