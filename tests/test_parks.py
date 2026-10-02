"""球場の倍率(第2弾②a。D-136〜D-138)の確認。"""

import json
import random

import pytest

from pennant import answers, api
from pennant.config import ConfigError, load_generation_config, validate_generation_config
from pennant.game import simulate_game
from pennant.game_config import load_game_config
from pennant.manager import SimpleManager
from pennant.models import NEUTRAL_PARK, ParkFactors
from pennant.newgame import new_league
from pennant.parks import assign_parks, draw_balanced, neutralize_parks, park_ranges, park_seed
from pennant.pa_stats import average_defense, average_player
from pennant.plate_appearance import default_model
from pennant.savegame import SaveDataError, load_game, save_game

CONFIG = load_generation_config()


def _by_league(league):
    groups = {}
    for t in league.teams:
        groups.setdefault(t.league_index, []).append(t)
    return groups


# ---- 受け入れ条件1:平均 1.0、範囲内、同じシードなら同じ ----

@pytest.mark.parametrize("seed", [1, 2, 3, 12345])
def test_league_means_are_exactly_one_and_within_range(seed):
    league = new_league(seed)
    ranges = park_ranges(CONFIG)
    assert ranges == {"home_run": (850, 1150), "babip": (970, 1030)}
    for teams in _by_league(league).values():
        assert sum(t.park.home_run for t in teams) == 1000 * len(teams)
        assert sum(t.park.babip for t in teams) == 1000 * len(teams)
        for t in teams:
            assert 850 <= t.park.home_run <= 1150 and 970 <= t.park.babip <= 1030
            assert isinstance(t.park.home_run, int) and isinstance(t.park.babip, int)


def test_same_seed_same_parks_and_other_rng_untouched():
    a, b = new_league(1), new_league(1)
    assert [t.park for t in a.teams] == [t.park for t in b.teams]
    assert [t.park for t in a.teams] != [t.park for t in new_league(2).teams]
    assert park_seed(1) != park_seed(2) and park_seed(1) != 1
    # 選手・球団の生成は、倍率の乱数とは別(指紋 (a) が変わらない)
    expected = json.loads(open("tests/data/fingerprints-parks-off.json", encoding="utf-8").read())
    from pennant.fingerprint import _digest, league_record

    assert _digest(league_record(a)) == expected["league"]


def test_draw_balanced_is_deterministic_and_balanced():
    values = draw_balanced(random.Random(5), 6, 850, 1150)
    assert values == draw_balanced(random.Random(5), 6, 850, 1150) and sum(values) == 6000
    assert all(850 <= v <= 1150 for v in values) and len(set(values)) > 1
    with pytest.raises(ValueError):
        draw_balanced(random.Random(1), 6, 1100, 1200)  # 1000 が範囲にない


def test_parks_config_validation():
    import copy

    data = copy.deepcopy(CONFIG.data)
    data["parks"]["home_run_range"] = [1.2, 0.9]
    with pytest.raises(ConfigError, match="parks.home_run_range"):
        validate_generation_config(data)
    data = copy.deepcopy(CONFIG.data)
    data["parks"]["babip_range"] = [0.9]
    with pytest.raises(ConfigError, match="parks.babip_range"):
        validate_generation_config(data)
    data = copy.deepcopy(CONFIG.data)
    data.pop("parks")  # 設定がなければ倍率なし(すべて 1.0)
    cfg = validate_generation_config(data)
    assert park_ranges(cfg) == {"home_run": (1000, 1000), "babip": (1000, 1000)}
    league = new_league(1)
    assign_parks(league, cfg)
    assert all(t.park == NEUTRAL_PARK for t in league.teams)


# ---- 受け入れ条件2:倍率 1.0 なら、これまでと完全に同じ ----

def test_neutral_park_gives_identical_probabilities_and_games():
    model = default_model()
    batter, pitcher, defense = average_player("batter"), average_player("pitcher"), average_defense()
    base = model.probabilities(batter, pitcher, defense)
    assert model.probabilities(batter, pitcher, defense, park=NEUTRAL_PARK) == base
    assert model.probabilities(batter, pitcher, defense, park=ParkFactors(1000, 1000)) == base
    league = new_league(1)
    cfg = load_game_config()
    mgr = SimpleManager(cfg)

    def play(park):
        rng = random.Random(7)
        h, _ = mgr.prepare(league.teams[0], rng, 0)
        a, _ = mgr.prepare(league.teams[1], rng, 0)
        return simulate_game(h, a, rng, config=cfg, manager=mgr, park=park)

    assert play(None).log == play(NEUTRAL_PARK).log
    assert play(ParkFactors(1500, 1100)).log != play(None).log


