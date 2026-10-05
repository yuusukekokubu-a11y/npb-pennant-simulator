"""打撃・走塁・守備の得点(第3弾②。D-160〜D-166)の確認。"""

import statistics
from collections import Counter
from fractions import Fraction

import pytest

from pennant import api
from pennant.abilities import BATTER
from pennant.baselines import load_baseline_settings, season_baselines
from pennant.baserunning import HOME
from pennant.parkfactors import load_park_settings, run_seasons
from pennant.records import season_records
from pennant.runvalues import (
    PlayerRuns,
    batting_runs,
    discretionary_runners,
    league_totals,
    player_park_factors,
    runs_record,
    season_context,
    season_player_runs,
    situation_key,
)

SETTINGS = load_baseline_settings()


@pytest.fixture(scope="module")
def season():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    g.advance(125)
    results = [p.result for p in g.state.season.played]
    base = season_baselines(results, SETTINGS, SETTINGS.default_baselines())
    return g, results, base, season_player_runs(results, base)


# ---- 受け入れ条件2・8:合計が 0、分数、同じ入力なら同じ結果 ----

def test_league_totals_are_exactly_zero_and_fractions(season):
    _, results, base, runs = season
    totals = league_totals(runs)
    assert totals["batting"] == 0 and totals["baserunning"] == 0 and totals["fielding"] == 0
    for r in runs.values():
        assert isinstance(r.batting, Fraction) and isinstance(r.baserunning, Fraction) and isinstance(r.fielding, Fraction)
        assert sum(r.fielding_by_position.values(), Fraction(0)) == r.fielding
    again = season_player_runs(results, base)
    assert runs_record(again) == runs_record(runs)


def test_batting_runs_formula_by_hand():
    values = {"w_bb": Fraction(7, 10), "w_hbp": Fraction(72, 100), "w_1b": Fraction(9, 10), "w_2b": Fraction(5, 4), "w_3b": Fraction(8, 5), "w_hr": Fraction(2),
              "woba_scale": Fraction(6, 5), "lg_woba": Fraction(32, 100), "lg_r_pa": Fraction(1, 9)}
    c = Counter(AB=8, BB=1, HBP=1, SF=0, B1=1, B2=1, B3=0, HR=1)
    woba = (Fraction(7, 10) + Fraction(72, 100) + Fraction(9, 10) + Fraction(5, 4) + 2) / 10
    runs, denom = batting_runs(c, values)
    assert denom == 10 and runs == (woba - Fraction(32, 100)) / Fraction(6, 5) * 10
    with_pf, _ = batting_runs(c, values, Fraction(11, 10))
    assert with_pf == runs + (1 - Fraction(11, 10)) * Fraction(1, 9) * 10  # 打ちやすい球場なら割り引かれる
    assert batting_runs(Counter(), values) == (Fraction(0), 0)


def test_batting_runs_follow_woba_and_park_factor(season):
    g, results, base, runs = season
    rec = season_records(results)
    pid = max(rec.batters, key=lambda p: rec.batters[p]["PA"])
    r, denom = batting_runs(rec.batters[pid], base.values)
    assert runs[pid].batting == r and runs[pid].plate_appearances == denom
    # 球場補正を入れると、合計は 0 からずれるが、打ちやすい球場の選手は下がる
    pfs = {p: Fraction(11, 10) if p == pid else Fraction(1) for p in rec.batters}
    adjusted = season_player_runs(results, base, lambda p: pfs[p])
    assert adjusted[pid].batting < runs[pid].batting
    assert league_totals(adjusted)["batting"] == adjusted[pid].batting - runs[pid].batting


# ---- 走塁(D-163) ----

def test_discretionary_runners_follow_the_rules(season):
    _, results, _, _ = season
    seen = Counter()
    for result in results:
        for x in result.log:
            on = {m.start: m.player_id for m in x.moves if m.start > 0}
            picked = discretionary_runners(x)
            assert x.batter_id not in picked  # 打者は含めない
            assert set(picked) <= set(on.values())
            r = x.pa.result
            if r in ("walk", "hit_by_pitch", "home_run", "triple", "strikeout", "line_out"):
                assert picked == []
            elif r == "double":
                assert picked == ([on[1]] if 1 in on else [])
            elif r == "single":
                assert picked == ([on[k] for k in (2, 1) if k in on] if x.pa.fielder in ("LF", "CF", "RF") else [])
            elif r == "ground_out":
                if x.base_out.outs == 2:
                    assert picked == []
                elif 1 in on and 2 in on:
                    assert on[2] not in picked  # 押し出される二塁走者は含めない
            elif r == "fly_out":
                if x.base_out.outs == 2 or x.pa.fielder not in ("LF", "CF", "RF"):
                    assert picked == []
                else:
                    assert 1 not in [m.start for m in x.moves if m.player_id in picked]  # 一塁走者は進まない
            seen[r] += 1
    assert seen["single"] > 0 and seen["ground_out"] > 0


