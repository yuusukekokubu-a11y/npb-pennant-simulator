"""進行の画面の得点板と調子(作り直し④。D-400):次の相手・連勝連敗・直近 10 試合は、試合の結果と日程から作る公開の情報。"""

import copy

import pytest

from pennant import api


@pytest.fixture(scope="module")
def game():
    g = api.Game.new(1, [None] * 12, 0, season_seed=1, baselines="default")
    g.advance(30)
    return g


def _outcomes(g, team_id):
    out = []
    for p in g.state.season.played:
        r = p.result
        if team_id in (r.home_team_id, r.away_team_id):
            mine, theirs = (r.home_runs, r.away_runs) if r.home_team_id == team_id else (r.away_runs, r.home_runs)
            out.append("分" if r.tie else ("勝" if mine > theirs else "負"))
    return out


def test_next_game_is_the_first_unplayed_game(game):
    m = game.status()["my_team"]
    season = game.state.season
    g = next(g for d in season.schedule.days[season.day:] for g in d if m["id"] in (g.home_id, g.away_id))
    n = m["next_game"]
    opp = g.away_id if g.home_id == m["id"] else g.home_id
    assert (n["day"], n["home"], n["opponent_id"], n["finished"]) == (g.day + 1, g.home_id == m["id"], opp, False)
    t = next(r for lg in game.standings()["leagues"] for r in lg["rows"] if r["team_id"] == opp)
    assert n["opponent_name"] == t["name"] and n["opponent_record"] == {"wins": t["wins"], "losses": t["losses"], "ties": t["ties"]}


def test_streak_and_last10_follow_results(game):
    m = game.status()["my_team"]
    out = _outcomes(game, m["id"])[::-1]
    ten = out[:10]
    assert m["last10"] == {"wins": ten.count("勝"), "losses": ten.count("負"), "ties": ten.count("分"), "games": len(ten)}
    if out[0] == "分":
        assert m["streak"] is None
    else:
        n = next((i for i, o in enumerate(out) if o != out[0]), len(out))
        assert m["streak"] == {"outcome": out[0], "count": n}


def test_before_the_season_and_spectator():
    g = api.Game.new(2, [None] * 12, 3, season_seed=2, baselines="default")
    m = g.status()["my_team"]
    assert m["next_game"]["day"] >= 1 and not m["next_game"]["finished"] and m["streak"] is None and m["last10"]["games"] == 0
    assert api.Game.new(2, [None] * 12, None, season_seed=2, baselines="default").status()["my_team"] is None  # 観戦のみ


@pytest.mark.slow
def test_after_the_season_shows_the_last_opponent(game):
    g = api.Game(copy.deepcopy(game.state), dirty=False)
    g.advance_to_end()
    n = g.status()["my_team"]["next_game"]
    last = next(p for p in reversed(g.state.season.played) if g.state.my_team_id in (p.scheduled.home_id, p.scheduled.away_id))
    assert n["finished"] and n["day"] == last.scheduled.day + 1  # 終わったら最後の試合の相手
