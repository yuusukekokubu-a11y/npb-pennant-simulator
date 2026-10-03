"""指標の基準値と第2弾の指標(wOBA・wRC+・OPS+・FIP)の確認(第2弾①。D-120〜D-127)。"""

import json
from collections import Counter
from fractions import Fraction

import pytest

from pennant import api
from pennant.baselines import (
    END,
    STATES,
    VALUE_NAMES,
    Baselines,
    RunTally,
    blend,
    blend_weight,
    blended_for_results,
    compute_baselines,
    league_totals,
    load_baseline_settings,
    state_label,
    trial_baselines,
    trial_seed,
)
from pennant.config import ConfigError
from pennant.metrics import compute, format_value
from pennant.newgame import new_league
from pennant.records import season_records
from pennant.savegame import load_game, save_game

SETTINGS = load_baseline_settings()


@pytest.fixture(scope="module")
def game():
    """試運転つきの新規開始(リーグ 1・シーズン 13。指紋 (d) と同じ)で、1シーズンを最後まで。"""
    g = api.Game.new(1, [None] * 12, 0, season_seed=13)
    g.advance(125)
    return g


@pytest.fixture(scope="module")
def trial(game):
    return game.state.baselines


# ---- 受け入れ条件1:試運転は、実際のリーグに影響しない。同じシードなら同じ基準値 ----

def test_trial_does_not_touch_league_and_is_reproducible(trial):
    league = new_league(1)
    before = json.dumps(league.to_dict(), sort_keys=True)
    days = []
    again = trial_baselines(league, SETTINGS, lambda d, n: days.append((d, n)))
    assert json.dumps(league.to_dict(), sort_keys=True) == before  # 疲労などの状態も変わらない
    assert again.to_dict() == trial.to_dict() and again.source == "trial"
    assert days[0] == (1, 125) and days[-1] == (125, 125)  # 進み具合を知らせる
    assert trial_seed(1) == trial_seed(1) != trial_seed(2) and trial_seed(1) != 1


def test_trial_does_not_change_the_season(game):
    """試運転があってもなくても、実際のシーズンの結果は同じ(指紋 (d))。"""
    from pennant.fingerprint import _digest, season_record

    expected = json.loads(open("tests/data/fingerprints.json", encoding="utf-8").read())
    assert _digest(season_record(game.state.season.result())) == expected["season"]


# ---- 受け入れ条件2・3:基準値の性質 ----

def test_woba_weights_are_ordered(trial):
    v = trial.values
    assert v["w_hr"] > v["w_3b"] > v["w_2b"] > v["w_1b"] > v["w_bb"] > 0
    lw = trial.linear_weights
    assert lw["OUT"] < 0 < lw["BB"] and lw["HR"] > 1


def test_re24_table(trial):
    re = trial.re24
    assert len(re) == STATES and all(isinstance(x, Fraction) and x >= 0 for x in re)
    for outs in range(3):
        row = re[outs * 8 : outs * 8 + 8]
        assert row[7] == max(row) and row[0] == min(row), f"{outs}アウト"  # 満塁が最大、走者なしが最小
    assert re[0] > re[8] > re[16]  # アウトが増えるほど小さい
    assert state_label(0) == "無死走者なし" and state_label(23) == "二死満塁"


def test_league_woba_equals_obp_and_fip_equals_era(game, trial):
    """その基準値を求めたシーズンでは、リーグ全体の wOBA = 出塁率、FIP = 防御率(ちょうど一致)。"""
    config = api.metrics_config()
    rec = season_records(p.result for p in game.state.season.played)
    current = compute_baselines(
        game.records.tally, league_totals(rec, "batter"), league_totals(rec, "pitcher"), SETTINGS, trial
    )
    bat = compute(config, "batter", league_totals(rec, "batter"), current.values)
    pit = compute(config, "pitcher", league_totals(rec, "pitcher"), current.values)
    assert bat["woba"] == bat["obp"] == current.values["lg_obp"]
    assert pit["fip"] == pit["era"] == current.values["lg_era"]
    # 試運転の値でも同じ(試運転のシーズンの中で)
    assert trial.values["lg_woba"] == trial.values["lg_obp"]


