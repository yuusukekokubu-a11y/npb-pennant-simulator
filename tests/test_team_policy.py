"""チーム方針(D-454〜D-457)と送りバント・犠打(D-458〜D-461):バントの動きと頻度、方針の効き、画面の操作、保存と再現。"""

import io
import json
import random
import zipfile
from collections import Counter
from types import SimpleNamespace

import pytest

from pennant import api, policy as policymod
from pennant.baserunning import Baserunning, Runner
from pennant.game_config import load_game_config
from pennant.game_stats import describe
from pennant.manager import BuntSituation, SimpleManager
from pennant.plate_appearance import BaseOutState, default_model
from pennant.records import season_records
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game
from pennant.stats_report import consistency_checks


@pytest.fixture(scope="module")
def mid():
    """自球団 T01 で 30 日進めた状態。"""
    g = api.Game.new(5, [None] * 12, 0, season_seed=5, baselines="default")
    g.advance(30)
    return g


def _fresh(g) -> api.Game:
    return api.Game(load_game(save_game(g.state)), dirty=False)


def _bunts(g):
    return [x for p in g.state.season.played for x in p.result.log if x.bunt]


# ---- 送りバントの動き(D-458) ----

def test_bunt_moves_for_each_kind():
    br = Baserunning(load_game_config(), default_model().rating)
    batter = SimpleNamespace(id="B")
    r1, r2 = Runner(SimpleNamespace(id="R1"), "P"), Runner(SimpleNamespace(id="R2"), "P")
    both = (r1, r2, None)

    def ends(play):
        return {m.player_id: m.end for m in play.moves}

    sac = br.bunt("sacrifice", batter, "P", both)
    assert ends(sac) == {"R2": 3, "R1": 2, "B": None} and sac.outs == 1 and sac.runs == 0
    pop = br.bunt("popup", batter, "P", both)
    assert ends(pop) == {"R2": 2, "R1": 1, "B": None} and pop.outs == 1
    lead = br.bunt("lead_out", batter, "P", both)
    assert ends(lead) == {"R2": None, "R1": 2, "B": 1} and lead.outs == 1  # 先の塁で前の走者がアウト(野選)
    hit = br.bunt("hit", batter, "P", (r1, None, None))
    assert ends(hit) == {"R1": 2, "B": 1} and hit.outs == 0
    err = br.bunt("error", batter, "P", (None, r2, None))
    assert ends(err) == {"R2": 3, "B": 1} and all(m.advanced_on_error for m in err.moves)
    with pytest.raises(ValueError):
        br.bunt("sacrifice", batter, "P", (None, None, r1))  # 三塁に走者がいるときはしない


def test_bunt_probability_follows_the_situation_and_strategy():
    m = SimpleManager()
    weak = SimpleNamespace(ratings={k: 40.0 for k in ("contact", "eye", "power", "batted_ball_quality")})
    strong = SimpleNamespace(ratings={k: 60.0 for k in ("contact", "eye", "power", "batted_ball_quality")})
    base = BuntSituation(inning=3, outs=0, first=True, second=False, lead=0, order=8)
    p = m.bunt_probability(base, weak)
    assert 0 < p < 1
    assert m.bunt_probability(base, weak, "many") > p > m.bunt_probability(base, weak, "few")
    assert m.bunt_probability(base, strong) < p  # 打撃の強い打者はしない
    assert m.bunt_probability(BuntSituation(3, 0, True, False, 0, 4), weak) < p  # 中軸はほとんどしない
    assert m.bunt_probability(BuntSituation(8, 0, True, False, -1, 8), weak) > p  # 終盤の接戦は多い
    assert m.bunt_probability(BuntSituation(3, 0, True, False, 6, 8), weak) < p  # 大差は少ない
    assert m.bunt_probability(BuntSituation(3, 2, True, False, 0, 8), weak) == 0  # 二死はしない
    assert m.bunt_probability(BuntSituation(3, 0, False, False, 0, 8), weak) == 0  # 走者なし


def test_bunt_skill_changes_success(mid):
    model = default_model()
    cfg = mid.state.season.game_config["bunt"]
    p = next(x for x in mid.state.season.players.values() if x.role == "batter")
    plus, minus = SimpleNamespace(**{**vars(p), "skills": ["bunt_plus"]}), SimpleNamespace(**{**vars(p), "skills": ["bunt_minus"]})
    good, bad = model.bunt_probabilities(plus, cfg), model.bunt_probabilities(minus, cfg)
    assert good["sacrifice"] > bad["sacrifice"] and abs(sum(good.values()) - 1) < 1e-9
    pa = model.resolve_bunt(p, p, BaseOutState(0, True), random.Random(1), cfg).pa
    assert pa.fielder in ("P", "C", "1B", "3B")


