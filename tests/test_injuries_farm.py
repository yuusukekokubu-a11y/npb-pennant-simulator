"""けが(D-445)と二軍の試合(D-446)と出場と若手の成長(D-447)と保存(D-448)。"""

import copy
import random
from collections import Counter

import pytest

from pennant import ConfigError, api, generate_league
from pennant import injuries as injmod
from pennant.farm import PLAYING_ENOUGH, PLAYING_FACTORS, playing_growth
from pennant.records import Records
from pennant.rules import RULE_KEYS, rules_from
from pennant.savegame import load_game, save_game
from pennant.season import Season


@pytest.fixture(scope="module")
def league(config, names):
    return generate_league(31, config, names)


@pytest.fixture(scope="module")
def played(league):
    """二軍・けがありで 1 シーズン(レギュラーシーズン)。"""
    s = Season(copy.deepcopy(league), 7, farm=True, injuries=injmod.default_settings())
    s.play_to_end()
    return s


def test_injury_count_and_severity(played):
    s = played
    per_team = Counter(x["team_id"] for x in s.injury_log)
    mean = sum(per_team.values()) / len(s.league.teams)
    assert 6 <= mean <= 14  # 1 球団で 1 シーズン 10 件ほど(8〜12 を目安。D-445)
    sev = Counter(x["severity"] for x in s.injury_log)
    assert sev["light"] > sev["middle"] > sev["heavy"]
    assert {x["where"] for x in s.injury_log} == {"first", "farm"}
    for x in s.injury_log:
        if x["severity"] == "season":
            assert x["until"] == injmod.SEASON_OUT
        else:
            assert x["until"] == x["start"] + 1 + x["days"]


def test_injured_players_are_not_in_the_first_team(played):
    s = played
    for x in s.injury_log:
        for d in range(x["start"] + 1, min(x["until"], s.total_days)):
            a = s.active_on(x["team_id"], d)
            if x["player_id"] in {p.id for p in a.players}:
                pytest.fail(f"{x['player_id']} は {d} 日目にけがなのに一軍にいる")
    for tid, a in s.actives.items():
        assert len(a.players) == 29 and sum(1 for p in a.batters if p.position == "C") >= 2


def test_farm_games_and_records(played):
    s = played
    f = s.farm
    assert f.games == len(s.played)  # 人数が足りない日はない(既定のリーグ)
    w = sum(v[0] for v in f.standings.values())
    l = sum(v[1] for v in f.standings.values())
    t = sum(v[2] for v in f.standings.values())
    assert w == l and w + t // 2 == f.games
    assert f.records.batters and f.records.pitchers


def test_no_injuries_no_farm_gives_the_same_games(league):
    """けが・二軍なしのシーズンは今までと同じ(測定のシーズン。乱数の系列が別)。"""
    a = Season(copy.deepcopy(league), 9)
    b = Season(copy.deepcopy(league), 9, farm=False, injuries=None)
    a.play_days(30)
    b.play_days(30)
    assert [g.result.home_runs for g in a.played] == [g.result.home_runs for g in b.played]
    assert not b.injury_log


def test_farm_without_injuries_keeps_first_team_games(league):
    """二軍の試合だけでは、一軍の試合の結果は変わらない(二軍の乱数は別の系列)。"""
    a = Season(copy.deepcopy(league), 9)
    b = Season(copy.deepcopy(league), 9, farm=True)
    a.play_days(20)
    b.play_days(20)
    assert [(g.result.home_runs, g.result.away_runs) for g in a.played] == [(g.result.home_runs, g.result.away_runs) for g in b.played]
    assert b.farm.games > 0


def test_multiplier_and_roll():
    st = injmod.default_settings()

    class P:
        age = 36
        skills = ["durable_plus"]

    m = injmod.multiplier(P, st, 0.8, fatigue=20)
    assert m == pytest.approx((1 + 3 * 0.06) * 1.5 * 0.6 * 0.8)
    rng = random.Random(1)
    rolls = [injmod.roll(rng, st, 1.0, 10) for _ in range(4000)]
    c = Counter(r["severity"] for r in rolls)
    assert 0.66 < c["light"] / 4000 < 0.74 and c["season"] < 80
    assert all(r["after"] is None for r in rolls if r["severity"] in ("light", "middle"))
    after = [r for r in rolls if r["after"]]
    heavy = sum(1 for r in rolls if r["severity"] in ("heavy", "season"))
    assert 0 < len(after) < heavy * 0.3