def test_park_changes_home_run_and_babip_in_the_expected_direction():
    model = default_model()
    batter, pitcher, defense = average_player("batter"), average_player("pitcher"), average_defense()
    base = model.probabilities(batter, pitcher, defense)
    high = model.probabilities(batter, pitcher, defense, park=ParkFactors(1150, 1030))
    low = model.probabilities(batter, pitcher, defense, park=ParkFactors(850, 970))
    assert high["home_run"] > base["home_run"] > low["home_run"]
    hits = lambda p: p["single"] + p["double"] + p["triple"]
    assert hits(high) > hits(base) > hits(low)
    # 三振・四球・失策は変わらない(本塁打の増減でわずかに動くインプレーの割合の分だけ)
    assert abs(high["strikeout"] - base["strikeout"]) < 0.002 and abs(high["walk"] - base["walk"]) < 0.002
    # ホームの有利は、これまでどおり home=True のときだけ(球場の倍率とは別)
    assert model.probabilities(batter, pitcher, defense, home=True)["home_run"] > base["home_run"]
    assert model.probabilities(batter, pitcher, defense, home=True, park=ParkFactors(850, 1000))["home_run"] < model.probabilities(batter, pitcher, defense, home=True)["home_run"]


# ---- 受け入れ条件6:セーブデータ ----

def test_parks_are_saved_and_old_saves_read_as_neutral():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    g.advance(2)
    data = save_game(g.state)
    loaded = load_game(data)
    assert [t.park for t in loaded.league.teams] == [t.park for t in g.state.league.teams]
    # 版3(球場の倍率がない)→ すべて 1.0 として読む
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    state = json.loads(files["state.json"])
    for td in state["league"]["teams"]:
        td.pop("park")
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 3
    files["state.json"] = json.dumps(state).encode()
    files["manifest.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    old = load_game(out.getvalue())
    assert all(t.park == NEUTRAL_PARK for t in old.league.teams)
    # 旧版の続きは、倍率なしの計算と同じ
    neutral = new_league(1)
    neutralize_parks(neutral)
    from pennant.season import Season

    ref = Season(neutral, 13)
    ref.play_days(3)
    old.season.play_days(1)
    # 保存までの試合(倍率あり)は変わらないが、読み込んだあとに進めた日(3日目)は、倍率なしの計算と同じになる
    assert [p.result.log == q.result.log for p, q in zip(old.season.played[12:], ref.played[12:])] == [True] * 6
    # 壊れた倍率は、分かりやすいエラー
    state["league"]["teams"][0]["park"] = {"home_run": 5, "babip": 1000}
    manifest["format_version"] = 4
    files["state.json"] = json.dumps(state).encode()
    files["manifest.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    with pytest.raises(SaveDataError, match="球場の倍率が範囲外"):
        load_game(out.getvalue())


# ---- 受け入れ条件8:公開用には倍率を含めない。答え合わせ用で見せる ----

def test_public_stadium_has_no_factors_and_answers_show_them():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    g.advance(4)
    d = g.stadium("T02")
    assert d["name"] == g.state.league.teams[1].stadium and d["games"] > 0
    assert "park" not in json.dumps(d) and "倍率" not in json.dumps(d["name"]) and "home_run_multiplier" not in json.dumps(d)
    assert float(d["home_runs_per_game"]) >= 0 and d["home_runs"] == sum(
        1 for p in g.state.season.played if p.result.home_team_id == "T02" for x in p.result.log if x.pa.result == "home_run"
    )
    for value in (g.teams(), g.team("T02"), g.game(0), g.last_day_games(), g.status()):
        assert "park" not in json.dumps(value)
    assert g.game(0)["summary"]["stadium"] == g.state.league.teams[[t.id for t in g.state.league.teams].index(g.game(0)["summary"]["home"]["team_id"])].stadium
    a = answers.stadium_answers(g, "T02", 1)
    park = g.state.league.teams[1].park
    assert a["home_run"]["value"] == park.home_run and a["home_run"]["text"] == f"{park.home_run / 1000:.3f}"
    with pytest.raises(ValueError):
        answers.stadium_answers(g, "T02", 0)