def test_plus_metrics_average_about_100(game):
    """wRC+・OPS+ の、打席数で重みづけした平均が約100(シーズンの最後に使う、混ぜた基準値で)。"""
    config = api.metrics_config()
    base, _ = game.baselines()
    rec = game.records.total
    for key in ("wrc_plus", "ops_plus"):
        num = den = Fraction(0)
        for counts in rec.batters.values():
            v = compute(config, "batter", counts, base.values)[key]
            if v is not None:
                num += v * counts["PA"]
                den += counts["PA"]
        avg = num / den
        assert 97 < avg < 103, (key, float(avg))
    # 今シーズンの値だけを使えば、wRC+ の平均はちょうど100
    current = compute_baselines(game.records.tally, league_totals(rec, "batter"), league_totals(rec, "pitcher"), SETTINGS, base)
    num = sum(compute(config, "batter", c, current.values)["wrc_plus"] * c["PA"] for c in rec.batters.values())
    assert num / sum(c["PA"] for c in rec.batters.values()) == 100


# ---- 受け入れ条件4:小さな手計算の例 ----

def test_linear_weights_by_hand():
    """2つの半イニングだけの集計で、RE24 と線形加重を手で計算した値と比べる。"""
    t = RunTally()
    # 無死走者なし(状況0)が2回:イニングの終わりまでの得点 1点と0点 → 期待値 1/2
    t.re_runs[0], t.re_count[0] = 1, 2
    # 無死一塁(状況1)が1回:終わりまで1点 → 期待値 1
    t.re_runs[1], t.re_count[1] = 1, 1
    # 一死走者なし(状況8)が2回:0点 → 期待値 0
    t.re_count[8] = 2
    t.moves["B1"][(0, 1)] = 1  # 単打:無死走者なし → 無死一塁
    t.moves["HR"][(1, 0)] = 1  # 本塁打(2点):無死一塁 → 無死走者なし
    t.move_runs["HR"][(1, 0)] = 2
    t.moves["OUT"][(0, 8)] = 2  # アウト:無死走者なし → 一死走者なし(2回)
    t.moves["OUT"][(8, END)] = 1  # 一死走者なし → イニング終了(手計算を簡単にするため)
    base = compute_baselines(t, {}, {}, SETTINGS)
    assert base.re24[0] == Fraction(1, 2) and base.re24[1] == 1 and base.re24[8] == 0
    assert base.linear_weights["B1"] == Fraction(1, 2)  # 1 − 1/2
    assert base.linear_weights["HR"] == Fraction(3, 2)  # 1/2 − 1 + 2
    assert base.linear_weights["OUT"] == Fraction(-1, 3)  # ((0 − 1/2) × 2 + (0 − 0)) ÷ 3


def test_metric_formulas_by_hand():
    config = api.metrics_config()
    b = {k: Fraction(0) for k in VALUE_NAMES}
    b.update(w_bb=Fraction(7, 10), w_hbp=Fraction(72, 100), w_1b=Fraction(9, 10), w_2b=Fraction(5, 4), w_3b=Fraction(8, 5), w_hr=Fraction(2),
             woba_scale=Fraction(6, 5), lg_woba=Fraction(32, 100), lg_obp=Fraction(32, 100), lg_slg=Fraction(2, 5), lg_r_pa=Fraction(1, 9),
             fip_hr=Fraction(13), fip_bb=Fraction(3), fip_so=Fraction(2), fip_constant=Fraction(31, 10), pf=Fraction(1))
    c = Counter(PA=10, AB=8, H=3, B1=1, B2=1, B3=0, HR=1, TB=7, BB=1, HBP=1, SF=0, SO=2)
    v = compute(config, "batter", c, b)
    woba = (Fraction(7, 10) + Fraction(72, 100) + Fraction(9, 10) + Fraction(5, 4) + 2) / 10
    assert v["woba"] == woba
    assert v["wrc_plus"] == ((woba - Fraction(32, 100)) / Fraction(6, 5) + Fraction(1, 9)) / Fraction(1, 9) * 100
    obp, slg = Fraction(5, 10), Fraction(7, 8)
    assert v["ops_plus"] == 100 * (obp / Fraction(32, 100) + slg / Fraction(2, 5) - 1)
    assert format_value(config, "wrc_plus", Fraction(2499, 10)) == "250" and format_value(config, "woba", woba) == ".557"
    p = compute(config, "pitcher", Counter(OUTS=27, HR=1, BB=2, HBP=1, SO=9), b)
    assert p["fip"] == (13 * 1 + 3 * 3 - 2 * 9) * Fraction(3, 27) + Fraction(31, 10)
    assert format_value(config, "fip", p["fip"]) == "3.54"
    assert compute(config, "batter", c)["woba"] is None  # 基準値がなければ値なし


# ---- 受け入れ条件5:混ぜ方 ----

