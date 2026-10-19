"""盗塁・暴投・捕逸(D-427)、特殊能力(D-428)、盗塁の作戦(D-429)、記録と WAR(D-430)、守備の表(D-431)、保存(D-433)。"""

import io
import json
import random
import zipfile
from collections import Counter

import pytest

from pennant import api
from pennant import skills as skill_mod
from pennant.game import CAUGHT, PASSED_BALL, STEAL, WILD_PITCH, simulate_game, steps
from pennant.game_config import default_game_data, load_game_config, validate_game_config
from pennant.records import earned_flags, season_records
from pennant.runvalues import league_totals, season_player_runs
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game
from pennant.stats_report import consistency_checks


@pytest.fixture(scope="module")
def mid():
    """自球団 T01 で 40 日進めた状態。"""
    g = api.Game.new(5, [None] * 12, 0, season_seed=5, baselines="default")
    g.advance(40)
    return g


def test_events_are_recorded_and_consistent(mid):
    s = mid.state.season
    results = [p.result for p in s.played]
    kinds = Counter(e.kind for r in results for x in r.log for e in x.events)
    assert set(kinds) == {STEAL, CAUGHT, WILD_PITCH, PASSED_BALL} and kinds[STEAL] > kinds[CAUGHT] > 0 and kinds[WILD_PITCH] > kinds[PASSED_BALL]
    rec = season_records(results)
    assert all(ok for _, ok, _ in consistency_checks(rec, results))  # 得点・失点・アウトの合計が合う(盗塁死のアウト・暴投の得点も)
    assert sum(c["SB"] for c in rec.batters.values()) == kinds[STEAL] and sum(c["WP"] for c in rec.pitchers.values()) == kinds[WILD_PITCH]
    catchers = {pid: by["C"] for pid, by in rec.fielders.items() if "C" in by}
    assert sum(c["SBA"] for c in catchers.values()) == kinds[STEAL] + kinds[CAUGHT] and sum(c["PB"] for c in catchers.values()) == kinds[PASSED_BALL]
    for r in results:  # 盗塁は 1 人が 1 つ進むかアウト。暴投・捕逸は全員 1 つ進む。捕逸の生還は自責点にしない
        for e, row in zip(steps(r), earned_flags(r)):
            if not hasattr(e, "kind"):
                continue
            if e.kind in (STEAL, CAUGHT):
                assert len(e.moves) == 1 and (e.moves[0].end == e.moves[0].start + 1) == (e.kind == STEAL) and e.outs_made == (e.kind == CAUGHT)
            else:
                assert all(m.end == min(m.start + 1, 4) for m in e.moves)
                if e.kind == PASSED_BALL:
                    assert not any(row)
    totals = league_totals(season_player_runs(results, mid.baselines()[0]))
    assert abs(float(totals["fielding"])) < 1e-9  # 捕手の守備(盗塁・捕逸)もリーグ全体で 0


def test_replay_and_no_events_without_settings(mid):
    s = mid.state.season
    for p in s.played[::40]:
        r = s.replay_game(p.scheduled.number)
        assert (r.home_runs, r.away_runs, sum(len(x.events) for x in r.log)) == (p.result.home_runs, p.result.away_runs, sum(len(x.events) for x in p.result.log))
    data = default_game_data()
    for key in ("steal", "battery"):
        data.pop(key)
    cfg = validate_game_config(data)
    t = s.league.teams
    setups = [s.manager.prepare(team, random.Random(1), 0, s.actives[team.id])[0] for team in t[:2]]
    r = simulate_game(setups[0], setups[1], random.Random(3), model=s.model, config=cfg, manager=s.manager)
    assert not any(x.events for x in r.log)  # 設定がなければ盗塁などは起きない(版 25 以前の試合の再現)


def test_strategy_changes_attempts():
    base = api.Game.new(5, [None] * 12, 0, season_seed=7, baselines="default")
    counts = {}
    for steal in ("aggressive", "cautious"):
        g = api.Game(load_game(save_game(base.state)), dirty=False)
        assert g.set_steal(steal)["steal"] == steal
        g.advance(50)
        rec = season_records([p.result for p in g.state.season.played])
        mine = {p.id for p in g._team("T01").players}
        counts[steal] = sum(c["SB"] + c["CS"] for pid, c in rec.batters.items() if pid in mine)
        again = api.Game(load_game(save_game(g.state)), dirty=False)
        assert again.lineup_view()["steal"] == steal  # 作戦は保存される
    assert counts["aggressive"] > counts["cautious"] * 1.5
    with pytest.raises(ValueError):
        base.set_steal("always")


def test_skills_for_steal_and_catcher():
    settings = skill_mod.default_settings()
    for key in ("steal_plus", "steal_minus", "catcher_plus", "catcher_minus"):
        assert key in settings.skills
    g = api.Game.new(5, [None] * 12, 0, season_seed=5, baselines="default")
    players = list(g.state.season.players.values())
    holders = [p for p in players if {"catcher_plus", "catcher_minus"} & set(p.skills or [])]
    assert holders and all(p.position == "C" for p in holders)  # キャッチャー◯/× は捕手だけ
    c = next(p for p in holders if "catcher_plus" in p.skills)
    assert skill_mod.lead_bonus(c) == 2 and skill_mod.catching_effects(c) == {"arm": 10.0, "fielding": 10.0}
    assert any("steal_plus" in (p.skills or []) for p in players)


def test_fielding_table_and_stats_columns(mid):
    d = mid.fielding_stats("C", league=0)
    assert [c["label"] for c in d["columns"]] == ["試合", "守備イニング", "失策", "守備の得点", "盗塁企図", "盗塁阻止", "盗塁阻止率", "捕逸"]
    team_games = {t: c["G"] for t, c in mid.records.total.teams.items()}
    assert d["rows"] and all(int(r["values"]["G"]) * 2 >= team_games[r["team_id"]] for r in d["rows"])  # 規定:チームの試合数の半分以上
    assert all(mid._team(r["team_id"]).league_index == 0 for r in d["rows"])
    a = mid.fielding_stats("all", qualified=False, league=1, sort="E", order="desc")
    assert a["columns"][0]["label"] == "主なポジション" and int(a["rows"][0]["values"]["E"]) >= int(a["rows"][-1]["values"]["E"])
    b = mid.stats("batter", "basic", sort="SB", league=0)
    assert "SB" in [c["key"] for c in b["columns"]] and int(b["rows"][0]["values"]["SB"]) > 0
    assert "WP" in [c["key"] for c in mid.stats("pitcher", "basic", league=0)["columns"]]
    with pytest.raises(ValueError):
        mid.fielding_stats("DH")


def test_version_25_save_starts_running_at_load_day(mid):
    data = save_game(mid.state)
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            files[name] = z.read(name)
    m = json.loads(files["manifest.json"])
    m["format_version"] = 25
    st = json.loads(files["state.json"])
    st["season"].pop("running_start", None)
    for key in ("steal", "battery"):
        st["configs"]["game"].pop(key, None)
    files["manifest.json"] = json.dumps(m).encode()
    files["state.json"] = json.dumps(st, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, b in files.items():
            z.writestr(name, b)
    state = load_game(buf.getvalue())
    s = state.season
    assert SAVE_FORMAT_VERSION == 31 and s.running_start == 40 and s.game_config.get("steal") == load_game_config().get("steal")
    s.play_days(5)
    assert any(x.events for p in s.played[-30:] for x in p.result.log)
