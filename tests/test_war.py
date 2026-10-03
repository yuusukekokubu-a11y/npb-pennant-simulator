"""WAR の計算(第3弾③a。D-167〜D-173)の確認。"""

import copy
import statistics
from fractions import Fraction

import pytest

from pennant import api
from pennant.baselines import load_baseline_settings, season_baselines
from pennant.config import ConfigError
from pennant.records import season_records
from pennant.runvalues import season_player_runs
from pennant.war import (
    dh_plate_appearances,
    load_war_settings,
    pitcher_role_outs,
    runs_per_win,
    season_war,
    target_total_war,
    validate_war_settings,
    war_record,
    war_totals,
)

SETTINGS = load_baseline_settings()
WAR = load_war_settings()


@pytest.fixture(scope="module")
def season():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    g.advance(125)
    results = [p.result for p in g.state.season.played]
    base = season_baselines(results, SETTINGS, SETTINGS.default_baselines())
    rec = season_records(results)
    runs = season_player_runs(results, base, records=rec)
    return g, results, base, rec, runs, season_war(results, base, runs, WAR, rec)


def test_runs_per_win_by_hand():
    values = {"lg_r_pa": Fraction(1, 9)}
    assert runs_per_win(values, Fraction(405, 10)) == 9  # 1試合 4.5 点 → 指数 2 で 9 点
    assert runs_per_win(values, Fraction(405, 10), Fraction(18, 10)) == 10  # 指数 1.8 なら 10 点


def test_settings_validation():
    data = copy.deepcopy(WAR.data)
    data["position_runs_per_season"]["P"] = 0
    with pytest.raises(ConfigError, match="position_runs_per_season.P"):
        validate_war_settings(data)
    data = copy.deepcopy(WAR.data)
    data["replacement"]["reliever_runs_per_9"] = 2.0
    with pytest.raises(ConfigError, match="reliever_runs_per_9"):
        validate_war_settings(data)
    assert WAR.position_runs("DH") < WAR.position_runs("1B") < WAR.position_runs("LF") < WAR.position_runs("2B") < WAR.position_runs("SS") < WAR.position_runs("C")


def test_batter_war_is_sum_of_parts_over_runs_per_win(season):
    _, results, base, rec, runs, lines = season
    total_pa = sum(c["PA"] for c in rec.batters.values())
    rpw = runs_per_win(base.values, Fraction(total_pa, sum(c["G"] for c in rec.teams.values())))
    assert 7 < rpw < 11
    games = max(c["G"] for c in rec.teams.values())
    dh = dh_plate_appearances(results)
    pa_per_slot = Fraction(total_pa, 9 * len(rec.teams))
    for pid, v in lines.items():
        if v.role != "batter":
            continue
        r = runs[pid]
        assert v.batting == r.batting and v.baserunning == r.baserunning and v.fielding == r.fielding
        pos = sum((WAR.position_runs(p) * Fraction(o, games * 27) for p, o in r.outs_by_position.items()), Fraction(0))
        pos += WAR.position_runs("DH") * Fraction(dh.get(pid, 0)) / pa_per_slot
        assert v.position == pos
        assert v.replacement == WAR.replacement("batter_runs_per_pa") * r.plate_appearances
        assert v.war == (v.batting + v.baserunning + v.fielding + v.position + v.replacement) / rpw
        assert isinstance(v.war, Fraction)


