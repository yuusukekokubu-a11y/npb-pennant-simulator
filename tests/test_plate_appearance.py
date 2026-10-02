"""1回の打席の計算の受け入れ条件(1〜10)の確認。"""

import copy
import random
from collections import Counter
from dataclasses import replace

import pytest

from pennant.abilities import BATTER, FIELDER_POSITIONS, PITCHER
from pennant.pa_config import default_pa_data, load_pa_config, validate_pa_config
from pennant.pa_stats import TARGETS, FirstTeamPool, average_defense, average_player, expected_rates, rates_from
from pennant.plate_appearance import (
    RESULTS,
    BaseOutState,
    OddsRatioModel,
    batter_side,
    resolve_plate_appearance,
)


@pytest.fixture(scope="module")
def model():
    return OddsRatioModel(load_pa_config())


@pytest.fixture(scope="module")
def neutral_model():
    """左右の補正をなくした設定(平均的な選手どうしで、リーグ平均の率がそのまま出る)。"""
    data = default_pa_data()
    for side in ("same_side", "opposite_side"):
        data["platoon"][side] = {k: 1.0 for k in data["platoon"][side]}
    return OddsRatioModel(validate_pa_config(data))


def avg_batter(**ratings):
    return average_player(BATTER, "B", ratings)


def avg_pitcher(**ratings):
    return average_player(PITCHER, "P", ratings)


def probs(model, batter=None, pitcher=None, defense=None):
    return model.probabilities(batter or avg_batter(), pitcher or avg_pitcher(), defense or average_defense())


def odds(p):
    return p / (1 - p)


# ---- 受け入れ条件1:同じシードで同じ結果、シードを変えると変わる ----

def _run(model, seed, n=300):
    rng = random.Random(seed)
    b, p, d = avg_batter(), avg_pitcher(), average_defense()
    return [resolve_plate_appearance(b, p, d, BaseOutState(), rng, model) for _ in range(n)]


def test_same_seed_same_results(model):
    assert _run(model, 11) == _run(model, 11)


def test_different_seed_different_results(model):
    assert _run(model, 11) != _run(model, 12)


# ---- 受け入れ条件2:平均的な打者と投手で、リーグ平均の率に近づく ----

def test_average_matchup_reproduces_league_rates_exactly(neutral_model):
    cfg = neutral_model.config
    s1 = neutral_model.stage1(avg_batter(), avg_pitcher())
    for event, rate in cfg["stage1"]["league_rates"].items():
        assert s1[event] == pytest.approx(rate, abs=1e-12)
    bb = neutral_model.batted_ball(avg_batter(), avg_pitcher())
    for t, share in cfg["batted_ball"]["league_shares"].items():
        assert bb[t] == pytest.approx(share, abs=1e-12)
    for t in ("ground", "line", "fly"):
        ip = neutral_model.in_play(avg_batter(), avg_pitcher(), t, {"range": 50, "arm": 50, "fielding": 50})
        hit = sum(v for k, v in ip.items() if k not in ("error", "out"))
        assert hit == pytest.approx(cfg["in_play"][t]["hit_rate"], abs=1e-12)


def test_average_matchup_sampling_approaches_league_rates(neutral_model):
    rng = random.Random(5)
    b, p, d = avg_batter(), avg_pitcher(), average_defense()
    n = 40_000
    counts = Counter(neutral_model.resolve(b, p, d, BaseOutState(), rng).result for _ in range(n))
    for event, rate in neutral_model.config["stage1"]["league_rates"].items():
        assert counts[event] / n == pytest.approx(rate, abs=0.006)


def test_sampling_matches_exact_probabilities(model, all_players):
    batter = next(p for p in all_players if p.role == BATTER)
    pitcher = next(p for p in all_players if p.role == PITCHER)
    defense = average_defense()
    exact = model.probabilities(batter, pitcher, defense)
    rng = random.Random(9)
    n = 40_000
    counts = Counter(model.resolve(batter, pitcher, defense, BaseOutState(), rng).result for _ in range(n))
    for r in RESULTS:
        assert counts[r] / n == pytest.approx(exact[r], abs=0.007), r


# ---- 受け入れ条件3:各能力の方向性 ----

