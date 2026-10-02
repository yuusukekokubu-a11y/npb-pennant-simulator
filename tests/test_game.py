"""1試合の進行の受け入れ条件(1〜10)の確認。

ルールの確認には、結果を台本どおりに返す「台本の打席モデル」を使う
(打席の計算は差し替えられる形なので、テスト用のものに入れ替えられる)。
"""

import copy
import random
from itertools import chain, repeat

import pytest

from pennant import generate_league
from pennant.abilities import BATTER, PITCHER
from pennant.baserunning import HOME, Baserunning, Runner
from pennant.fatigue import advance_day, can_relieve, recovery_per_day
from pennant.game import BOTTOM, TOP, simulate_game
from pennant.game_config import default_game_data, load_game_config, validate_game_config
from pennant.game_stats import GAME_TARGETS, play_games, summarize
from pennant.manager import DH, SimpleManager
from pennant.pa_stats import average_player
from pennant.plate_appearance import BaseOutState, PlateAppearance


@pytest.fixture(scope="module")
def game_config():
    return load_game_config()


@pytest.fixture(scope="module")
def league2(config, names):
    return generate_league(77, config, names)


def setups(league, game_config, seed=0, manager=None):
    manager = manager or SimpleManager(game_config)
    rng = random.Random(seed)
    home, _ = manager.prepare(league.teams[0], rng, 0)
    away, _ = manager.prepare(league.teams[1], rng, 0)
    return home, away


# ---- 台本どおりに結果を返す打席モデル ----

SCRIPT_DEFAULTS = {
    "home_run": (None, None),
    "strikeout": (None, None),
    "walk": (None, None),
    "hit_by_pitch": (None, None),
    "single": ("line", "CF"),
    "double": ("line", "LF"),
    "triple": ("fly", "RF"),
    "ground_out": ("ground", "SS"),
    "fly_out": ("fly", "CF"),
    "line_out": ("line", "2B"),
    "error": ("ground", "3B"),
}


class ScriptedModel:
    def __init__(self, results, default="strikeout"):
        self.results = chain(results, repeat(default))
        self.batters = []

    def rating(self, player, item):
        return player.ratings[item]

    def fielding(self, position, pitcher, defense):
        return {"range": 50, "arm": 50, "fielding": 50}

    def probabilities(self, batter, pitcher, defense):
        raise NotImplementedError

    def resolve(self, batter, pitcher, defense, base_out, rng):
        self.batters.append(batter)
        r = next(self.results)
        bb, fielder = SCRIPT_DEFAULTS[r]
        return PlateAppearance(r, batter.id, pitcher.id, batter.bats or "R", base_out, batted_ball=bb, fielder=fielder)


def scripted_game(league, game_config, results, config=None):
    home, away = setups(league, game_config)
    return simulate_game(home, away, random.Random(1), model=ScriptedModel(results), config=config or game_config)


def halves(result):
    out = {}
    for x in result.log:
        out.setdefault((x.inning, x.half), []).append(x)
    return out


# ---- 受け入れ条件1:同じシードで同じ結果 ----

def test_same_seed_same_game(league2, game_config):
    a = simulate_game(*setups(league2, game_config), random.Random(5), config=game_config)
    b = simulate_game(*setups(league2, game_config), random.Random(5), config=game_config)
    c = simulate_game(*setups(league2, game_config), random.Random(6), config=game_config)
    assert a == b
    assert a.log != c.log


# ---- 受け入れ条件2:試合のルール ----

def test_three_outs_and_tie_after_max_innings(league2, game_config):
    r = scripted_game(league2, game_config, [])  # 全員三振
    assert r.innings == 12 and r.tie and r.extra_innings and not r.walkoff
    for key, pas in halves(r).items():
        assert len(pas) == 3
        assert sum(x.outs_made for x in pas) == 3
    assert len(r.line[r.home_team_id]) == len(r.line[r.away_team_id]) == 12


def test_no_tie_setting_keeps_playing(league2, game_config):
    data = default_game_data()
    data["rules"]["allow_tie"] = False
    data["rules"]["max_innings_without_tie"] = 15
    cfg = validate_game_config(data)
    r = scripted_game(league2, game_config, [], config=cfg)
    assert r.innings == 15  # 安全上の上限まで続ける


def test_bottom_of_ninth_skipped_when_home_leads(league2, game_config):
    r = scripted_game(league2, game_config, ["strikeout"] * 3 + ["home_run"])  # 1回裏に先制
    assert r.home_runs == 1 and r.away_runs == 0
    assert r.innings == 9 and r.line[r.home_team_id][-1] is None
    assert (9, BOTTOM) not in halves(r)


