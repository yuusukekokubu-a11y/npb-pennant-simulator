"""年齢カーブ(D-030、D-031、D-033、D-350)。

年齢カーブは「ピークからの差(点数)」で持つ。
年齢による差は、ピークより若いと (ピーク年齢 - 年齢) × 1年あたりの差、
ピークを過ぎると 衰退の速さ × 年数 + 加速 × 年数² で大きくなる。

作り直し①(D-350)から、潜在能力(ピーク時の能力)は持たない。毎年のオフの増減は
  年齢による増減(ある年齢から次の年齢への差の変わり方)× 伸びやすさ(伸びる年だけ)+ 小さな揺れ
で決める。生成のときだけ「ピーク時の水準」を一度引き、そこから年齢による差を引いて今の能力にする。
「固定」グループの項目は年齢で変わらない(今の見せる能力にはない)。
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


def yearly_change(config: GenerationConfig, item: str, age: float, growth_type: str, ease: float) -> float:
    """age 歳から age+1 歳になるときの、年齢による増減の見込み(伸びる年は伸びやすさを掛ける。D-350)。"""
    if group_of(config, item) == FIXED_GROUP:
        return 0.0
    change = age_gap(config, item, age, growth_type) - age_gap(config, item, age + 1, growth_type)
    return change * ease if change > 0 else change


def expected_gain(config: GenerationConfig, item: str, age: float, growth_type: str, ease: float = 1.0) -> float:
    """今の年齢からピークまでに伸びる見込みの合計(ドラフト候補の天井の目安に使う。D-352)。"""
    if group_of(config, item) == FIXED_GROUP:
        return 0.0
    return age_gap(config, item, age, growth_type) * ease
