"""日程の143試合(D-435〜D-438):連戦・交流戦・休み・オールスター・交流戦の順位・保存。"""

import copy
import random
from collections import Counter

import pytest

from pennant import ConfigError, generate_league
from pennant.api import Game
from pennant.savegame import load_game, save_game
from pennant.schedule import RoundRobinSchedule, SeriesSchedule, make_schedule_maker
from pennant.season import Season
from pennant.season_config import default_season_data, validate_season_config


@pytest.fixture(scope="module")
def league3(config, names):
    return generate_league(3, config, names)


@pytest.fixture(scope="module")
def season_done(league3):
    season = Season(copy.deepcopy(league3), 21)
    season.play_to_end()
    return season


def _make(league, year=1, seed=5):
    return make_schedule_maker(default_season_data()["schedule"], year).make(league, random.Random(seed))


def test_143_games_with_series_interleague_and_off_days(league3):
    s = _make(league3)
    league_of = {t.id: t.league_index for t in league3.teams}
    games, home, il_home, pairs = Counter(), Counter(), Counter(), Counter()
    for d, day in enumerate(s.days):
        teams_today = [t for g in day for t in (g.home_id, g.away_id)]
        assert len(teams_today) == len(set(teams_today)) and len(teams_today) in (0, 12)  # 試合の日は全球団が 1 試合
        for g in day:
            assert g.day == d
            assert g.interleague == (league_of[g.home_id] != league_of[g.away_id])
            games[g.home_id] += 1
            games[g.away_id] += 1
            (il_home if g.interleague else home)[g.home_id] += 1
            pairs[frozenset((g.home_id, g.away_id))] += 1
    assert set(games.values()) == {143}
    assert sorted(home.values()) == [62] * 6 + [63] * 6 and set(il_home.values()) == {9}
    assert Counter(pairs.values()) == {25: 30, 3: 36}
    assert 160 <= len(s.days) <= 180
    assert [g.number for g in s.games] == list(range(858))
    run = longest = 0
    for d, day in enumerate(s.days):
        run = run + 1 if day else 0
        longest = max(longest, run)
    assert longest <= 6  # 試合が続くのは 6 日まで


def test_interleague_block_and_allstar_break(league3):
    s = _make(league3)
    il_days = [d for d, day in enumerate(s.days) if day and day[0].interleague]
    assert len(il_days) == 18
    before = sum(1 for d in s.days[: il_days[0]] if d)
    assert 45 <= before <= 50  # 45 試合目のあとの、最初のシリーズの区切り
    a, b = s.allstar_days
    assert b == a + 1 and not s.days[a] and not s.days[b] and not s.days[a - 1] and not s.days[b + 1]  # 休み・試合・試合・休み
    assert 86 <= sum(1 for d in s.days[:a] if d) <= 89


def test_series_are_consecutive_and_home_alternates(league3):
    s = _make(league3)
    homes: dict[frozenset, list[str]] = {}
    for g in s.games:
        if not g.interleague:
            homes.setdefault(frozenset((g.home_id, g.away_id)), []).append(g.home_id)
    for hs in homes.values():
        changes = sum(1 for x, y in zip(hs, hs[1:]) if x != y)
        assert changes == 8 and Counter(hs).most_common(1)[0][1] == 13  # 9 シリーズで交互(シリーズの中はホームが同じ)


def test_interleague_home_swaps_by_year(league3):
    def il_homes(year):
        return {frozenset((g.home_id, g.away_id)): g.home_id for g in _make(league3, year).games if g.interleague}

    one, two = il_homes(1), il_homes(2)
    assert one.keys() == two.keys() and all(one[k] != two[k] for k in one)


def test_old_round_robin_without_series():
    maker = make_schedule_maker({"games_per_opponent": 25})
    assert isinstance(maker, RoundRobinSchedule) and isinstance(make_schedule_maker(default_season_data()["schedule"]), SeriesSchedule)


def test_series_must_add_up():
    data = default_season_data()
    data["schedule"]["series"] = [3, 3]
    with pytest.raises(ConfigError, match="合計"):
        validate_season_config(data)
    data["schedule"]["series"] = [3, "x"]
    with pytest.raises(ConfigError, match="series"):
        validate_season_config(data)