@pytest.mark.parametrize(
    "who,item,key,direction",
    [
        ("batter", "contact", "K%", -1),
        ("batter", "eye", "BB%", +1),
        ("batter", "power", "HR%", +1),
        ("batter", "batted_ball_quality", "BABIP", +1),
        ("batter", "speed", "BABIP", +1),
        ("pitcher", "strikeout", "K%", +1),
        ("pitcher", "control", "BB%", -1),
        ("pitcher", "stuff", "HR%", -1),
        ("pitcher", "contact_suppression", "BABIP", -1),
        ("fielder", "range", "BABIP", -1),
        ("fielder", "arm", "BABIP", -1),
        ("fielder", "fielding", "失策率(インプレーあたり)", -1),
    ],
)
def test_direction_of_each_ability(model, who, item, key, direction):
    def rate(value):
        b = avg_batter(**{item: value}) if who == "batter" else avg_batter()
        p = avg_pitcher(**{item: value}) if who == "pitcher" else avg_pitcher()
        d = average_defense({item: value}) if who == "fielder" else average_defense()
        return rates_from(model.probabilities(b, p, d))[key]

    assert (rate(60) - rate(40)) * direction > 0


@pytest.mark.parametrize("who", ["batter", "pitcher"])
def test_ground_ball_tendency_direction(model, who):
    """ゴロ/フライ傾向は高いほどゴロが多い(D-047)。"""
    def ground(value):
        b = avg_batter(gb_fb=value) if who == "batter" else avg_batter()
        p = avg_pitcher(gb_fb=value) if who == "pitcher" else avg_pitcher()
        return model.batted_ball(b, p)["ground"]

    assert ground(60) > ground(50) > ground(40)


# ---- 受け入れ条件4:両方が極端なとき、倍率の掛け算に近い(平均法ではない) ----

def test_extremes_multiply_like_odds_ratio(neutral_model):
    m = neutral_model
    p0 = m.stage1(avg_batter(), avg_pitcher())["home_run"]
    pb = m.stage1(avg_batter(power=80), avg_pitcher())["home_run"]
    pp = m.stage1(avg_batter(), avg_pitcher(stuff=20))["home_run"]
    both = m.stage1(avg_batter(power=80), avg_pitcher(stuff=20))["home_run"]
    expected_odds_ratio = (odds(pb) / odds(p0)) * (odds(pp) / odds(p0))
    assert odds(both) / odds(p0) == pytest.approx(expected_odds_ratio, rel=0.08)
    # 平均法なら (pb + pp) / 2 程度にとどまるが、掛け算なので大きく上回る
    assert both > max(pb, pp)
    assert both > 1.5 * (pb + pp) / 2


# ---- 受け入れ条件5:確率の合計が常に 1 ----