def test_bunts_are_recorded_and_consistent(mid):
    bunts = _bunts(mid)
    assert len(bunts) > 100
    kinds = Counter("sac" if x.sac_bunt else x.pa.result for x in bunts)
    assert kinds["sac"] > 0.6 * len(bunts) and set(kinds) <= {"sac", "fly_out", "ground_out", "single", "error"}
    assert all(x.base_out.outs < 2 and not x.base_out.third and (x.base_out.first or x.base_out.second) for x in bunts)
    for x in bunts:
        if x.sac_bunt:
            assert x.outs_made == 1 and all(m.end == m.start + 1 for m in x.moves if m.start > 0)
    results = [p.result for p in mid.state.season.played]
    rec = season_records(results)
    assert all(ok for _, ok, _ in consistency_checks(rec, results))  # 打数 = 打席 − 四球 − 死球 − 犠牲フライ − 犠打 など
    assert sum(c["SH"] for c in rec.batters.values()) == sum(1 for x in bunts if x.sac_bunt)
    assert describe(next(x for x in bunts if x.sac_bunt)).endswith("犠打")
    b = mid.stats("batter", "basic", league=0)
    assert "SH" in [c["key"] for c in b["columns"]]


# ---- 練習の重点・起用の方針(D-455、D-456) ----

def test_practice_weights_keep_the_total():
    gains = {"meet": 1.0, "power": 2.0, "speed": 0.5, "arm": 0.5, "defense": 0.5, "catch": 0.5}
    w = policymod.practice_weights({"practice_batter": "meet"}, "batter", gains)
    assert w["meet"] == pytest.approx(1.2) and w["power"] < 1
    assert sum(v * w[k] for k, v in gains.items()) == pytest.approx(sum(gains.values()))
    w4 = policymod.practice_weights({"practice_batter": "defense_running"}, "batter", gains)
    assert w4["speed"] == pytest.approx(1.2) and w4["meet"] >= 0.5
    assert policymod.practice_weights({"practice_batter": "balance"}, "batter", gains) == {}
    wp = policymod.practice_weights({"practice_pitcher": "control_breaking"}, "pitcher", {"velocity": 1.0, "control": 1.0, "stamina": 1.0, "breaking": 1.0})
    assert wp["control"] == wp["breaking"] == pytest.approx(1.2) and wp["velocity"] == pytest.approx(0.8)


def test_practice_focus_moves_growth():
    from pennant.config import load_generation_config
    from pennant.newgame import new_league
    from pennant.offseason import age_and_update

    cfg = load_generation_config()
    league = new_league(3, gen_config=cfg, prerun=False)
    young = [p for p in league.all_players() if p.role == "batter" and p.age <= 22][:20]
    import copy

    totals = {}
    for key in ("balance", "power"):
        gain = 0.0
        for p in young:
            q = copy.deepcopy(p)
            ch = age_and_update(q, cfg, random.Random(1), policy={"practice_batter": key})
            gain += ch["power"]
        totals[key] = gain
    assert totals["power"] > totals["balance"]


def test_usage_policy_changes_the_roster_score(mid):
    s = mid.state.season
    team = s.teams["T03"]
    young = next(p for p in team.players if p.age <= 25)
    old = team.policy
    try:
        team.policy = {"usage": "standard"}
        std = s._roster_score("T03")(young)
        team.policy = {"usage": "youth"}
        assert s._roster_score("T03")(young) == pytest.approx(std + 3)
        cur = next(p for p in s.actives["T03"].players if p.age > 25)
        team.policy = {"usage": "standard"}
        a = s._roster_score("T03")(cur)
        team.policy = {"usage": "win"}
        assert s._roster_score("T03")(cur) == pytest.approx(a - 4)  # 勝ちにいくは今の一軍の有利を足さない
    finally:
        team.policy = old


# ---- 画面の操作と AI の決め方(D-454) ----

def test_ai_decides_all_teams_and_you_can_change_any_team(mid):
    g = _fresh(mid)
    pols = [policymod.policy_of(t) for t in g.state.league.teams]
    assert all(not p["manual"] for p in pols) and Counter(p["usage"] for p in pols)["win"] == 4
    view = g.team("T07")["policy"]
    assert [f["key"] for f in view["fields"]] == ["practice_batter", "practice_pitcher", "usage", "steal", "bunt"] and not view["manual"]
    v = g.set_team_policy("T07", {"bunt": "many", "practice_batter": "power"})
    assert v["manual"] and {f["key"]: f["value"] for f in v["fields"]}["bunt"] == "many" and g.dirty
    policymod.refresh_ai(g.state.league)  # 年度の確定で AI が決め直しても、あなたが決めた方針は変わらない
    assert policymod.policy_of(g._team("T07"))["practice_batter"] == "power"
    with pytest.raises(ValueError, match="バントの作戦"):
        g.set_team_policy("T07", {"bunt": "always"})
    back = g.reset_team_policy("T07")
    assert not back["manual"]
    assert set(policymod.SimplePolicyMaker().decide(g.state.league)["T07"].items()) <= set(policymod.policy_of(g._team("T07")).items())


