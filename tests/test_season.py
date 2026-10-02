"""実装④:シーズン(日程・順位)の受け入れ条件の確認。"""

import copy
import importlib.util
import random
from collections import Counter
from pathlib import Path

import pytest

from pennant import ConfigError, generate_league
from pennant.fingerprint import season_record
from pennant.pa_config import default_pa_data, validate_pa_config
from pennant.plate_appearance import OddsRatioModel
from pennant.schedule import RoundRobinSchedule, home_holders, round_robin_rounds
from pennant.season import Season, _Record, derive_seed
from pennant.season_config import default_season_data, validate_season_config
from pennant.pa_stats import average_defense, average_player

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def league3(config, names):
    return generate_league(3, config, names)


@pytest.fixture(scope="module")
def season_done(league3):
    season = Season(copy.deepcopy(league3), 21)
    result = season.play_to_end()
    return season, result


def _season_config(**standings):
    data = default_season_data()
    data["standings"].update(standings)
    return validate_season_config(data)


# ---- 受け入れ条件2:日程の条件 ----

def test_schedule_conditions(league3):
    s = RoundRobinSchedule(25).make(league3, random.Random(5))
    league_of = {t.id: t.league_index for t in league3.teams}
    assert len(s.days) == 125 and len(s) == 750
    games_per_team, home, pairs = Counter(), Counter(), Counter()
    for day in s.days:
        teams_today = [t for g in day for t in (g.home_id, g.away_id)]
        assert len(teams_today) == len(set(teams_today)) == 12  # 同じ日に同じチームが2試合しない・全チームが試合をする
        assert Counter(g.league_index for g in day) == {0: 3, 1: 3}  # 2つのリーグが同じ日に並行する
        for g in day:
            assert league_of[g.home_id] == league_of[g.away_id] == g.league_index  # 同じリーグの中だけ
            games_per_team[g.home_id] += 1
            games_per_team[g.away_id] += 1
            home[g.home_id] += 1
            pairs[frozenset((g.home_id, g.away_id))] += 1
    assert set(games_per_team.values()) == {125}
    assert set(pairs.values()) == {25} and len(pairs) == 30
    assert sorted(home.values()) == [62] * 6 + [63] * 6
    assert [g.number for g in s.games] == list(range(750))


def test_home_alternates_and_holders_have_13(league3):
    s = RoundRobinSchedule(25).make(league3, random.Random(9))
    meetings: dict[frozenset, list[str]] = {}
    for g in s.games:
        meetings.setdefault(frozenset((g.home_id, g.away_id)), []).append(g.home_id)
    holders_per_team = Counter()
    for pair, homes in meetings.items():
        assert all(a != b for a, b in zip(homes, homes[1:]))  # 対戦ごとに交互
        holder, n = Counter(homes).most_common(1)[0]
        assert n == 13
        holders_per_team[holder] += 1
    assert set(holders_per_team.values()) <= {2, 3}
    for t in league3.teams:
        assert holders_per_team[t.id] in (2, 3)


def test_games_per_opponent_is_a_setting(league3):
    s = RoundRobinSchedule(4).make(league3, random.Random(1))
    home = Counter(g.home_id for g in s.games)
    assert len(s.days) == 20 and set(home.values()) == {10}  # 偶数なら、ホームとアウェイが同じ数


def test_round_robin_rounds_cover_every_pair_once():
    rounds = round_robin_rounds(list("ABCDEF"))
    assert len(rounds) == 5
    pairs = [frozenset(p) for r in rounds for p in r]
    assert len(pairs) == len(set(pairs)) == 15
    with pytest.raises(ValueError, match="偶数"):
        round_robin_rounds(list("ABC"))


def test_home_holders_are_balanced():
    for seed in range(20):
        holders = home_holders(list("ABCDEF"), random.Random(seed))
        assert len(holders) == 15
        assert sorted(Counter(holders.values()).values()) == [2, 2, 2, 3, 3, 3]


# ---- 受け入れ条件1:同じシードで同じシーズン ----

def test_same_seed_same_season(league3, season_done):
    season, result = season_done
    again = Season(copy.deepcopy(league3), 21).play_to_end()
    assert season_record(again) == season_record(result)
    assert [x.pa for p in again.games for x in p.result.log] == [x.pa for p in result.games for x in p.result.log]


