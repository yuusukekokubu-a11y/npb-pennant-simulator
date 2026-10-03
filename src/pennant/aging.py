"""年齢カーブ(D-030、D-031、D-033)。

年齢カーブは「ピークからの差(点数)」で持つ(DESIGN.md 検討中の論点)。
  現在の能力 = 潜在能力 - 年齢による差 + 能力の揺れ
年齢による差は、ピークより若いと (ピーク年齢 - 年齢) × 1年あたりの差、
ピークを過ぎると 衰退の速さ × 年数 + 加速 × 年数² で大きくなる。
「固定」グループ(ゴロ/フライ傾向)は年齢で変わらない。
"""

from __future__ import annotations

from .config import GenerationConfig

FIXED_GROUP = "fixed"


_GROUP_CACHE: dict[int, dict[str, str]] = {}  # 設定ごとの 項目 → グループ(何度も引くので覚えておく)


def group_of(config: GenerationConfig, item: str) -> str:
    table = _GROUP_CACHE.get(id(config))
    if table is None:
        table = {}
        for key, group in config["aging"]["groups"].items():
            for it in group["items"]:
                table[it] = key
        _GROUP_CACHE[id(config)] = table
    try:
        return table[item]
    except KeyError:
        raise KeyError(item) from None


def age_gap(config: GenerationConfig, item: str, age: float, growth_type: str) -> float:
    """その年齢で、潜在能力(ピーク)から何点下がっているか。0 以上。"""
    key = group_of(config, item)
    if key == FIXED_GROUP:
        return 0.0
    group = config["aging"]["groups"][key]
    peak = group["peak_age"] + config["aging"]["growth_types"][growth_type]["peak_shift"]
    if age < peak:
        return group["gap_per_year_before_peak"] * (peak - age)
    years = age - peak
    return group["decline_per_year"] * years + group["decline_accel"] * years * years


def current_rating(
    config: GenerationConfig,
    item: str,
    potential: float,
    age: float,
    growth_type: str,
    drift: float,
) -> float:
    """潜在能力から、現在の能力を出す。"""
    if group_of(config, item) == FIXED_GROUP:
        return potential
    return potential - age_gap(config, item, age, growth_type) + drift
