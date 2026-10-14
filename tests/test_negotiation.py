"""FA で選手が提示を選ぶときの点数(作り直し③。D-365)の確認。選手ごとの志望はなくした。
FA の流れは test_fa.py、主力級の期待は test_fa_star.py。"""

import copy

import pytest

from pennant.config import ConfigError, load_generation_config, load_name_parts
from pennant.generate import generate_league
from pennant.models import Player
from pennant.negotiation import (
    accepts,
    depth_slots,
    load_negotiation_settings,
    offer_score,
    playing_time,
    standing_ranks,
    validate_negotiation_settings,
    winning,
    years_bonus,
)

NEG = load_negotiation_settings()
GOOD = {"rank": 0, "slots": 2.0, "standing": 1, "league_size": 6}
BAD = {"rank": 9, "slots": 2.0, "standing": 6, "league_size": 6}


def test_settings_and_validation():
    ch = NEG.choice
    assert ch["attachment"] == pytest.approx(0.10) and ch["playing_time"] == pytest.approx(0.10) and ch["winning"] == pytest.approx(0.05)
    assert ch["accept"] == pytest.approx(-0.15)
    for gone in ("axes", "threshold", "multi_year"):  # 志望の軸としきい値はなくした(D-365)
        assert gone not in NEG.data
    assert "preference" not in Player.__dataclass_fields__
    data = copy.deepcopy(NEG.data)
    data["choice"]["attachment"] = 2
    with pytest.raises(ConfigError, match="choice.attachment"):
        validate_negotiation_settings(data)
    data = copy.deepcopy(NEG.data)
    del data["choice"]["years"]["per_year"]
    with pytest.raises(ConfigError, match="choice.years.per_year"):
        validate_negotiation_settings(data)
    data = copy.deepcopy(NEG.data)
    data["fa"]["declare"]["base"] = 2
    with pytest.raises(ConfigError, match="fa.declare.base"):
        validate_negotiation_settings(data)


def test_parts_of_the_score():
    assert playing_time(GOOD) == 1.0 and playing_time({**GOOD, "rank": 2}) == 0.0 and playing_time(BAD) == -1.0
    assert winning(GOOD) == 1.0 and winning(BAD) == -1.0 and winning({**GOOD, "standing": None}) == 0.0  # 順位がない(事前運転)
    assert winning({**GOOD, "standing": 3}) == pytest.approx(0.2)
    # 年数の加点:長い年数・高い年齢ほど大きい
    assert years_bonus(NEG, 1, 35) == 0 and 0 < years_bonus(NEG, 3, 25) < years_bonus(NEG, 5, 25) < years_bonus(NEG, 5, 33)
    assert years_bonus(NEG, 3, 25) == pytest.approx(2 * 0.03)


def test_money_is_central_with_attachment():
    base = offer_score(NEG, 1000, 1000, 1.0, False, 1, 28, GOOD, 0.0)
    assert base == pytest.approx(0.0 + 0.10 + 0.05)  # 算定どおり + 出場機会 + 勝てるチーム
    # 金額:1 割高いと 0.1 上がる
    assert offer_score(NEG, 1100, 1000, 1.0, False, 1, 28, GOOD, 0.0) - base == pytest.approx(0.1)
    # 愛着:元の球団の提示は 1 割増しに感じる(D-365)
    former = offer_score(NEG, 1000, 1000, 1.0, True, 1, 28, GOOD, 0.0)
    assert former - base == pytest.approx(0.1)
    # 出場機会と勝てるチームの差は最大でも 3 割の金額の差と同じ。それより大きい金額の差なら金額で選ぶ(金額が中心)
    rich_bad = offer_score(NEG, 1310, 1000, 1.0, False, 1, 28, BAD, 0.0)
    poor_good = offer_score(NEG, 1000, 1000, 1.0, False, 1, 28, GOOD, 0.0)
    assert rich_bad > poor_good
    # 主力級の期待:基準が 算定 × 倍率 になる(D-322)
    assert offer_score(NEG, 1200, 1000, 1.2, False, 1, 28, GOOD, 0.0) == pytest.approx(base)
    # 選ぶかどうか:−0.15 以上なら選ぶ
    assert accepts(NEG, -0.15) and not accepts(NEG, -0.151)
    assert not accepts(NEG, offer_score(NEG, 800, 1000, 1.0, False, 1, 28, BAD, 0.0))


def test_depth_slots_and_standing_ranks():
    league = generate_league(4, load_generation_config(), load_name_parts())
    slots = depth_slots(load_generation_config())
    assert slots["SP"] == pytest.approx(14 * 14 / 32) and slots["C"] == pytest.approx(15 * 9 / 38)
    rec = {t.id: (60 - i, 60 + i) for i, t in enumerate(league.teams)}
    st = standing_ranks(league.teams, rec)
    assert sorted(st[t.id] for t in league.teams if t.league_index == 0) == [1, 2, 3, 4, 5, 6] and standing_ranks(league.teams, None) == {}
