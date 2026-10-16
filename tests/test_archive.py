"""記録室(D-422、D-423):歴代の順位と優勝・年ごとのタイトル・通算記録・補強の記録。読むだけで結果は変えない。"""

import json

import pytest

from pennant import api
from pennant.records import qualified_batters


@pytest.fixture(scope="module")
def two_seasons():
    """観戦のみで 2 回のオフを終えた状態(3 シーズン目の途中)。"""
    g = api.Game.new(3, [None] * 12, None, season_seed=17, baselines="default")
    for _ in range(2):
        g.advance(125)
        g.year_end()
    g.advance(10)
    return g


def test_nothing_before_the_first_year_end():
    g = api.Game.new(4, [None] * 12, 0, season_seed=4, baselines="default")
    g.advance(3)
    assert g.record_standings()["year"] is None and g.record_titles()["leagues"] == [] and g.record_moves()["year"] is None
    assert g.record_entries("draft")["year"] is None
    rows = g.record_leaders("batter", "H")["rows"]
    assert rows and rows[0]["rank"] == 1 and rows[0]["seasons"] == 1  # 通算は進行中の今シーズンも数える


@pytest.mark.slow
def test_standings_and_champions(two_seasons):
    g = two_seasons
    s = g.record_standings(1)
    a = g.state.history[0]
    assert s["years"] == [1, 2] and s["year"] == 1 and len(s["leagues"]) == 2
    for lg in s["leagues"]:
        assert [r["rank"] for r in lg["rows"]] == sorted(r["rank"] for r in lg["rows"]) and len(lg["rows"]) == 6
        assert {r["team_id"] for r in lg["rows"] if r["champion"]} == set(a.champions[lg["league_index"]])
    assert sum(c["count"] for c in g.record_standings()["champions"]) == sum(len(v) for x in g.state.history for v in x.champions.values())
    with pytest.raises(ValueError):
        g.record_standings(3)  # 進行中のシーズンは選べない


@pytest.mark.slow
def test_titles_follow_the_archived_records(two_seasons):
    g = two_seasons
    a = g.state.history[1]
    t = g.record_titles(2)
    league_of = {x.id: x.league_index for x in g.state.league.teams}
    for lg in t["leagues"]:
        by = {x["key"]: x for x in lg["titles"]}
        assert list(by) == ["AVG", "OBP", "HR", "RBI", "H", "SB", "W", "ERA", "SO", "SV", "HLD"]  # 盗塁王(D-432)
        hr = max(c.get("HR", 0) for pid, c in a.records.batters.items() if league_of[a.records.batter_team[pid]] == lg["league_index"])
        assert by["HR"]["value_text"] == str(hr) and by["HR"]["players"]
        q = set(qualified_batters(a.records))
        for p in by["AVG"]["players"]:
            assert p["player_id"] in q and league_of[p["team_id"]] == lg["league_index"]  # 首位打者は規定打席に届いた選手から


@pytest.mark.slow
def test_leaders_entries_and_moves(two_seasons):
    g = two_seasons
    L = g.record_leaders("pitcher", "W")
    values = [r["value"] for r in L["rows"]]
    assert values == sorted(values, reverse=True) and len(L["rows"]) >= 20 and L["rows"][0]["seasons"] <= 3
    assert all(r["rank"] == 1 + sum(1 for v in values if v > r["value"]) for r in L["rows"])  # 同じ値は同じ順位
    with pytest.raises(ValueError):
        g.record_leaders("batter", "W")
    d = g.record_entries("draft")
    assert d["rows"] and {r["route"] for r in d["rows"]} <= {"draft", "dev_draft"}  # ドラフト以外は混ざらない(D-422)
    f = g.record_entries("foreign", d["team_id"], d["year"]) if g.record_entries("foreign", d["team_id"])["year"] else None
    if f:
        assert {r["route"] for r in f["rows"]} <= {"foreign"}
    m = g.record_moves(1)
    kinds = {r["kind"] for r in m["rows"]}
    assert {"retire", "release"} <= kinds and not kinds & {"draft", "dev_draft", "foreign"}
    retired = {n.player_id for n in g.state.offseasons[0].retired}
    assert {r["player_id"] for r in m["rows"] if r["kind"] == "retire"} == retired
    t = g.record_moves(1, "T01")
    assert t["rows"] and len(t["rows"]) < len(m["rows"])
    text = json.dumps([g.record_standings(), g.record_titles(), L, d, m], ensure_ascii=False)
    for key in ("ratings", "potential", "growth_type", "hidden", "grade", "comment"):
        assert f'"{key}"' not in text  # 隠し情報と内部の評価は出さない