def test_settings_are_checked():
    data = copy.deepcopy(injmod.default_settings().data)
    data["severity"][0]["share"] = 0.5
    with pytest.raises(ConfigError, match="合計が 1"):
        injmod.validate_injury_settings(data)


def test_aftereffect_lowers_abilities(league):
    p = copy.deepcopy(league.teams[0].players[0])
    before = dict(p.abilities)
    out = injmod.apply_aftereffect(p, {"items": 2, "points": [2, 3]}, 5)
    assert len(out) == 2 and all(before[k] - p.abilities[k] == v for k, v in out.items())


def test_playing_growth():
    rec = Records()
    rec.batters["A"] = Counter({"PA": 200})
    farm = Records()
    farm.batters["A"] = Counter({"PA": PLAYING_ENOUGH - 200})
    farm.batters["B"] = Counter({"PA": 10})

    class P:
        def __init__(self, pid, age):
            self.id, self.age, self.role = pid, age, "batter"

    out = playing_growth([P("A", 22), P("B", 24), P("C", 30), P("D", 19)], rec, farm)
    assert out == {"A": PLAYING_FACTORS[0], "B": PLAYING_FACTORS[1], "D": PLAYING_FACTORS[1]}


def test_rules_option():
    assert "injuries" in RULE_KEYS and rules_from(None).injuries is True
    assert rules_from({"injuries": False}).injuries is False


def test_api_save_and_year_end():
    g = api.Game.new(11, [None] * 12, 0, season_seed=11, baselines="default")
    s = g.state.season
    assert s.farm is not None and s.injury_settings is not None
    g.advance(40)
    again = api.Game(load_game(save_game(g.state)), dirty=False)
    assert again.state.season.injury_log == s.injury_log and again.state.season.farm.standings == s.farm.standings
    g.advance(30)
    again.advance(30)
    assert again.state.season.injury_log == s.injury_log and again.state.season.farm.standings == s.farm.standings
    assert again.state.season.farm.records.batters == s.farm.records.batters
    view = g.roster_view()
    assert all("farm_stats" in r and "injury" in r for r in view["farm"])
    st = g.status()
    assert st["farm"] and st["injuries"] is not None
    fs = g.farm_standings()
    assert fs["available"] and len(fs["leagues"]) == 2 and all(len(lg["rows"]) == 6 for lg in fs["leagues"])
    hurt = next(iter(s.injury_log))
    page = g.player(hurt["player_id"])
    assert page["injuries"] and page["injuries"][-1]["label"] == hurt["label"]
    if s.is_injured(hurt["player_id"]):
        assert page["player"]["injury"]["text"]
        with pytest.raises(ValueError, match="けが"):
            pid = hurt["player_id"]
            team = s.teams[hurt["team_id"]]
            out = next(p for p in s.actives[team.id].players if p.role == s.players[pid].role)
            s.swap(team.id, out.id, pid)
    g.advance_to_end()
    g.year_end()
    a = g.state.history[-1]
    assert a.farm_records is not None and a.injuries
    devs = {p.id for t in g.state.league.teams for p in t.development}
    assert devs & (set(a.farm_records.batters) | set(a.farm_records.pitchers))  # 育成選手も出る
    g.offseason_auto()
    nxt = g.state.season
    assert nxt.farm is not None and nxt.injury_settings is not None and not nxt.injury_log


def test_rules_without_injuries():
    g = api.Game.new(11, [None] * 12, 0, season_seed=11, baselines="default", rules={"injuries": False})
    assert g.state.season.injury_settings is None and g.state.season.farm is not None
    g.advance(15)
    assert not g.state.season.injury_log and g.status()["injuries"] is None
