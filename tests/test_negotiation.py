"""志望と、提示への満足度の判定(F3-2b。D-238、D-245、D-250)の確認。
作り直し②で更改の交渉はなくなり(D-358)、志望は FA と市場で選手が選ぶときだけに使う。更改の流れは test_renewal.py。"""

import copy

import pytest

from pennant.config import ConfigError
from pennant.generate import generate_league
from pennant.config import load_generation_config, load_name_parts
from pennant.negotiation import (
    depth_ranks,
    depth_slots,
    draw_preference,
    judge,
    load_negotiation_settings,
    multi_year_bonus,
    satisfaction,
    standing_ranks,
    validate_negotiation_settings,
)

NEG = load_negotiation_settings()


# ---- 設定と志望 ----

def test_settings_and_validation():
    assert list(NEG.axes) == ["salary", "playing_time", "winning"]
    for gone in ("max_offers", "ai", "money_none", "threshold_without_money"):  # 更改の交渉と「なし」の設定はなくした(D-353、D-358)
        assert gone not in NEG.data
    assert NEG.reason("salary") == "年俸が低い" and NEG.reason("playing_time") == "出場機会が見込めない" and NEG.reason("winning") == "優勝を争えない"
    data = copy.deepcopy(NEG.data)
    data["axes"]["winning"]["kind"] = "money"
    with pytest.raises(ConfigError, match="kind"):
        validate_negotiation_settings(data)
    data = copy.deepcopy(NEG.data)
    data["fa"]["declare"]["base"] = 2
    with pytest.raises(ConfigError, match="fa.declare.base"):
        validate_negotiation_settings(data)
    # 軸はあとから足せる(設定に書くだけで、種類の関数は共通)
    data = copy.deepcopy(NEG.data)
    data["axes"]["home"] = {"label": "地元", "reason": "地元を離れたくない", "kind": "standing", "weight_shape": 1.0, "strength": 0.1}
    more = validate_negotiation_settings(data)
    assert set(draw_preference("X1", 5, more)) == {"salary", "playing_time", "winning", "home"}


def test_preference_is_separate_stream_and_deterministic():
    w = draw_preference("B01C001", 7, NEG)
    assert w == draw_preference("B01C001", 7, NEG) and w != draw_preference("B01C002", 7, NEG) and w != draw_preference("B01C001", 8, NEG)
    assert abs(sum(w.values()) - 1.0) < 1e-3 and all(v >= 0 for v in w.values())
    # 志望を付けても、選手の生成(能力)は変わらない(別の乱数系列。D-245)
    config, parts = load_generation_config(), load_name_parts()
    a, b = generate_league(3, config, parts), generate_league(3, config, parts)
    from pennant.negotiation import ensure_preferences

    ensure_preferences(a.all_players(), a.seed, NEG)
    assert [p.ratings for p in a.all_players()] == [p.ratings for p in b.all_players()]
    assert all(p.preference for p in a.all_players()) and all(p.preference is None for p in b.all_players())


def test_satisfaction_and_judge():
    ctx = {"rank": 0, "slots": 2.0, "standing": 1, "league_size": 6}
    assert satisfaction("salary", NEG, 1000, 1000, ctx) == pytest.approx(-0.2)  # 算定どおりはやや不満
    assert satisfaction("salary", NEG, 1250, 1000, ctx) == pytest.approx(0.8) and satisfaction("salary", NEG, 3000, 1000, ctx) == 1.0
    assert satisfaction("playing_time", NEG, 1000, 1000, ctx) == 1.0 and satisfaction("playing_time", NEG, 1000, 1000, {**ctx, "rank": 2}) == 0.0
    assert satisfaction("playing_time", NEG, 1000, 1000, {**ctx, "rank": 9}) == -1.0
    assert satisfaction("winning", NEG, 1000, 1000, ctx) == 1.0 and satisfaction("winning", NEG, 1000, 1000, {**ctx, "standing": 6}) == -1.0
    assert satisfaction("winning", NEG, 1000, 1000, {**ctx, "standing": None}) == 0.0  # 順位がない(事前運転)
    pref = {"salary": 0.5, "playing_time": 0.3, "winning": 0.2}
    bad = {"rank": 9, "slots": 2.0, "standing": 6, "league_size": 6}
    r1 = judge(NEG, pref, 1, 1000, 1000, 25, bad, 0.0)
    r2 = judge(NEG, pref, 1, 1300, 1000, 25, bad, 0.0)
    assert r2["score"] > r1["score"] and r1["reason"] == "playing_time"
    heavy = {"salary": 0.9, "playing_time": 0.05, "winning": 0.05}
    good = {**bad, "rank": 0, "standing": 1}
    r3 = judge(NEG, heavy, 1, 1000, 1000, 25, good, 0.0)
    assert r3["reason"] == "salary" and set(r3["values"]) == set(NEG.axes)  # 年俸の軸は常に効く
    # 複数年の加点:長い年数・高い年齢ほど大きい
    assert multi_year_bonus(1, 35, NEG) == 0 and 0 < multi_year_bonus(3, 25, NEG) < multi_year_bonus(5, 25, NEG) < multi_year_bonus(5, 33, NEG)
    assert judge(NEG, pref, 5, 1000, 1000, 33, bad, 0.0)["score"] > r1["score"]


def test_depth_and_standing_ranks():
    league = generate_league(4, load_generation_config(), load_name_parts())
    team = league.teams[0]
    ranks = depth_ranks(team.players, lambda p: p.age)  # 評価の代わりに年齢で
    sp = sorted((p for p in team.players if p.position == "SP"), key=lambda p: -p.age)
    assert ranks[sp[0].id] == 0 and ranks[sp[-1].id] == sum(1 for p in sp if p.age > sp[-1].age)
    slots = depth_slots(load_generation_config())
    assert slots["SP"] == pytest.approx(14 * 14 / 32) and slots["C"] == pytest.approx(15 * 9 / 38)
    rec = {t.id: (60 - i, 60 + i) for i, t in enumerate(league.teams)}
    st = standing_ranks(league.teams, rec)
    assert sorted(st[t.id] for t in league.teams if t.league_index == 0) == [1, 2, 3, 4, 5, 6] and standing_ranks(league.teams, None) == {}
