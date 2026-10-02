"""リーグ生成の受け入れ条件(1〜9)の確認。

統計の確認は「許容範囲」を設けている。シードを固定しているので結果は毎回同じだが、
設定値を少し変えても落ちにくいよう、ゆとりを持たせた範囲にしている。
"""

import statistics
from collections import Counter

import pytest

from pennant import generate_draft_class, generate_league
from pennant.abilities import BATTER, PITCHER, STYLE_ITEMS, items_for, strength_items_for
from pennant.config import validate_generation_config, default_generation_data
from pennant.stats import AGE_BINS, calibration_suggestion, overall, potential_overall


# ---- 受け入れ条件1:同じシードなら同じ結果、違うシードなら違う結果 ----

def test_same_seed_gives_same_league(config, names):
    a = generate_league(42, config, names)
    b = generate_league(42, config, names)
    assert a.to_dict() == b.to_dict()


def test_different_seed_gives_different_league(config, names):
    a = generate_league(42, config, names)
    b = generate_league(43, config, names)
    assert a.to_dict() != b.to_dict()


def test_same_seed_gives_same_draft_class(config, names):
    a = generate_draft_class(5, 50, config, names)
    b = generate_draft_class(5, 50, config, names)
    c = generate_draft_class(6, 50, config, names)
    assert [p.to_dict() for p in a] == [p.to_dict() for p in b]
    assert [p.to_dict() for p in a] != [p.to_dict() for p in c]


# ---- 受け入れ条件2:12球団(各リーグ6球団)、各70人、内訳が設定値どおり ----

def test_league_structure(league, config):
    assert len(league.teams) == 12
    assert Counter(t.league_index for t in league.teams) == {0: 6, 1: 6}
    assert len(league.league_names) == 2
    expected = {**config["roster"]["pitchers"], **config["roster"]["fielders"]}
    for team in league.teams:
        assert len(team.players) == 70
        assert Counter(p.position for p in team.players) == expected
        assert all(p.team_id == team.id for p in team.players)
    assert len({t.name for t in league.teams}) == 12
    assert len({t.place for t in league.teams}) == 12
    assert len({p.id for p in league.all_players()}) == 840


def test_ratings_have_the_right_items(league):
    for p in league.all_players():
        assert set(p.ratings) == set(items_for(p.role))
        assert set(p.hidden.potential) == set(items_for(p.role))
        assert p.role == (PITCHER if p.position in ("SP", "RP") else BATTER)


def test_hidden_information_is_separated(league):
    p = league.all_players()[0]
    data = p.to_dict()
    # 潜在能力・成長タイプ・生成時の型・能力の揺れは hidden の中だけにある
    assert set(data["hidden"]) == {"potential", "growth_type", "archetype", "ability_drift"}
    for key in ("potential", "growth_type", "archetype", "ability_drift"):
        assert key not in data


# ---- 受け入れ条件3:年齢が 18〜42 歳に収まり、平均・標準偏差が設定値に近い ----

def test_initial_ages(all_players, config):
    ages = [p.age for p in all_players]
    cfg = config["initial_ages"]
    assert min(ages) >= cfg["min"] and max(ages) <= cfg["max"]
    # 範囲の外を取り直すので、平均はわずかに上がり、標準偏差はわずかに小さくなる
    assert statistics.fmean(ages) == pytest.approx(cfg["mean"], abs=0.6)
    assert statistics.stdev(ages) == pytest.approx(cfg["sd"], abs=0.6)


def test_draft_ages(draft_class, config):
    origins = config["draft"]["origins"]
    for p in draft_class:
        o = origins[p.origin]
        assert o["age_min"] <= p.age <= o["age_max"]
    shares = Counter(p.origin for p in draft_class)
    for key, o in origins.items():
        assert shares[key] / len(draft_class) == pytest.approx(o["share"], abs=0.03)


# ---- 受け入れ条件4:型の出現割合が、多数回の生成で設定値に近づく ----

def test_batter_archetype_shares(all_players, config):
    batters = [p for p in all_players if p.role == BATTER]
    counts = Counter(p.hidden.archetype for p in batters)
    for key, entry in config["batter_archetypes"].items():
        assert counts[key] / len(batters) == pytest.approx(entry["share"], abs=0.025)


def test_pitcher_quality_and_role_shares(all_players, config):
    pitchers = [p for p in all_players if p.role == PITCHER]
    qualities = Counter(p.hidden.archetype.split("/")[1] for p in pitchers)
    for key, entry in config["pitcher_qualities"].items():
        assert qualities[key] / len(pitchers) == pytest.approx(entry["share"], abs=0.025)
    # 役割は球団の構成(先発・救援の人数)と一致する
    for p in pitchers:
        assert p.hidden.archetype.split("/")[0] == p.position


def test_growth_type_shares(all_players, config):
    counts = Counter(p.hidden.growth_type for p in all_players)
    for key, entry in config["aging"]["growth_types"].items():
        assert counts[key] / len(all_players) == pytest.approx(entry["share"], abs=0.02)


# ---- 受け入れ条件5:型の補正を入れても、潜在能力の平均が基準値から大きくずれない ----

def _potential_means_by_item(players, role):
    return {
        item: statistics.fmean(p.hidden.potential[item] for p in players if p.role == role)
        for item in items_for(role)
    }


@pytest.mark.parametrize("role,base_key", [(BATTER, "batter_base_mean"), (PITCHER, "pitcher_base_mean")])
def test_potential_mean_stays_at_base_for_rookies(draft_class, config, role, base_key):
    means = _potential_means_by_item(draft_class, role)
    for item, m in means.items():
        expected = config["potential"]["style_mean"] if item in STYLE_ITEMS else config["potential"][base_key]
        assert m == pytest.approx(expected, abs=1.0), item