def test_different_seed_different_season(league3, season_done):
    _, result = season_done
    other = Season(copy.deepcopy(league3), 22)
    other.play_days(5)
    assert season_record(other.result())["games"] != season_record(result)["games"][: len(other.played)]


def test_seed_derivation_is_stable():
    """シードの導き方は hashlib を使い、版や環境に依存しない(値を固定して確かめる)。"""
    assert derive_seed(1, "game:0") == int.from_bytes(__import__("hashlib").sha256(b"pennant:1:game:0").digest()[:8], "big")
    assert derive_seed(1, "game:0") != derive_seed(1, "game:1") != derive_seed(2, "game:1")


# ---- 受け入れ条件4:1試合だけを再現できる ----

def test_replay_single_game(season_done):
    season, result = season_done
    for number in (0, 137, 400, 749):
        original = result.games[number].result
        again = season.replay_game(number)
        assert [x.pa for x in again.log] == [x.pa for x in original.log]
        assert (again.home_runs, again.away_runs) == (original.home_runs, original.away_runs)


# ---- シーズンの進行 ----

def test_daily_flow_and_progress(league3):
    season = Season(copy.deepcopy(league3), 4)
    calls = []
    season.play_days(3, progress=lambda *a: calls.append(a))
    assert calls == [(1, 125, 6, 750), (2, 125, 12, 750), (3, 125, 18, 750)]
    starters = {l.pitcher_id for p in season.played for l in p.result.pitchers if l.role == "starter"}
    assert len(starters) == 36  # 3日 × 12チーム。各チームの先発は毎日別の投手(6人ローテーション)
    season.play_days(1000)
    assert season.is_over and season.day == 125
    with pytest.raises(ValueError, match="終わって"):
        season.play_day()


def test_rotation_mostly_keeps_order(season_done):
    _, result = season_done
    skipped = sum(sum(p.starter_skipped.values()) for p in result.games)
    assert skipped / (2 * len(result.games)) < 0.15  # 疲労で飛ばすのは例外


def test_season_result(season_done):
    _, result = season_done
    assert len(result.games) == 750
    for i, rows in result.standings.items():
        assert len(rows) == 6
        assert all(r.games == 125 for r in rows)
        assert result.champions[i] == [rows[0].team_id]
        assert sum(r.wins for r in rows) == sum(r.losses for r in rows)


# ---- 受け入れ条件3:順位表の並び ----

def _standings(league3, records, **standings):
    season = Season(copy.deepcopy(league3), 1, season_config=_season_config(**standings))
    ids = [t.id for t in league3.teams if t.league_index == 0]
    for tid in season._records:
        season._records[tid] = _Record()
    for tid, (w, l, t, vs) in records.items():
        season._records[ids[tid]] = _Record(w, l, t, {ids[k]: v for k, v in vs.items()})
    return [ids.index(r.team_id) for r in season.standings(0)], season.standings(0)


def test_order_by_pct(league3):
    order, rows = _standings(league3, {0: (5, 5, 0, {}), 1: (8, 2, 0, {}), 2: (2, 8, 0, {}), 3: (6, 4, 0, {})})
    assert order[:4] == [1, 3, 0, 2]
    assert [r.rank for r in rows] == [1, 2, 3, 4, 5, 6]


def test_ties_do_not_count_in_pct(league3):
    order, rows = _standings(league3, {0: (6, 4, 0, {}), 1: (6, 3, 5, {})})
    assert order[0] == 1 and rows[0].pct == pytest.approx(6 / 9)


def test_same_pct_then_more_wins(league3):
    order, _ = _standings(league3, {0: (3, 2, 0, {}), 1: (6, 4, 0, {})})  # どちらも .600
    assert order[:2] == [1, 0]


def test_same_pct_and_wins_then_head_to_head(league3):
    # 0 と 1 は同じ成績。直接対決は 1 が 3勝1敗
    order, _ = _standings(league3, {0: (6, 4, 0, {1: [1, 3, 0]}), 1: (6, 4, 0, {0: [3, 1, 0]})})
    assert order[:2] == [1, 0]


