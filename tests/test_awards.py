"""表彰(D-442、D-443):賞の数・選び方・新人王の対象・保存・記録なしのシーズン。"""

from collections import Counter

import pytest

from pennant import api
from pennant import awards as awardsmod
from pennant.savegame import load_game, save_game


@pytest.fixture(scope="module")
def game():
    g = api.Game.new(11, [None] * 12, 0, season_seed=11, baselines="default")
    g.advance_to_end()
    g.year_end()
    return g


def _war(line):
    return line.war if line.role == "batter" else line.war_ra


def test_award_counts(game):
    a = game.state.history[-1]
    per = Counter((x["league"], x["key"]) for x in a.awards)
    for li in (0, 1):
        assert per[(li, "mvp")] == 1 and per[(li, "rookie")] <= 1
        assert per[(li, "best_nine")] == 10  # 投手・捕手・一塁・二塁・三塁・遊撃・外野 3・指名打者
        assert per[(li, "best_batter")] == 9  # 投手を除く
        assert per[(li, "best_defense")] == 8  # 捕手・内野 4・外野 3
        assert per[(li, "best_setup")] == 1 and per[(li, "best_closer")] == 1
    assert per[(None, "best_starter")] == 1
    of = [x["player_id"] for x in a.awards if x["key"] == "best_nine" and x["slot"] == "OF" and x["league"] == 0]
    assert len(set(of)) == 3


def test_mvp_is_the_league_war_leader(game):
    a = game.state.history[-1]
    league_of = {t.id: t.league_index for t in game.state.league.teams}
    for li in (0, 1):
        mvp = next(x for x in a.awards if x["key"] == "mvp" and x["league"] == li)
        best = max(_war(v) for pid, v in a.war.items() if league_of.get(a.players[pid]["team_id"]) == li)
        assert _war(a.war[mvp["player_id"]]) == best


def test_rookies_and_starter_rules(game):
    a = game.state.history[-1]
    rec = a.records
    for x in a.awards:
        if x["key"] == "rookie":
            p = game.state.season.players.get(x["player_id"]) or next(q for t in game.state.league.teams for q in t.players if q.id == x["player_id"])
            assert not p.foreign
            assert a.players[x["player_id"]]["age"] - awardsmod.ENTRY_AGE.get(p.origin, 22) < awardsmod.ROOKIE_YEARS
        if x["key"] == "best_starter":
            c = rec.pitchers[x["player_id"]]
            assert c["GS"] * 2 >= c["G"] and c["OUTS"] >= rec.teams[x["team_id"]]["G"] * 3
        if x["key"] in ("best_setup", "best_closer"):
            c = rec.pitchers[x["player_id"]]
            assert c["GS"] * 2 < c["G"]


def test_primary_slot():
    from pennant.records import Records
    from collections import Counter as C

    rec = Records()
    rec.batters["X"] = C({"G": 100})
    rec.fielders["X"] = {"LF": C({"G": 30}), "CF": C({"G": 25}), "1B": C({"G": 40})}
    assert awardsmod.primary_slot(rec, "X") == "OF"  # 外野はまとめて 55 試合
    rec.batters["Y"] = C({"G": 100})
    rec.fielders["Y"] = {"1B": C({"G": 20})}
    assert awardsmod.primary_slot(rec, "Y") == "DH"  # スタメン 100 − 守備 20


def test_api_views_and_save(game):
    r = game.record_awards(1)
    assert r["available"] and [g["label"] for g in r["groups"]][-1] == "両リーグ"
    mvp = r["groups"][0]["awards"][0]
    assert mvp["label"] == "MVP" and mvp["value"].startswith("WAR")
    assert any(x["label"] == "MVP" for x in game.player_awards(mvp["player_id"]))
    assert game.offseason_summary()["awards"] == r["groups"]
    again = api.Game(load_game(save_game(game.state)), dirty=False)
    assert again.state.history[-1].awards == game.state.history[-1].awards


def test_seasons_before_awards_have_no_record(game):
    """版 28 までに終わったシーズン(awards がない)は「記録なし」(D-443)。"""
    a = game.state.history[-1]
    saved = a.awards
    try:
        a.awards = None
        assert game.record_awards(1)["available"] is False and game.player_awards(saved[0]["player_id"]) == []
    finally:
        a.awards = saved