def test_game_ends_after_nine_when_away_leads(league2, game_config):
    r = scripted_game(league2, game_config, ["home_run"])  # 1回表に先制
    assert r.innings == 9 and r.away_runs == 1 and (9, BOTTOM) in halves(r)


def _until_bottom_ninth():
    # 1回表:本塁打+3三振、1回裏:3三振、2〜8回:各6三振、9回表:3三振
    return ["home_run"] + ["strikeout"] * (3 + 3 + 7 * 6 + 3)


def test_walkoff_home_run_ends_game(league2, game_config):
    r = scripted_game(league2, game_config, _until_bottom_ninth() + ["home_run", "home_run", "home_run"])
    assert r.walkoff and r.innings == 9
    assert r.home_runs == 2 and r.away_runs == 1  # 2本目の本塁打で終了
    assert r.log[-1].walkoff


def test_walkoff_counts_only_needed_runs(league2, game_config):
    """本塁打以外のサヨナラでは、勝ち越しに必要な分だけ得点を数える。"""
    script = _until_bottom_ninth() + ["walk", "walk", "walk", "double"]
    r = scripted_game(league2, game_config, script)
    assert r.walkoff and r.home_runs == 2 and r.away_runs == 1
    assert r.log[-1].runs == 2


def test_extra_innings_walkoff(league2, game_config):
    script = ["strikeout"] * (9 * 6) + ["strikeout"] * 3 + ["home_run"]  # 10回裏に本塁打
    r = scripted_game(league2, game_config, script)
    assert r.innings == 10 and r.walkoff and r.extra_innings and r.home_runs == 1


# ---- 受け入れ条件3:進塁のルール ----

def _runner(pid="R", speed=50):
    p = average_player(BATTER, pid, {"speed": speed})
    return Runner(p, "PIT")


def _br(overrides=None):
    data = default_game_data()
    for key, base in (overrides or {}).items():
        data["advancement"][key]["base"] = base
    cfg = validate_game_config(data)
    return Baserunning(cfg, lambda p, i: p.ratings[i])


def _pa(result, fielder=None, bb=None):
    return PlateAppearance(result, "B", "PIT", "R", BaseOutState(), batted_ball=bb, fielder=fielder)


def _advance(br, result, bases, outs=0, fielder=None, bb=None, seed=0):
    batter = average_player(BATTER, "B")
    return br.advance(_pa(result, fielder, bb), batter, "PIT", bases, outs, {"range": 50, "arm": 50, "fielding": 50}, random.Random(seed))


def test_walk_forces_only_when_needed():
    br = _br()
    loaded = (_runner("1"), _runner("2"), _runner("3"))
    play = _advance(br, "walk", loaded)
    assert play.runs == 1 and all(b is not None for b in play.bases)
    play = _advance(br, "hit_by_pitch", (None, _runner("2"), None))
    assert play.runs == 0 and play.bases[0].player.id == "B" and play.bases[1].player.id == "2"


def test_home_run_and_triple_clear_the_bases():
    br = _br()
    loaded = (_runner("1"), _runner("2"), _runner("3"))
    assert _advance(br, "home_run", loaded).runs == 4
    play = _advance(br, "triple", loaded, fielder="RF", bb="fly")
    assert play.runs == 3 and play.bases == (None, None, play.bases[2]) and play.bases[2].player.id == "B"


def test_double_play_only_with_runner_on_first_and_less_than_two_outs():
    br = _br({"double_play": 0.999})
    r1 = _runner("1")
    assert _advance(br, "ground_out", (r1, None, None), outs=0, fielder="SS", bb="ground").double_play
    assert _advance(br, "ground_out", (r1, None, None), outs=1, fielder="SS", bb="ground").double_play
    play = _advance(br, "ground_out", (r1, None, None), outs=2, fielder="SS", bb="ground")
    assert not play.double_play and play.outs == 1
    assert not _advance(br, "ground_out", (None, _runner("2"), None), outs=0, fielder="SS", bb="ground").double_play
    assert not _advance(br, "fly_out", (r1, None, None), outs=0, fielder="CF", bb="fly").double_play
    assert not _advance(br, "line_out", (r1, None, None), outs=0, fielder="2B", bb="line").double_play


def test_sac_fly_only_with_runner_on_third_less_than_two_outs_and_fly():
    br = _br({"fly_out_runner_from_3b_scores": 0.999, "ground_out_runner_from_3b_scores": 0.999})
    r3 = (None, None, _runner("3"))
    play = _advance(br, "fly_out", r3, outs=1, fielder="CF", bb="fly")
    assert play.sac_fly and play.runs == 1
    assert not _advance(br, "fly_out", r3, outs=2, fielder="CF", bb="fly").sac_fly
    assert not _advance(br, "fly_out", r3, outs=0, fielder="SS", bb="fly").sac_fly  # 内野フライ
    assert not _advance(br, "ground_out", r3, outs=0, fielder="SS", bb="ground").sac_fly
    assert not _advance(br, "line_out", r3, outs=0, fielder="2B", bb="line").sac_fly