def test_pitcher_war_formulas(season):
    _, results, base, rec, runs, lines = season
    values = base.values
    lg_outs = sum(c["OUTS"] for c in rec.pitchers.values())
    lg_ra9 = Fraction(27 * sum(c["R"] for c in rec.pitchers.values()), lg_outs)
    role_outs = pitcher_role_outs(results)
    team_field = {}
    for pid, r in runs.items():
        if pid in rec.batters:
            team_field[rec.batter_team[pid]] = team_field.get(rec.batter_team[pid], Fraction(0)) + r.fielding
    team_outs = {}
    for pid, c in rec.pitchers.items():
        team_outs[rec.pitcher_team[pid]] = team_outs.get(rec.pitcher_team[pid], 0) + c["OUTS"]
    rpw = runs_per_win(values, Fraction(sum(c["PA"] for c in rec.batters.values()), sum(c["G"] for c in rec.teams.values())))
    checked = 0
    for pid, c in rec.pitchers.items():
        v = lines[pid]
        assert v.role == "pitcher" and v.outs == c["OUTS"]
        if not c["OUTS"]:
            continue
        ip9 = Fraction(c["OUTS"], 27)
        fip = (values["fip_hr"] * c["HR"] + values["fip_bb"] * (c["BB"] + c["HBP"]) - values["fip_so"] * c["SO"]) * 3 / Fraction(c["OUTS"]) + values["fip_constant"]
        assert v.fip_runs == (values["lg_era"] - fip) * ip9
        assert v.park_factor == 1 and v.ra_runs == (lg_ra9 - Fraction(27 * c["R"], c["OUTS"])) * ip9
        tid = rec.pitcher_team[pid]
        assert v.defense_adjustment == team_field.get(tid, Fraction(0)) * Fraction(c["OUTS"], team_outs[tid])
        ro = role_outs[pid]
        assert ro["starter"] + ro["reliever"] == c["OUTS"]
        assert v.replacement == WAR.replacement("starter_runs_per_9") * Fraction(ro["starter"], 27) + WAR.replacement("reliever_runs_per_9") * Fraction(ro["reliever"], 27)
        assert v.war_fip == (v.fip_runs + v.replacement) / rpw and v.war_ra == (v.ra_runs - v.defense_adjustment + v.replacement) / rpw
        checked += 1
    assert checked > 100
    # リーグ全体では、FIP 版と失点版の「平均との差」の合計は 0、守備の調整の合計も 0(各ポジションの守備の得点の合計が 0 のため)
    assert sum((v.fip_runs for v in lines.values() if v.role == "pitcher"), Fraction(0)) == 0
    assert sum((v.ra_runs for v in lines.values() if v.role == "pitcher"), Fraction(0)) == 0
    assert sum((v.defense_adjustment for v in lines.values() if v.role == "pitcher"), Fraction(0)) == 0


def test_pitcher_park_factor_lowers_ra_war_in_hitter_parks(season):
    _, results, base, rec, runs, lines = season
    pid = max(rec.pitchers, key=lambda p: rec.pitchers[p]["OUTS"])
    adjusted = season_war(results, base, runs, WAR, rec, lambda p: Fraction(11, 10) if p == pid else Fraction(1))
    assert adjusted[pid].war_ra > lines[pid].war_ra  # 打ちやすい球場で投げた投手は、失点版が上がる
    assert adjusted[pid].war_fip == lines[pid].war_fip  # FIP 版は球場補正をしない(D-144)


def test_league_total_matches_target_and_split(season):
    _, _, _, rec, _, lines = season
    t = war_totals(lines)
    target = target_total_war(rec, WAR)
    total = t["batters"] + t["pitchers_ra"]
    assert abs(float(total / target) - 1) < 0.10, (float(total), float(target))  # 受け入れ条件2:±10%
    share = float(t["batters"] / total)
    assert 0.5 < share < 0.7  # 野手:投手 ≒ 6:4


def test_same_input_same_output_and_record(season):
    _, results, base, rec, runs, lines = season
    again = season_war(results, base, runs, WAR, rec)
    assert war_record(again) == war_record(lines)
    d = war_record(lines)
    assert all(isinstance(v, str) for rec_ in d.values() for k, v in rec_.items() if k not in ("plate_appearances", "outs"))


def test_war_correlates_with_true_ability(season):
    g, _, _, rec, _, lines = season
    players = {p.id: p for p in g.state.league.all_players()}
    from pennant.stats import overall

    bats = [pid for pid, v in lines.items() if v.role == "batter" and v.plate_appearances >= 300]
    pits = [pid for pid, v in lines.items() if v.role == "pitcher" and v.outs >= 150]
    assert statistics.correlation([float(lines[p].war) for p in bats], [overall(players[p]) for p in bats]) > 0.4
    assert statistics.correlation([float(lines[p].war_fip) for p in pits], [overall(players[p]) for p in pits]) > 0.2  # 1シーズンの投手は運のぶれが大きい(複数シーズンは inspect_war.py)
    assert statistics.correlation([float(lines[p].war_ra) for p in pits], [overall(players[p]) for p in pits]) > 0.2