def test_allstar_results_do_not_touch_stats(season_done):
    s = season_done
    assert len(s.allstar) == 2 and [a["number"] for a in s.allstar] == [0, 1]
    assert {a["home_league"] for a in s.allstar} == {0, 1}
    for a in s.allstar:
        assert len(a["players"]["0"]) == len(a["players"]["1"]) == 29
        assert a["winner"] in (0, 1, None)
        if a["winner"] is not None:
            assert a["mvp"] and all(m["league"] == a["winner"] for m in a["mvp"])
            assert a["runs"][str(a["winner"])] > a["runs"][str(1 - a["winner"])]
    allstar_days = set(s.schedule.allstar_days)
    assert not any(p.scheduled.day in allstar_days for p in s.played)  # 公式戦には入れない
    assert all(r.games == 143 for i in (0, 1) for r in s.standings(i))


def test_allstar_does_not_change_the_season(league3, season_done):
    """オールスターは写しで行い、別の乱数なので、行わなくても公式戦の結果は同じ。"""
    data = default_season_data()
    del data["schedule"]["allstar"]
    cfg = validate_season_config(data)
    other = Season(copy.deepcopy(league3), 21, season_config=cfg)
    assert not other.schedule.allstar_days
    other.play_days(60)
    assert [(p.result.home_runs, p.result.away_runs) for p in other.played] == [(p.result.home_runs, p.result.away_runs) for p in season_done.played[: len(other.played)]]


def test_interleague_standings(season_done):
    rows = season_done.interleague_standings()
    assert len(rows) == 12 and [r.rank for r in rows] == list(range(1, 13))
    assert all(r.games == 18 for r in rows)
    assert sum(r.wins for r in rows) == sum(r.losses for r in rows)
    pcts = [r.pct for r in rows]
    assert pcts == sorted(pcts, reverse=True)


def test_one_day_skips_off_days():
    g = Game.new(11, [None] * 12, 0, season_seed=11, baselines="default")
    s = g.state.season
    off = next(d for d in range(1, s.total_days) if not s.schedule.days[d] and d not in s.schedule.allstar_days)
    g.advance(off)  # 休みの日の前まで
    assert g.status()["next_day"]["kind"] == "off"
    g.advance(1)
    assert s.day == off + 2 and s.schedule.days[s.day - 1]  # 休みを飛ばして次の試合の日まで
    assert g.last_day_games()["games"]
    assert g.games_on(off + 1)["day_kind"]["kind"] == "off"


def test_api_interleague_and_allstar_and_save():
    g = Game.new(12, [None] * 12, 0, season_seed=12, baselines="default")
    s = g.state.season
    assert g.standings()["interleague"] is None
    g.advance(s.schedule.allstar_days[1] + 1)
    st = g.status()
    assert len(st["allstar"]) == 2 and st["allstar"][0]["mvp"] is not None
    il = g.standings()["interleague"]
    assert il["finished"] and il["champion"] == il["rows"][0]["name"] and len(il["rows"]) == 12
    again = load_game(save_game(g.state))
    assert again.season.allstar == s.allstar and again.season.day == s.day
    g.advance(s.total_days)
    assert s.is_over


def test_version_26_keeps_this_season_and_next_season_gets_143():
    """版 26 のセーブデータは、今のシーズンを今までの日程で続け、次のシーズンから 143 試合(D-438)。"""
    from pennant.api.offseason import _next_season_config
    from pennant.save_migrations import v26_to_v27

    old = default_season_data()
    old["schedule"] = {"games_per_opponent": 25}
    cfg = validate_season_config(old)
    bundle = v26_to_v27.convert({"state": {"configs": {"season": old}, "season": {"day": 30}}})
    assert bundle["state"]["season"]["allstar"] == [] and "series" not in bundle["state"]["configs"]["season"]["schedule"]
    assert isinstance(make_schedule_maker(cfg["schedule"]), RoundRobinSchedule)
    nxt = _next_season_config(cfg)
    assert nxt["schedule"] == default_season_data()["schedule"] and nxt["standings"] == cfg["standings"]
    new = validate_season_config(default_season_data())
    assert _next_season_config(new) is new
