"""リーグ生成の受け入れ条件(1〜9)の確認。

統計の確認は「許容範囲」を設けている。シードを固定しているので結果は毎回同じだが、
設定値を少し変えても落ちにくいよう、ゆとりを持たせた範囲にしている。
"""

import statistics
from collections import Counter

import pytest

from pennant import aging, generate_draft_class, generate_league
from pennant.config import load_generation_config
from pennant.abilities import BATTER, FIELDER_POSITIONS, PITCHER, abilities_for, items_for
from pennant.config import archetype_shares, validate_generation_config, default_generation_data
from pennant.stats import AGE_BINS, calibration_suggestion, overall, projected_overall


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


def test_abilities_and_ratings_have_the_right_items(league, config):
    from pennant.ability_model import engine_ratings

    for p in league.all_players():
        assert set(p.abilities) == set(abilities_for(p.role))  # 見せる能力(正本。D-349)
        assert set(p.ratings) == set(items_for(p.role)) and p.ratings == engine_ratings(config, p)  # 試合の計算の能力は換算
        assert p.role == (PITCHER if p.position in ("SP", "RP") else BATTER)
        assert (p.pitches is not None and 2 <= len(p.pitches) <= 4 and len({x["type"] for x in p.pitches}) == len(p.pitches)) == (p.role == PITCHER)


def test_hidden_information_is_separated(league):
    p = league.all_players()[0]
    data = p.to_dict()
    # 成長タイプ・生成時の型・伸びやすさ・弾道の傾きは hidden の中だけにある。潜在能力は持たない(D-350)
    assert set(data["hidden"]) == {"growth_type", "archetype", "ease", "trajectory_bias"}
    for key in ("potential", "growth_type", "archetype", "ease", "trajectory_bias"):
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
    """全体では、ポジション別の足す量が人数で打ち消し合い、設定値(各 20%)どおり(D-157)。"""
    batters = [p for p in all_players if p.role == BATTER]
    counts = Counter(p.hidden.archetype for p in batters)
    for key, entry in config["batter_archetypes"].items():
        assert counts[key] / len(batters) == pytest.approx(entry["share"], abs=0.025)


def test_batter_archetype_shares_by_position(all_players, config):
    """ポジションごとの型の割合が、全体の割合 + 足す量 どおり(第3弾①。受け入れ条件1)。"""
    batters = [p for p in all_players if p.role == BATTER]
    for pos in FIELDER_POSITIONS:
        group = [p for p in batters if p.position == pos]
        counts = Counter(p.hidden.archetype for p in group)
        expected = archetype_shares(config, pos)
        assert sum(expected.values()) == pytest.approx(1.0)
        for key, share in expected.items():
            assert counts[key] / len(group) == pytest.approx(share, abs=0.05), (pos, key)  # 1ポジション 360〜1080 人(標準誤差 約 0.02)
    # 狙いの方向:捕手・遊撃・二塁・中堅は守備型・俊足型が多め、一塁・三塁・両翼は長距離砲が多め
    for pos in ("C", "SS", "2B", "CF"):
        s = archetype_shares(config, pos)
        assert s["defensive"] + s["speedster"] > 0.45 and s["power_hitter"] < 0.15
    for pos in ("1B", "3B", "LF", "RF"):
        s = archetype_shares(config, pos)
        assert s["power_hitter"] > 0.3 and s["defensive"] < 0.1


CONFIG_FOR_AGING = load_generation_config()


def test_position_ability_means_follow_the_shares(all_players):
    """ポジション別の能力の平均(今の能力には年ごとの揺れも入るので、差の下限は 0.5)が、狙いの方向(遊撃・捕手・二塁・中堅は守備力・走力が高め、一塁・三塁・両翼はパワーが高め。受け入れ条件3)。"""
    batters = [p for p in all_players if p.role == BATTER]

    def mean(pos, item):  # 年齢の差を除くため、ピークの見込みで比べる
        return statistics.fmean(p.abilities[item] + aging.expected_gain(CONFIG_FOR_AGING, item, p.age, p.hidden.growth_type) for p in batters if p.position == pos)

    for defensive_pos in ("C", "SS", "2B", "CF"):
        for corner in ("1B", "3B", "LF", "RF"):
            assert mean(defensive_pos, "defense") > mean(corner, "defense") + 0.5, (defensive_pos, corner)
            assert mean(defensive_pos, "speed") > mean(corner, "speed") + 0.5, (defensive_pos, corner)
            assert mean(corner, "power") > mean(defensive_pos, "power") + 0.5, (defensive_pos, corner)


def test_pitcher_quality_and_role_shares(all_players, config):
    pitchers = [p for p in all_players if p.role == PITCHER]
    qualities = Counter(p.hidden.archetype.split("/")[1] for p in pitchers)
    for key, entry in config["pitcher_qualities"].items():
        assert qualities[key] / len(pitchers) == pytest.approx(entry["share"], abs=0.025)
    # 役割は球団の構成(先発・救援の人数)と一致する
    for p in pitchers:
        assert p.hidden.archetype.split("/")[0] == p.position


def test_growth_type_and_ease(all_players, config):
    counts = Counter(p.hidden.growth_type for p in all_players)
    for key, entry in config["aging"]["growth_types"].items():
        assert counts[key] / len(all_players) == pytest.approx(entry["share"], abs=0.02)
    g = config["abilities"]["growth"]
    eases = [p.hidden.ease for p in all_players]
    assert statistics.fmean(eases) == pytest.approx(g["ease_mean"], abs=0.02) and all(g["ease_min"] <= e <= g["ease_max"] for e in eases)


# ---- 受け入れ条件5:型の補正を入れても、ピークの見込みの平均が基準値から大きくずれない(D-350) ----

def _projected_means_by_item(players, role, config):
    from pennant import aging

    return {
        item: statistics.fmean(p.abilities[item] + aging.expected_gain(config, item, p.age, p.hidden.growth_type) for p in players if p.role == role)
        for item in abilities_for(role)
    }


@pytest.mark.parametrize("role,base_key", [(BATTER, "batter_base_mean"), (PITCHER, "pitcher_base_mean")])
def test_peak_mean_stays_at_base_for_rookies(draft_class, config, role, base_key):
    for item, m in _projected_means_by_item(draft_class, role, config).items():
        assert m == pytest.approx(config["potential"][base_key], abs=1.0), item


# ---- 受け入れ条件6:初期選手は年齢が高い層ほど能力が高い(生き残りバイアス)。新人ではその傾向がない ----

def test_survival_bias_in_initial_players(all_players):
    means = []
    for lo, hi in AGE_BINS[:4]:  # 38 歳以上は人数が少ないので除く
        group = [projected_overall(p) for p in all_players if lo <= p.age <= hi]
        means.append(statistics.fmean(group))
    assert means == sorted(means)
    assert means[-1] - means[0] > 3.0


def test_no_age_trend_in_rookies(draft_class):
    by_origin = {}
    for key in ("high_school", "college", "corporate"):
        by_origin[key] = statistics.fmean(projected_overall(p) for p in draft_class if p.origin == key)
    assert max(by_origin.values()) - min(by_origin.values()) < 1.5


# ---- 受け入れ条件7:若い層は今の能力がピークの見込みより低い ----

def test_young_players_are_below_their_peak(all_players):
    young = [p for p in all_players if p.age <= 22]
    gap = statistics.fmean(projected_overall(p) - overall(p) for p in young)
    assert gap > 5.0


def test_initial_players_have_form(all_players):
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