@pytest.mark.parametrize("role,base_key", [(BATTER, "batter_base_mean"), (PITCHER, "pitcher_base_mean")])
def test_potential_mean_stays_at_base_without_survival_bias(names, role, base_key):
    data = default_generation_data()
    data["survival_bias"]["per_year"] = 0
    cfg = validate_generation_config(data)
    players = [p for seed in range(1, 6) for p in generate_league(seed, cfg, names).all_players()]
    for item, m in _potential_means_by_item(players, role).items():
        expected = cfg["potential"]["style_mean"] if item in STYLE_ITEMS else cfg["potential"][base_key]
        assert m == pytest.approx(expected, abs=1.0), item


# ---- 受け入れ条件6:初期選手は年齢が高い層ほど潜在能力が高い。新人ではその傾向がない ----

def test_survival_bias_in_initial_players(all_players):
    means = []
    for lo, hi in AGE_BINS[:4]:  # 38 歳以上は人数が少ないので除く
        group = [potential_overall(p) for p in all_players if lo <= p.age <= hi]
        means.append(statistics.fmean(group))
    assert means == sorted(means)
    assert means[-1] - means[0] > 3.0


def test_no_age_trend_in_rookies(draft_class):
    by_origin = {}
    for key in ("high_school", "college", "corporate"):
        by_origin[key] = statistics.fmean(potential_overall(p) for p in draft_class if p.origin == key)
    assert max(by_origin.values()) - min(by_origin.values()) < 1.0


# ---- 受け入れ条件7:若い層は現在の能力が潜在能力より低い。「固定」は年齢で変わらない ----

def test_young_players_are_below_potential(all_players):
    young = [p for p in all_players if p.age <= 22]
    gap = statistics.fmean(potential_overall(p) - overall(p) for p in young)
    assert gap > 5.0


def test_fixed_group_does_not_change_with_age(all_players, draft_class):
    for p in all_players + draft_class:
        for item in STYLE_ITEMS:
            assert p.ratings[item] == p.hidden.potential[item]
            assert p.hidden.ability_drift[item] == 0.0


def test_rookies_have_no_ability_drift(draft_class):
    for p in draft_class:
        assert all(v == 0.0 for v in p.hidden.ability_drift.values())


def test_initial_players_have_drift_and_form(all_players):
    older = [p for p in all_players if p.age >= 30]
    assert any(v != 0.0 for p in older for v in p.hidden.ability_drift.values())
    assert statistics.stdev(p.state.form for p in all_players) > 1.0


# ---- 受け入れ条件8:一軍相当の平均が 50 付近になるよう、設定値で調整できる ----

def test_first_team_mean_is_near_50(leagues, config):
    sugg = calibration_suggestion(leagues, config)
    for role in (BATTER, PITCHER):
        assert sugg[role]["first_team_mean"] == pytest.approx(50.0, abs=1.0)


def test_calibration_suggestion_is_exact(names):
    """基準値を +3 すると、一軍相当の平均もちょうど +3 動く(調整案の計算が正しいことの確認)。"""
    data = default_generation_data()
    base = validate_generation_config(data)
    data["potential"]["batter_base_mean"] += 3
    data["potential"]["pitcher_base_mean"] += 3
    shifted = validate_generation_config(data)
    a = calibration_suggestion([generate_league(9, base, names)], base)
    b = calibration_suggestion([generate_league(9, shifted, names)], shifted)
    for role in (BATTER, PITCHER):
        assert b[role]["first_team_mean"] - a[role]["first_team_mean"] == pytest.approx(3.0, abs=1e-9)
        assert b[role]["suggested_base"] == pytest.approx(a[role]["suggested_base"], abs=1e-9)


# ---- 受け入れ条件9:同じ球団・同じリーグで名前が重複しない ----

def test_names_are_unique_in_league(leagues):
    for lg in leagues:
        names = [(p.family_name, p.given_name) for p in lg.all_players()]
        assert len(names) == len(set(names))


def test_draft_names_do_not_clash_with_existing_league(league, config, names):
    existing = {(p.family_name, p.given_name) for p in league.all_players()}
    rookies = generate_draft_class(3, 200, config, names, existing=league)
    rookie_names = [(p.family_name, p.given_name) for p in rookies]
    assert len(rookie_names) == len(set(rookie_names))
    assert not existing & set(rookie_names)


# ---- その他:左右 ----

def test_handedness(all_players, config):
    """投手は投げ手だけ、打者は打席の左右だけを持つ(D-046、D-048)。"""
    hand = config["handedness"]
    for p in all_players:
        if p.role == PITCHER:
            assert p.throws in ("R", "L") and p.bats is None
        else:
            assert p.bats in ("R", "L", "S") and p.throws is None
    pitchers = [p for p in all_players if p.role == PITCHER]
    lefty = sum(p.throws == "L" for p in pitchers) / len(pitchers)
    assert lefty == pytest.approx(hand["pitcher_throws_left"], abs=0.03)
    batters = [p for p in all_players if p.role == BATTER]
    bats = Counter(p.bats for p in batters)
    for key, share in hand["bats"].items():
        assert bats[key] / len(batters) == pytest.approx(share, abs=0.02)


def test_strength_items_exclude_style():
    for role in (BATTER, PITCHER):
        assert not set(strength_items_for(role)) & set(STYLE_ITEMS)