def test_error_marks_batter_and_runners():
    br = _br()
    play = _advance(br, "error", (_runner("1"), None, None), fielder="3B", bb="ground")
    batter_move = next(m for m in play.moves if m.start == 0)
    assert batter_move.reached_on_error and batter_move.advanced_on_error and batter_move.end == 1
    assert all(m.advanced_on_error for m in play.moves)


def test_faster_runner_takes_extra_base_more_often():
    br = _br()
    rates = {}
    for speed in (35, 65):
        hits = sum(
            _advance(br, "single", (None, _runner("2", speed), None), fielder="CF", bb="line", seed=s).runs
            for s in range(3000)
        )
        rates[speed] = hits / 3000
    assert rates[65] > rates[35]


# ---- 受け入れ条件4・9:不変条件と打席ログ ----

@pytest.fixture(scope="module")
def many_games(league2, game_config):
    lg = copy.deepcopy(league2)
    return play_games(lg, 120, 3, game_config), lg


def test_invariants(many_games):
    results, lg = many_games
    roles = {p.id: p.role for p in lg.all_players()}
    for r in results:
        score = {r.home_team_id: 0, r.away_team_id: 0}
        for key, pas in halves(r).items():
            outs = 0
            for x in pas:
                assert x.base_out.outs == outs  # 打席の前のアウト数が正しく引き継がれる
                outs += x.outs_made
                assert outs <= 3
                ends = [m.end for m in x.moves if m.end is not None and m.end != HOME]
                assert len(ends) == len(set(ends))  # 同じ塁に2人いない
                assert x.runs == sum(1 for m in x.moves if m.scored)
                assert roles[x.batter_id] == BATTER and roles[x.pitcher_id] == PITCHER
                score[x.batting_team_id] += x.runs
            assert outs == 3 or pas[-1].walkoff
        assert score[r.home_team_id] == r.home_runs and score[r.away_team_id] == r.away_runs
        assert sum(v for v in r.line[r.home_team_id] if v is not None) == r.home_runs


def test_log_has_required_fields(many_games):
    results, _ = many_games
    seen_error = False
    for r in results:
        pitcher_ids = {p.pitcher_id for p in r.pitchers}
        for x in r.log:
            assert 1 <= x.lineup_slot <= 9 and x.inning >= 1 and x.half in (TOP, BOTTOM)
            assert isinstance(x.score_diff, int) and 0 <= x.base_out.index < 24
            assert x.pa.batter_id == x.batter_id
            for m in x.moves:
                assert m.responsible_pitcher_id in pitcher_ids
            if x.pa.result == "error":
                seen_error = True
                assert any(m.start == 0 and m.reached_on_error for m in x.moves)
    assert seen_error


def test_runs_are_charged_to_the_pitcher_who_allowed_the_runner(many_games):
    results, _ = many_games
    for r in results:
        charged = {}
        for x in r.log:
            for m in x.moves:
                if m.scored:
                    charged[m.responsible_pitcher_id] = charged.get(m.responsible_pitcher_id, 0) + 1
        for line in r.pitchers:
            assert line.runs == charged.get(line.pitcher_id, 0)


# ---- 受け入れ条件5:投手の起用と疲労 ----

def test_starter_exit_rules(many_games, game_config):
    results, _ = many_games
    manager = SimpleManager(game_config)
    for r in results:
        for line in r.pitchers:
            if line.role != "starter":
                continue
            if line.exit_reason == "run_limit":
                assert line.runs >= manager.run_limit()
            elif line.exit_reason == "batters_limit":
                assert line.batters_faced >= game_config["pitching"]["starter_bf_min"]


def test_relievers_pitch_one_inning(many_games):
    """救援は1イニング単位。例外は、登板できる投手がいなくて続投した場合だけ(記録が残る)。"""
    results, _ = many_games
    for r in results:
        lines = {p.pitcher_id: p for p in r.pitchers if p.role == "reliever"}
        innings = {}
        for x in r.log:
            if x.pitcher_id in lines:
                innings.setdefault(x.pitcher_id, set()).add(x.inning)
        for pid, inns in innings.items():
            assert len(inns) == 1 + lines[pid].forced_extra_innings


