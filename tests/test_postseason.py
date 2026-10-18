"""ポストシーズン(D-439〜D-441):CS・日本シリーズの組み合わせと日程、勝ち抜けの決まり、成績に入れないこと、保存。"""

import copy

import pytest

from pennant import ConfigError, generate_league
from pennant.api import Game
from pennant.api.offseason import _next_season_config
from pennant.postseason import Postseason, default_settings
from pennant.savegame import load_game, save_game
from pennant.season import Season
from pennant.season_config import default_season_data, validate_season_config


@pytest.fixture(scope="module")
def league3(config, names):
    return generate_league(3, config, names)


@pytest.fixture(scope="module")
def done(league3):
    s = Season(copy.deepcopy(league3), 21)
    s.play_to_end()
    standings = {i: [(r.team_id, r.wins, r.losses) for r in s.standings(i)] for i in (0, 1)}
    played = len(s.played)
    s.play_days(1000)
    return s, standings, played


def test_brackets_follow_the_final_standings(done):
    s, standings, _ = done
    post = s.postseason
    assert s.complete and post.finished
    order = {i: [t for t, _, _ in standings[i]] for i in (0, 1)}
    firsts = [x for x in post.series if x["stage"] == "first"]
    finals = [x for x in post.series if x["stage"] == "final"]
    japan = [x for x in post.series if x["stage"] == "japan"]
    assert len(firsts) == 2 and len(finals) == 2 and len(japan) == 1
    for f in firsts:
        assert f["teams"] == order[f["league"]][1:3] and f["seeds"] == [2, 3]
    for f in finals:
        first = next(x for x in firsts if x["league"] == f["league"])
        assert f["teams"] == [order[f["league"]][0], first["winner"]]
    assert set(japan[0]["teams"]) == {f["winner"] for f in finals}
    assert post.champion == japan[0]["winner"]


def test_series_rules(done):
    s, _, _ = done
    post = s.postseason
    for x in post.series:
        c = post._stage_cfg(x["stage"])
        w = x["wins"]
        k = x["teams"].index(x["winner"])
        if x["stage"] == "japan":
            assert w[k] == c["wins"] and w[1 - k] < c["wins"]
        else:
            assert len(x["games"]) <= c["games"]
            assert w[k] >= c["wins"] or (k == 0 and len(x["games"]) == c["games"])  # 使い切ったら順位が上
        if x["stage"] == "final":
            assert sum(w) == len(x["games"]) - x["ties"] + c["advantage"]  # アドバンテージの 1 勝
        homes = [post.games[n].scheduled.home_id for n in x["games"]]
        if x["stage"] == "japan":
            assert homes == [x["teams"][0 if i % 7 in (0, 1, 5, 6) else 1] for i in range(len(homes))]  # 2・3・2
        else:
            assert set(homes) == {x["teams"][0]}  # CS は順位が上の球場
        assert x["mvp"] is not None and x["mvp"]["team_id"] == x["winner"]


def test_schedule_rest_days_and_parallel_leagues(done):
    s, _, _ = done
    post = s.postseason
    total = s.total_days
    firsts = [x for x in post.series if x["stage"] == "first"]
    assert {x["start"] for x in firsts} == {total + 1}  # シーズンの後に休み 1 日。2 リーグは同じ日
    for x in post.series:
        days = [post.games[n].scheduled.day for n in x["games"]]
        assert days == [post.game_day(x, i) for i in range(len(days))]
    first_end = max(post.games[x["games"][-1]].scheduled.day for x in firsts)
    finals = [x for x in post.series if x["stage"] == "final"]
    assert {x["start"] for x in finals} == {first_end + 2}
    final_end = max(post.games[x["games"][-1]].scheduled.day for x in finals)
    japan = next(x for x in post.series if x["stage"] == "japan")
    assert japan["start"] == final_end + 3
    jdays = [post.games[n].scheduled.day - japan["start"] for n in japan["games"]]
    assert jdays == [0, 1, 3, 4, 5, 7, 8][: len(jdays)]  # 2 試合目・5 試合目の後に休み


def test_postseason_is_not_in_standings_or_stats(done):
    s, standings, played = done
    assert len(s.played) == played  # 成績・試合の一覧はレギュラーシーズンだけ
    assert {i: [(r.team_id, r.wins, r.losses) for r in s.standings(i)] for i in (0, 1)} == standings


def test_replay_postseason_game(done):
    s, _, _ = done
    for k in (0, len(s.postseason.games) - 1):
        a, b = s.replay_game(k, post=True), s.postseason.games[k].result
        assert [x.pa for x in a.log] == [x.pa for x in b.log]


def test_cs_goes_to_the_higher_seed_when_games_run_out(done):
    s, _, _ = done
    post = Postseason(s, default_settings())
    x = post._new("first", 0, "T01", "T02", 0, ["T01", "T02", "T03"])
    x["games"] = [0, 1, 2]
    x["wins"], x["ties"] = [1, 1], 1
    post.games = s.postseason.games[:3]
    post._decide(x)
    assert x["winner"] == "T01"


def test_settings_are_checked():
    data = default_season_data()
    data["postseason"]["final_stage"]["wins"] = 9
    with pytest.raises(ConfigError, match="勝ち抜け"):
        validate_season_config(data)
    data = default_season_data()
    data["postseason"]["rest_days"] = [1, 1]
    with pytest.raises(ConfigError, match="rest_days"):
        validate_season_config(data)
    data = default_season_data()
    del data["postseason"]
    assert validate_season_config(data).data.get("postseason") is None


def test_no_postseason_without_settings(league3):
    """版 27 のセーブデータの続き:設定に postseason がなければ、レギュラーシーズンで終わる(D-441)。"""
    data = default_season_data()
    del data["postseason"]
    cfg = validate_season_config(data)
    s = Season(copy.deepcopy(league3), 4, season_config=cfg)
    s.play_to_end()
    assert s.postseason is None and s.complete
    assert "postseason" in _next_season_config(cfg).data


def test_api_flow_save_and_year_end():
    g = Game.new(11, [None] * 12, 0, season_seed=11, baselines="default")
    s = g.state.season
    g.advance(s.total_days)
    st = g.status()
    assert st["regular_over"] and not st["is_over"] and not st["can_year_end"] and st["postseason"]["series"]
    with pytest.raises(ValueError, match="終わっていません"):
        g.year_end()
    g.advance(1)  # 休みを飛ばして CS 1st の第 1 戦まで
    assert s.day == s.total_days + 2 and g.status()["next_day"]["kind"] == "postseason"
    assert all(x["tags"][0].endswith("第1戦") for x in g.last_day_games()["games"])
    g.advance(3)
    again = Game(load_game(save_game(g.state)), dirty=False)
    assert again.state.season.day == s.day and again.status()["postseason"] == g.status()["postseason"]
    g.advance_to_end()
    again.advance_to_end()
    assert g.status()["is_over"] and g.status()["postseason"]["champion"] == again.status()["postseason"]["champion"]
    last = g.last_day_games()["games"]
    assert last and g.game(last[0]["game_no"])["summary"]["postseason"]
    champion = g.status()["postseason"]["champion"]
    g.year_end()
    rs = g.record_standings(1)
    assert rs["postseason"]["champion"] == champion and len(rs["postseason"]["series"]) == 5
    assert [c["team_name"] for c in rs["champions"] if c["japan_years"] == [1]] == [champion]
