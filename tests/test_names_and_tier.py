"""新規開始のリーグ名・球場名(D-409)と、自球団の資金力(D-410)。"""

import copy
from collections import Counter

import pytest

from pennant import api
from pennant.contracts import assign_tiers, base_funds, load_contract_settings, rate_funds
from pennant.newgame import TeamNameError
from pennant.savegame import SAVE_FORMAT_VERSION, SaveDataError, load_game, save_game

CS = load_contract_settings()
IDS = [f"T{i:02d}" for i in range(1, 13)]


@pytest.fixture(scope="module")
def huge_game():
    return api.Game.new(1, [None] * 12, 0, season_seed=1, baselines="default", league_names=["北の連盟", ""], stadium_names=["夢球場"] + [""] * 11, my_tier="huge")


def test_names_are_applied_and_saved(huge_game):
    g = huge_game
    assert g.state.league.league_names[0] == "北の連盟" and g.state.league.league_names[1] != ""
    assert g.state.league.teams[0].stadium == "夢球場" and g.state.league.teams[1].stadium
    again = load_game(save_game(g.state))
    assert again.league.league_names[0] == "北の連盟" and again.league.teams[0].stadium == "夢球場"
    assert SAVE_FORMAT_VERSION == 33 and again.budget_tiers["T01"] == "huge"
    bad = copy.deepcopy(g.state)
    bad.league.teams[2].stadium = " "
    with pytest.raises(SaveDataError, match="stadium"):
        load_game(save_game(bad))
    bad = copy.deepcopy(g.state)
    bad.league.league_names[1] = "a\u0007b"
    with pytest.raises(SaveDataError, match="league_names"):
        load_game(save_game(bad))


def test_place_name_problems():
    p = api.check_place_names(3, ["同じ", "同じ"], [""] * 11 + ["x" * 30])
    assert p["leagues"][0] is None and "同じ名前" in p["leagues"][1] and "長すぎ" in p["stadiums"][11]
    with pytest.raises(TeamNameError, match="リーグ名"):
        api.Game.new(3, [None] * 12, None, season_seed=3, baselines="default", league_names=["同じ", "同じ"])


def test_tiers_with_my_choice(huge_game):
    auto = assign_tiers(IDS, 1, CS)
    assert assign_tiers(IDS, 1, CS, None) == auto  # 自動は今までと同じ割り当て
    tiers = huge_game.state.budget_tiers
    assert tiers["T01"] == "huge" and Counter(tiers.values()) == Counter({"medium": 6, "small": 3, "large": 2, "huge": 1})  # 超大量は大として数える
    small = assign_tiers(IDS, 1, CS, {"T05": "small"})
    assert small["T05"] == "small" and Counter(small.values()) == Counter({"large": 3, "medium": 6, "small": 3})
    assert huge_game.state.team_funds["T01"] == base_funds("huge", CS) == 100 * base_funds("large", CS)
    with pytest.raises(ValueError, match="資金力"):
        api.Game.new(1, [None] * 12, 0, season_seed=1, baselines="default", my_tier="giant")


def test_huge_funds_do_not_raise_the_rate():
    big = base_funds("huge", CS)
    cap = rate_funds(big, "huge", CS)
    assert cap == int(base_funds("large", CS) * CS.funds_range[1]) and rate_funds(big, "large", CS) == big  # 単価には大の上限までだけ数える
