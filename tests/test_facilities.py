"""設備(D-383):設定・効きの倍率・維持費と年俸に使える額・新規開始の段階・AI の段階・能力の更新への効き・
契約の段階での変更・保存(版 22。D-385)。"""

import copy
import random

import pytest

from pennant import api, facilities as facmod
from pennant.config import ConfigError, load_generation_config
from pennant.generate import generate_league
from pennant.offseason import age_and_update
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

FS = facmod.load_facility_settings()


@pytest.fixture(scope="module")
def season_end():
    g = api.Game.new(5, ["X"] + [None] * 11, 0, season_seed=9, baselines="default")
    g.advance_to_end()
    g.year_end()
    return g


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def test_settings_effects_and_upkeep():
    assert FS.kinds == ("practice", "medical", "development") and FS.standard == 3 and FS.levels == 5
    assert [FS.upkeep(lv) for lv in range(1, 6)] == [0, 5000, 15000, 35000, 70000]
    assert FS.effect("practice", 3) == 1.0 and FS.effect("practice", 5) == pytest.approx(1.3) and FS.effect("practice", 1) == pytest.approx(0.7)
    assert FS.effect("medical", 5) == pytest.approx(0.7) and FS.effect("medical", 1) == pytest.approx(1.3)  # 下がり方に掛ける
    assert FS.effect("development", 5) == pytest.approx(1.5) and FS.effect("development", 1) == pytest.approx(0.5)
    assert FS.initial("large") == 3 and FS.initial("medium") == 2 and FS.initial("small") == 2
    assert FS.ai_level(480000) == 3 and FS.ai_level(400000) == 2 and FS.ai_level(320000) == 2 and FS.ai_level(600000) == 5 and FS.ai_level(100000) == 1
    data = copy.deepcopy(FS.data)
    data["upkeep"] = [0, 1, 2]
    with pytest.raises(ConfigError, match="upkeep"):
        facmod.validate_facility_settings(data)


def test_growth_and_decline_factors():
    config = load_generation_config()
    league = generate_league(3, with_foreign=False)
    young = next(p for p in league.all_players() if p.age <= 21)
    old = next(p for p in league.all_players() if p.age >= 34)
    for p, kind in ((young, "growth"), (old, "decline")):
        base = copy.deepcopy(p)
        a = age_and_update(copy.deepcopy(base), config, random.Random(1))
        b = age_and_update(copy.deepcopy(base), config, random.Random(1), 1.3, 0.7)
        if kind == "growth":
            assert sum(b.values()) > sum(a.values())  # 練習設備で伸びが大きい
        else:
            assert sum(b.values()) > sum(a.values())  # トレーナーで下がり方が小さい


def test_new_game_levels_and_cap():
    g = api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default")
    state = g.state
    for t in state.league.teams:
        assert t.facilities == {k: FS.initial(state.budget_tiers[t.id]) for k in FS.kinds}
        info = g.budget_info(t.id)
        assert info["upkeep"] == facmod.team_upkeep(t) and info["cap"] == state.team_funds[t.id] - info["upkeep"] - info["staff"]
        assert facmod.team_upkeep(t) + sum(int(p.contract["salary"]) for p in t.players + t.development) <= state.team_funds[t.id]  # 開始時は維持費を引いた額の中
    view = g.team("T01")
    assert [x["level"] for x in view["facilities"]] == [state.league.teams[0].facilities[k] for k in FS.kinds]


def test_set_facility_in_contract_stage(season_end):
    g = fresh(season_end)
    v = g.facilities_view()
    assert v["editable"] and v["mine"]["team_id"] == "T01" and len(v["teams"]) == 12
    cap0 = v["mine"]["budget"]["cap"]
    v = g.set_facility("practice", 5)
    lv = v["mine"]["levels"]
    assert lv["practice"] == 5 and v["mine"]["budget"]["cap"] == g.state.team_funds["T01"] - facmod.team_upkeep(g._team("T01")) - v["mine"]["budget"]["staff"]
    assert v["mine"]["budget"]["cap"] < cap0
    with pytest.raises(ValueError, match="段階"):
        g.set_facility("practice", 6)
    with pytest.raises(ValueError, match="知らない"):
        g.set_facility("stadium", 3)
    # 保存して読み込んでも同じ(版 22)
    again = load_game(save_game(g.state))
    assert SAVE_FORMAT_VERSION == 33 and next(t for t in again.league.teams if t.id == "T01").facilities["practice"] == 5
    # 契約の段階を過ぎると変えられない
    while g.offseason_view()["stage"] == "contract":
        g.offseason_stage_auto()
    with pytest.raises(ValueError, match="契約の段階"):
        g.set_facility("practice", 1)


def test_old_save_gets_initial_levels(season_end):
    g = fresh(season_end)
    for t in g.state.league.teams:
        t.facilities = {}
    loaded = load_game(save_game(g.state))
    assert all(t.facilities == {k: FS.initial(loaded.budget_tiers[t.id]) for k in FS.kinds} for t in loaded.league.teams)


def test_ai_decides_by_funds(season_end):
    g = fresh(season_end)
    state = g.state
    for t in state.league.teams:
        if t.id != "T01":
            assert set(t.facilities.values()) == {FS.ai_level(state.team_funds[t.id])}  # 契約の段階の始めに、資金に応じて