def test_probabilities_sum_to_one(model, all_players):
    batters = [p for p in all_players if p.role == BATTER][:60]
    pitchers = [p for p in all_players if p.role == PITCHER][:60]
    defense = {pos: next(p for p in batters if p.position == pos) for pos in FIELDER_POSITIONS}
    for b, p in zip(batters, pitchers):
        pr = model.probabilities(b, p, defense)
        assert set(pr) == set(RESULTS)
        assert sum(pr.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(v >= 0 for v in pr.values())
        assert sum(model.stage1(b, p).values()) == pytest.approx(1.0, abs=1e-12)
        assert sum(model.batted_ball(b, p).values()) == pytest.approx(1.0, abs=1e-12)


def test_extreme_ratings_still_sum_to_one(model):
    for v in (-20, 0, 100, 140):
        b = average_player(BATTER, "B", {i: v for i in avg_batter().ratings})
        p = average_player(PITCHER, "P", {i: 100 - v for i in avg_pitcher().ratings})
        assert sum(probs(model, b, p).values()) == pytest.approx(1.0, abs=1e-9)


# ---- 受け入れ条件6:左右の補正 ----

def test_platoon_same_side_favors_pitcher(model):
    same = rates_from(probs(model, avg_batter(), avg_pitcher()))
    opposite = rates_from(probs(model, average_player(BATTER, "B", bats="L"), avg_pitcher()))
    assert same["K%"] > opposite["K%"]
    assert same["OPS"] < opposite["OPS"]


def test_switch_hitter_picks_favorable_side(model):
    switch = average_player(BATTER, "B", bats="S")
    for throws, expected in (("R", "L"), ("L", "R")):
        pitcher = average_player(PITCHER, "P", throws=throws)
        assert batter_side(switch, pitcher) == expected
        opposite = average_player(BATTER, "B", bats=expected)
        assert probs(model, switch, pitcher) == probs(model, opposite, pitcher)


def test_platoon_can_be_turned_off(neutral_model):
    same = probs(neutral_model, avg_batter(), avg_pitcher())
    opposite = probs(neutral_model, average_player(BATTER, "B", bats="L"), avg_pitcher())
    assert same == pytest.approx(opposite)


# ---- 受け入れ条件7:守備 ----

def test_better_defense_lowers_hits_and_errors(model):
    good = rates_from(probs(model, defense=average_defense({"range": 65, "fielding": 65})))
    bad = rates_from(probs(model, defense=average_defense({"range": 35, "fielding": 35})))
    assert good["BABIP"] < bad["BABIP"]
    assert good["失策率(インプレーあたり)"] < bad["失策率(インプレーあたり)"]


def test_pitcher_fielding_uses_fixed_values(model):
    """投手が担当の打球は、設定の固定値で計算する(投手の能力は使わない)。"""
    a = model.fielding("P", avg_pitcher(), average_defense())
    b = model.fielding("P", avg_pitcher(stuff=90, control=10), average_defense())
    assert a == b == model.config["pitcher_fielding"]


# ---- 受け入れ条件8:型・成長タイプ・潜在能力・名前・年齢を変えても、能力が同じなら同じ確率 ----

def test_hidden_info_and_names_are_not_used(model, all_players):
    batter = next(p for p in all_players if p.role == BATTER)
    pitcher = next(p for p in all_players if p.role == PITCHER)
    defense = average_defense()
    base = model.probabilities(batter, pitcher, defense)

    def disguise(player):
        other = copy.deepcopy(player)
        other.family_name, other.given_name, other.age = "別人", "太郎", other.age + 9
        other.hidden.archetype = "something_else"
        other.hidden.growth_type = "late" if player.hidden.growth_type != "late" else "early"
        other.hidden.potential = {k: v + 25 for k, v in player.hidden.potential.items()}
        other.hidden.ability_drift = {k: 3.0 for k in player.hidden.ability_drift}
        return other

    assert model.probabilities(disguise(batter), disguise(pitcher), defense) == base


def test_form_is_added_to_ratings(model):
    hot = avg_batter()
    hot.state.form = 10.0
    plus10 = average_player(BATTER, "B", {i: 60 for i in avg_batter().ratings})
    assert probs(model, hot) == pytest.approx(probs(model, plus10))


def test_form_weight_zero_ignores_form():
    data = default_pa_data()
    data["form_weight"] = 0
    m = OddsRatioModel(validate_pa_config(data))
    hot = avg_batter()
    hot.state.form = 10.0
    assert probs(m, hot) == probs(m, avg_batter())


# ---- 受け入れ条件9:校正の目標の範囲に入る(一軍相当どうし) ----

def test_first_team_matchups_are_within_targets(model, leagues, config):
    pool = FirstTeamPool(leagues[:3], config)
    rates = expected_rates(model, pool, 3000, seed=1)
    for key, (low, high) in TARGETS.items():
        assert low <= rates[key] <= high, f"{key}: {rates[key]:.4f}"


# ---- 受け入れ条件10:指名打者制(投手は打席に立たない) ----

def test_pitcher_cannot_bat(model):
    with pytest.raises(ValueError, match="指名打者制"):
        model.probabilities(avg_pitcher(), avg_pitcher(), average_defense())


def test_defense_must_be_eight_fielders(model):
    defense = average_defense()
    with pytest.raises(ValueError):
        model.probabilities(avg_batter(), avg_pitcher(), {**defense, "P": avg_pitcher()})
    del defense["CF"]
    with pytest.raises(ValueError):
        model.probabilities(avg_batter(), avg_pitcher(), defense)
    defense = average_defense()
    defense["SS"] = avg_pitcher()
    with pytest.raises(ValueError):
        model.probabilities(avg_batter(), avg_pitcher(), defense)


def test_pitchers_have_no_batting_attributes(all_players):
    for p in all_players:
        if p.role == PITCHER:
            assert p.bats is None
            assert "contact" not in p.ratings


# ---- 記録の中身 ----

def test_record_contents(model):
    rng = random.Random(3)
    state = BaseOutState(outs=1, first=True, third=True)
    b, p, d = avg_batter(), avg_pitcher(), average_defense()
    seen = set()
    for _ in range(500):
        pa = resolve_plate_appearance(b, p, d, state, rng, model)
        seen.add(pa.result)
        assert pa.batter_id == "B" and pa.pitcher_id == "P"
        assert pa.base_out == state and pa.pitch_type is None
        if pa.result in ("strikeout", "walk", "hit_by_pitch", "home_run"):
            assert pa.batted_ball is None and pa.fielder is None
        else:
            assert pa.batted_ball in ("ground", "line", "fly")
            assert pa.fielder in ("P",) + FIELDER_POSITIONS
            if pa.result.endswith("_out"):
                assert pa.result == f"{pa.batted_ball}_out"
                assert not pa.unfieldable
    assert {"strikeout", "single", "ground_out", "fly_out"} <= seen


def test_base_out_state_has_24_states():
    states = [BaseOutState.from_index(i) for i in range(24)]
    assert [s.index for s in states] == list(range(24))
    with pytest.raises(ValueError):
        BaseOutState(outs=3)
    with pytest.raises(ValueError):
        BaseOutState.from_index(24)