def test_fatigued_pitchers_cannot_pitch(league2, game_config):
    manager = SimpleManager(game_config)
    lg = copy.deepcopy(league2)
    active = manager.select_active(lg.teams[0])
    for p in active.starters[:3]:
        p.state.fatigue = 50
    starter, _ = manager.choose_starter(active, 0)
    assert starter in active.starters[3:]
    for r in active.relievers:
        r.state.fatigue = 50
    from pennant.manager import PitchingSituation

    # 救援が全員疲れているときは、疲れていない先発が救援に回る
    spare = manager.choose_reliever(active, PitchingSituation(9, 1, set()))
    assert spare in active.starters[3:]
    for p in active.starters:
        p.state.fatigue = 50
    assert manager.choose_reliever(active, PitchingSituation(9, 1, set())) is None
    assert not any(can_relieve(r, game_config) for r in active.relievers)


def test_higher_recovery_recovers_faster(game_config):
    slow = average_player(PITCHER, "S", {"recovery": 35})
    fast = average_player(PITCHER, "F", {"recovery": 65})
    for p in (slow, fast):
        p.state.fatigue = 20
    advance_day([slow, fast], game_config)
    assert fast.state.fatigue < slow.state.fatigue
    assert recovery_per_day(fast, game_config) > recovery_per_day(slow, game_config)


def test_game_does_not_change_input_players(league2, game_config):
    lg = copy.deepcopy(league2)
    before = [p.to_dict() for p in lg.all_players()]
    simulate_game(*setups(lg, game_config), random.Random(2), config=game_config)
    assert [p.to_dict() for p in lg.all_players()] == before


# ---- 受け入れ条件6:休養 ----

def _lineup_ids(league, cfg, seed):
    manager = SimpleManager(cfg)
    active = manager.select_active(league.teams[0])
    return [tuple((s.player.id, s.position) for s in manager.starting_lineup(active, random.Random(seed)).slots)]


def test_no_rest_gives_same_lineup_every_game(league2):
    data = default_game_data()
    data["rest"]["probability"] = data["rest"]["catcher_probability"] = 0
    cfg = validate_game_config(data)
    lineups = {tuple(_lineup_ids(league2, cfg, s)[0]) for s in range(30)}
    assert len(lineups) == 1


def test_rest_gives_bench_players_plate_appearances(league2):
    data = default_game_data()
    data["rest"]["probability"] = data["rest"]["catcher_probability"] = 0.5
    cfg = validate_game_config(data)
    lg = copy.deepcopy(league2)
    results = play_games(lg, 30, 4, cfg)
    s = summarize(results)
    assert s["控えの打席の割合"] > 0.2
    for r in results:
        for slots in r.lineups.values():
            positions = [pos for _, _, pos, _ in slots]
            assert sorted(positions) == sorted(["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", DH])


# ---- 受け入れ条件7:型・成長タイプ・潜在能力を使わない ----

def test_hidden_info_not_used(league2, game_config):
    a_league = copy.deepcopy(league2)
    b_league = copy.deepcopy(league2)
    for p in b_league.all_players():
        p.hidden.archetype = "other"
        p.hidden.growth_type = "late"
        p.hidden.potential = {k: v + 30 for k, v in p.hidden.potential.items()}
    a = simulate_game(*setups(a_league, game_config), random.Random(8), config=game_config)
    b = simulate_game(*setups(b_league, game_config), random.Random(8), config=game_config)
    assert a == b


# ---- 受け入れ条件8:差し替え可能 ----

def test_manager_can_be_replaced(league2, game_config):
    class ReverseOrder(SimpleManager):
        def batting_order(self, nine):
            return list(reversed(super().batting_order(nine)))

    normal = setups(league2, game_config)[0]
    custom = setups(league2, game_config, manager=ReverseOrder(game_config))[0]
    assert [s.player.id for s in custom.lineup.slots] == [s.player.id for s in reversed(normal.lineup.slots)]
    r = simulate_game(*setups(league2, game_config, manager=ReverseOrder(game_config)), random.Random(1),
                      config=game_config, manager=ReverseOrder(game_config))
    assert r.log


def test_active_roster_is_29(league2, game_config):
    active = SimpleManager(game_config).select_active(league2.teams[0])
    assert len(active.starters) == 6 and len(active.relievers) == 8 and len(active.batters) == 15
    assert len(active.players) == 29
    assert sum(1 for p in active.batters if p.position == "C") == 2
    assert {p.position for p in active.batters} >= {"C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"}
    assert sorted(active.relief_roles.values()).count("closer") == 1


# ---- 受け入れ条件10:校正の目標 ----

def test_calibration_targets(league2, game_config):
    lg = copy.deepcopy(league2)
    s = summarize(play_games(lg, 1000, 11, game_config))
    for key, (low, high) in GAME_TARGETS.items():
        assert low <= s[key] <= high, f"{key}: {s[key]:.3f}"