def test_bunt_strategy_changes_attempts(mid):
    counts = {}
    for bunt in ("many", "few"):
        g = _fresh(mid)
        g.set_team_policy("T01", {"bunt": bunt})
        before = len(g.state.season.played)
        g.advance(30)
        counts[bunt] = sum(1 for p in g.state.season.played[before:] for x in p.result.log if x.bunt and x.batting_team_id == "T01")
    assert counts["many"] > counts["few"] * 2


# ---- 保存と再現(D-461) ----

def test_save_load_continues_the_same_and_replays_bunts(mid):
    g = _fresh(mid)
    g.set_team_policy("T02", {"bunt": "many", "steal": "aggressive", "usage": "youth"})
    a, b = _fresh(g), _fresh(g)
    assert policymod.policy_of(a._team("T02"))["manual"] and SAVE_FORMAT_VERSION == 32
    a.advance(10)
    b = _fresh(b)
    b.advance(10)
    key = lambda x: [(p.scheduled.number, p.result.home_runs, p.result.away_runs, sum(y.bunt for y in p.result.log)) for p in x.state.season.played]
    assert key(a) == key(b)
    s = a.state.season
    for p in s.played[-60::7]:
        r = s.replay_game(p.scheduled.number)
        assert [(x.batter_id, x.pa.result, x.bunt, x.sac_bunt) for x in r.log] == [(x.batter_id, x.pa.result, x.bunt, x.sac_bunt) for x in p.result.log]


def _as_version_31(data: bytes, steal: str = "aggressive") -> bytes:
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            files[name] = z.read(name)
    m = json.loads(files["manifest.json"])
    m["format_version"] = 31
    st = json.loads(files["state.json"])
    st["configs"]["game"].pop("bunt", None)
    st["season"].pop("bunt_start", None)
    for t in st["league"]["teams"]:
        t.pop("policy", None)
    st["season"]["lineup_plans"] = {"T01": {"style": "power", "slots": None, "steal": steal}}
    files["manifest.json"] = json.dumps(m).encode()
    files["state.json"] = json.dumps(st, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, b in files.items():
            z.writestr(name, b)
    return buf.getvalue()


def test_version_31_save_reads_standard_policies_and_moves_steal(mid):
    state = load_game(_as_version_31(save_game(mid.state)))
    s = state.season
    pols = {t.id: policymod.policy_of(t) for t in state.league.teams}
    assert pols["T01"] == {**policymod.DEFAULT, "steal": "aggressive", "manual": True}  # 盗塁の作戦は方針へ移す
    assert all(p == {**policymod.DEFAULT, "manual": False} for tid, p in pols.items() if tid != "T01")
    assert "steal" not in s.lineup_plans["T01"] and s.bunt_start == s.day == 30 and s.game_config.get("bunt") == load_game_config().get("bunt")
    early = s.played[3]
    r = s.replay_game(early.scheduled.number)  # 読み込んだ日より前の試合は、バントなしで再現する(この見本は新しいコードで進めたので、結果の比べはしない)
    assert not any(x.bunt for x in r.log)
    s.play_days(5)
    assert any(x.bunt for p in s.played[-30:] for x in p.result.log)


def test_reproducible_with_the_same_seed():
    runs = []
    for _ in range(2):
        g = api.Game.new(9, [None] * 12, None, season_seed=4, baselines="default")
        g.advance(8)
        runs.append([(x.batter_id, x.pa.result, x.bunt) for p in g.state.season.played for x in p.result.log])
    assert runs[0] == runs[1] and any(b for *_, b in runs[0])


@pytest.mark.slow
def test_sacrifice_count_per_season_is_in_range():
    """「普通」で 1 球団 1 シーズンに犠打 80〜100 前後(D-458)。全球団の作戦を普通にして 1 シーズン。"""
    g = api.Game.new(5, [None] * 12, None, season_seed=6, baselines="default")
    for t in g.state.league.teams:
        t.policy = {**policymod.policy_of(t), "bunt": "normal"}
    g.state.season.play_to_end()
    sh = Counter(x.batting_team_id for p in g.state.season.played for x in p.result.log if x.sac_bunt)
    mean = sum(sh.values()) / 12
    assert 70 <= mean <= 110, mean