def test_blend_weight_grows_to_about_75_percent(game, trial):
    assert blend_weight(0, 20000) == 0 and blend_weight(20000, 20000) == Fraction(1, 2)
    fresh = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    first, w0 = fresh.baselines()
    assert w0 == 0 and first.values == fresh.state.baselines.values  # 初日は出発点の値のまま
    weights = []
    for _ in range(3):
        fresh.advance(10)
        weights.append(fresh.baselines()[1])
    assert weights == sorted(weights) and weights[0] > 0
    final, w = game.baselines()
    assert Fraction(74, 100) < w < Fraction(76, 100)
    rec = game.records.total
    current = compute_baselines(game.records.tally, league_totals(rec, "batter"), league_totals(rec, "pitcher"), SETTINGS, trial)
    for k in ("w_hr", "lg_obp", "fip_constant", "woba_scale"):  # 混ぜた値は、出発点と今シーズンの値の間(比重 w の位置)
        assert final.values[k] == w * current.values[k] + (1 - w) * trial.values[k]


def test_incremental_baselines_match_whole_season(game, trial):
    """画面の操作の関数(試合ごとに足していく)と、シーズン全体から求めた値が同じ。"""
    whole, w = blended_for_results(trial, [p.result for p in game.state.season.played], SETTINGS)
    mine, w2 = game.baselines()
    assert w == w2 and whole.values == mine.values and whole.re24 == mine.re24


def test_blend_uses_prior_when_current_is_missing(trial):
    current = Baselines({"w_hr": Fraction(3)})
    mixed, w = blend(trial, current, 20000, 20000)
    assert mixed.values["w_hr"] == (Fraction(3) + trial.values["w_hr"]) / 2
    assert mixed.values["lg_obp"] == trial.values["lg_obp"]


# ---- 受け入れ条件6:セーブデータ ----

def test_baselines_are_saved_and_loaded(game):
    loaded = load_game(save_game(game.state))
    assert loaded.baselines.to_dict() == game.state.baselines.to_dict()
    assert loaded.baselines.source == "trial"
    again = api.Game(loaded, dirty=False)
    assert again.baselines()[0].values == game.baselines()[0].values
    assert again.stats("batter", "saber") == game.stats("batter", "saber")


def test_default_baselines_and_settings():
    d = SETTINGS.default_baselines()
    assert set(d.values) == set(VALUE_NAMES) and d.source == "default"
    assert d.values["w_hr"] == Fraction(str(SETTINGS.data["defaults"]["w_hr"])) and SETTINGS.blend_constant == 20000  # 既定値は scripts/make_baselines.py で求め直す(小数の文字を分数にする)
    g = api.Game.new(2, [None] * 12, 0, baselines="default")
    assert g.state.baselines.source == "default"
    with pytest.raises(ValueError):
        api.Game.new(2, [None] * 12, 0, baselines="fast")


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.update(blend_constant_pa=0), "blend_constant_pa"),
        (lambda d: d.update(fip_coefficients="derived"), "fip_coefficients"),
        (lambda d: d["defaults"].pop("w_hr"), "defaults.w_hr"),
        (lambda d: d["defaults"].update(w_hr="abc"), "defaults.w_hr"),
    ],
)
def test_settings_validation(change, message):
    import copy

    from pennant.baselines import validate_baseline_settings

    data = copy.deepcopy(SETTINGS.data)
    change(data)
    with pytest.raises(ConfigError, match=message):
        validate_baseline_settings(data)


# ---- 画面:セイバーの表・注記・指標の解説 ----

def test_saber_table_and_notes(game):
    t = game.stats("batter", "saber")
    assert t["sort"]["key"] == "wrc_plus" and "前のシーズン" not in t["baseline_note"] and "試運転" in t["baseline_note"]
    assert "75%" in t["baseline_note"]
    cols = {c["key"]: c for c in t["columns"]}
    assert "1シーズン目は 1.0" in cols["wrc_plus"]["description"]
    assert "1シーズン目は 1.0" in cols["ops_plus"]["description"]
    assert all(r["values"]["woba"] != "-" for r in t["rows"])
    assert game.stats("batter", "basic")["baseline_note"] is None


def test_metrics_guide_matches_definitions():
    config = api.metrics_config()
    guide = api.metrics_guide()
    seen = [m for g in guide["groups"] for m in g["metrics"]]
    assert [m["key"] for m in seen] == config.in_category("basic") + config.in_category("saber")
    for m in seen:
        d = config.metrics[m["key"]]
        assert m["name"] == d["name"] and m["description"] == d["description"]
        assert m["notes"] == [d[k] for k in ("note", "better_note") if d.get(k)]
    woba = next(m for m in seen if m["key"] == "woba")
    assert woba["formulas"][0]["text"].startswith("(四球の重み × 四球 + ")