def test_baserunning_credit_is_mean_difference_split_among_runners(season):
    _, results, base, runs = season
    ctx = season_context(results, base)
    # 状況ごとの平均と、打席ごとの差の合計を手で足し直す
    by_player: dict[str, Fraction] = {}
    for result in results:
        halves = {}
        for x in result.log:
            halves.setdefault((x.inning, x.half), []).append(x)
        for pas in halves.values():
            for k, x in enumerate(pas):
                if x.walkoff:
                    continue
                runners = discretionary_runners(x)
                if not runners:
                    continue
                before = base.re24[x.base_out.index]
                after = Fraction(0) if k + 1 >= len(pas) else base.re24[pas[k + 1].base_out.index]
                if before is None or after is None:
                    continue
                d = after - before + x.runs
                mean = ctx.situation_mean(situation_key(x))
                for pid in runners:
                    by_player[pid] = by_player.get(pid, Fraction(0)) + (d - mean) / len(runners)
    for pid, v in by_player.items():
        assert runs[pid].baserunning == v
    assert sum(by_player.values(), Fraction(0)) == 0


def test_walks_and_home_runs_give_no_baserunning_credit(season):
    _, results, base, _ = season
    only = [r for r in results[:5]]
    # 四球・本塁打・三振だけの打席からは走塁の得点が出ない(選択の余地がない)
    for result in only:
        for x in result.log:
            if x.pa.result in ("walk", "home_run", "strikeout"):
                assert discretionary_runners(x) == []


# ---- 守備(D-164) ----

def test_fielding_runs_match_hand_recount(season):
    _, results, base, runs = season
    ctx = season_context(results, base)
    expected: dict[str, Fraction] = {}
    chances: dict[str, Counter] = {}
    for result in results:
        for x in result.log:
            pa = x.pa
            if pa.fielder and not pa.unfieldable and x.fielder_id and pa.batted_ball:
                rate = ctx.out_rate(pa.fielder, pa.batted_ball)
                made = 1 if pa.result in ("ground_out", "fly_out", "line_out") else 0
                expected[x.fielder_id] = expected.get(x.fielder_id, Fraction(0)) + (made - rate) * ctx.out_value(pa.batted_ball, base)
                chances.setdefault(x.fielder_id, Counter())[pa.fielder] += 1
    for pid, v in expected.items():
        assert runs[pid].fielding == v and runs[pid].chances_by_position == dict(chances[pid])
    for b in ("ground", "line", "fly"):
        assert ctx.out_value(b, base) > 0  # アウトにする価値は正
    # 真の能力が高い遊撃手ほど守備の得点が高い(1シーズンでも、相関は正)
    g = season[0]
    players = {p.id: p for p in g.state.league.all_players()}
    ss = [pid for pid in runs if runs[pid].chances_by_position.get("SS", 0) >= 200]
    xs = [float(runs[pid].fielding) for pid in ss]
    ys = [statistics.fmean(players[pid].ratings[i] for i in ("range", "arm", "fielding")) for pid in ss]
    assert len(ss) >= 10 and statistics.correlation(xs, ys) > 0


def test_defensive_outs_come_from_lineups(season):
    _, results, _, runs = season
    # 1試合で、守備側の 8 人(指名打者を除く)は同じアウト数を記録する
    result = results[0]
    outs = Counter()
    for x in result.log:
        outs[x.fielding_team_id] += x.outs_made
    single = season_player_runs([result], season[2])
    for team_id, slots in result.lineups.items():
        fielders = [(pid, pos) for _, pid, pos, _ in slots if pos != "DH"]
        assert len(fielders) == 8
        for pid, pos in fielders:
            assert single[pid].outs_by_position == {pos: outs[team_id]}
    # 投手は打順表にいないので、守備アウト数を持たない(担当した打球の数は持つ)
    assert all(not r.outs_by_position for pid, r in runs.items() if pid in {p.id for p in season[0].state.league.all_players() if p.role != BATTER})


# ---- 受け入れ条件3・4・7:複数シーズンの相関、指紋 (k) ----

@pytest.mark.slow
def test_runs_correlate_with_true_abilities_over_three_seasons():
    cumulative: dict[str, PlayerRuns] = {}
    league_holder = {}

    def on_results(k, results, league, estimates):
        league_holder["league"] = league
        base = season_baselines(results, SETTINGS, SETTINGS.default_baselines())
        pfs = player_park_factors(results, estimates)
        for pid, r in season_player_runs(results, base, lambda p: pfs.get(p, Fraction(1))).items():
            cumulative.setdefault(pid, PlayerRuns()).add(r)
        if estimates is not None:
            assert any(v != 1 for v in pfs.values())  # 2シーズン目からは球場補正が入る

    run_seasons(1, 3, load_park_settings(), on_results=on_results)
    players = {p.id: p for p in league_holder["league"].all_players()}
    fielders = [pid for pid, r in cumulative.items() if players[pid].role == BATTER and sum(r.chances_by_position.values()) >= 600]
    f_corr = statistics.correlation([float(cumulative[p].fielding) for p in fielders], [statistics.fmean(players[p].ratings[i] for i in ("range", "arm", "fielding")) for p in fielders])
    runners = [pid for pid, r in cumulative.items() if players[pid].role == BATTER and r.plate_appearances >= 900]
    b_corr = statistics.correlation([float(cumulative[p].baserunning) for p in runners], [statistics.fmean(players[p].ratings[i] for i in ("speed", "baserunning")) for p in runners])
    assert f_corr > 0.3 and b_corr > 0.3, (f_corr, b_corr)
