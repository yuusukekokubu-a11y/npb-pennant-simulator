"""選手のページの年度別の成績は 1 シーズン目から出す(D-417)。通算の行は今シーズンと同じ値。"""

from pennant import api


def test_first_season_shows_current_and_career():
    g = api.Game.new(4, [None] * 12, 0, season_seed=4, baselines="default")
    g.advance(5)
    pid = next(iter(g.records.total.batters))
    h = g.player(pid)["history"]
    current, career = h["rows"]
    assert current["season"] == "current" and current["year"] == 1 and career["season"] == "career"
    assert career["tables"] == current["tables"] and career["war"] == current["war"] and career["age"] is None and career["team_name"] == ""