def test_all_equal_then_lot_is_fixed_by_seed(league3):
    recs = {0: (6, 4, 0, {1: [2, 2, 0]}), 1: (6, 4, 0, {0: [2, 2, 0]})}
    a, rows = _standings(league3, recs)
    b, _ = _standings(league3, recs)
    assert a == b and rows[0].rank == 1 and rows[1].rank == 2


def test_shared_rank_setting(league3):
    recs = {0: (6, 4, 0, {}), 1: (3, 2, 0, {}), 2: (1, 9, 0, {})}
    _, rows = _standings(league3, recs, allow_shared_rank=True)
    assert [r.rank for r in rows[:3]] == [1, 1, 3]


def test_games_behind(league3):
    _, rows = _standings(league3, {0: (10, 4, 0, {}), 1: (8, 6, 0, {}), 2: (7, 6, 1, {})})
    assert [r.games_behind for r in rows[:3]] == [0.0, 2.0, 2.5]
    assert [r.games_behind_prev for r in rows[:3]] == [0.0, 2.0, 0.5]


# ---- 受け入れ条件5:ホームの有利 ----

def _model(home_advantage):
    data = default_pa_data()
    data["home_advantage"] = home_advantage
    return OddsRatioModel(validate_pa_config(data))


def test_home_advantage_in_odds_ratio():
    b, p, d = average_player("batter", "B"), average_player("pitcher", "P"), average_defense()
    model = _model({"strikeout": 0.94, "walk": 1.06, "home_run": 1.06, "in_play_hit": 1.06})
    away, home = model.probabilities(b, p, d), model.probabilities(b, p, d, home=True)
    assert home["strikeout"] < away["strikeout"]
    assert home["walk"] > away["walk"] and home["home_run"] > away["home_run"] and home["single"] > away["single"]
    flat = _model({"strikeout": 1.0, "walk": 1.0, "home_run": 1.0, "in_play_hit": 1.0})
    assert flat.probabilities(b, p, d, home=True) == flat.probabilities(b, p, d)


def test_no_home_advantage_gives_about_half(league3, season_done):
    flat = _model({"strikeout": 1.0, "walk": 1.0, "home_run": 1.0, "in_play_hit": 1.0})
    result = Season(copy.deepcopy(league3), 21, model=flat).play_to_end()

    def home_rate(r):
        decided = [p.result for p in r.games if not p.result.tie]
        return sum(g.winner == g.home_team_id for g in decided) / len(decided)

    assert 0.46 <= home_rate(result) <= 0.54
    assert home_rate(season_done[1]) > home_rate(result)


def test_home_advantage_config_is_checked():
    data = default_pa_data()
    data["home_advantage"]["triple"] = 1.1
    with pytest.raises(ConfigError, match="home_advantage.triple"):
        validate_pa_config(data)


# ---- 受け入れ条件7:隠し情報を使わない ----

def test_hidden_info_not_used(league3):
    a = Season(copy.deepcopy(league3), 8)
    a.play_days(10)
    changed = copy.deepcopy(league3)
    for p in changed.all_players():
        p.hidden.potential = {k: 99.0 for k in p.hidden.potential}
        p.hidden.growth_type = "late"
        p.hidden.archetype = "other"
    b = Season(changed, 8)
    b.play_days(10)
    assert season_record(a.result()) == season_record(b.result())


# ---- 設定ファイルと確認用スクリプト ----

def test_season_config_validation():
    data = default_season_data()
    data["schedule"]["games_per_opponent"] = 0
    data["standings"]["allow_shared_rank"] = "yes"
    with pytest.raises(ConfigError) as info:
        validate_season_config(data)
    assert "schedule.games_per_opponent" in str(info.value) and "true か false" in str(info.value)


def test_script_runs(capsys):
    spec = importlib.util.spec_from_file_location("inspect_season", ROOT / "scripts" / "inspect_season.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--seed", "2"]) == 0
    out = capsys.readouterr().out
    for heading in ("最終順位表", "ホーム・アウェイの試合数", "ホームの勝率", "チーム勝率の分布", "登板間隔", "飛ばした回数"):
        assert heading in out
