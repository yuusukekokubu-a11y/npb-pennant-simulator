"""調子(D-424)と一軍の入れ替え(D-425)と、その保存(D-426)。"""

import io
import json
import zipfile
from collections import Counter

import pytest

from pennant import api
from pennant.condition import LEVELS, Conditions, level_at, settings
from pennant.game_config import load_game_config
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

CFG = settings(load_game_config())


@pytest.fixture(scope="module")
def mid():
    """自球団 T01 で 20 日進めた状態。"""
    g = api.Game.new(5, [None] * 12, 0, season_seed=5, baselines="default")
    g.advance(20)
    return g


def test_condition_is_deterministic_and_mean_reverting():
    c = Conditions(11, CFG)
    ids = [f"P{i:04d}" for i in range(2000)]
    first = Counter(c.level(pid, 0) for pid in ids)
    assert set(first) <= set(LEVELS) and 0.33 < first[0] / len(ids) < 0.47  # 初めは普通が 4 割くらい
    later = [c.level(pid, 120) for pid in ids]
    assert later == [level_at(11, pid, 120, 0, CFG) for pid in ids]  # 少しずつ計算しても、数え直しても同じ
    assert 0.25 < sum(1 for x in later if x == 0) / len(ids) < 0.5 and abs(sum(later) / len(ids)) < 0.15  # 偏らない
    assert sum(1 for pid in ids if c.level(pid, 4) != c.level(pid, 0)) == 0  # 5 日たつまでは変わらない
    assert sum(1 for pid in ids if level_at(11, pid, 5, 0, CFG) != level_at(11, pid, 0, 0, CFG)) > 400  # 5 日ごとに変わる


def test_ai_review_changes_the_roster_and_keeps_the_lock(mid):
    s = mid.state.season
    hist = s.active_history["T02"]
    hurt_days = {(x["team_id"], x["start"] + 1) for x in s.injury_log}  # けがの次の日にも入れ替わる(D-445)
    hurt = {x["player_id"] for x in s.injury_log}
    assert len(hist) >= 2 and {h["day"] for h in hist[1:] if ("T02", h["day"]) not in hurt_days} <= {7, 14}  # 7 日ごとの見直しで変わる
    for tid, h in s.active_history.items():  # 外して 10 日たたない選手は、一軍に戻らない(けがで外れた選手は、治ればすぐ戻れる)
        for a, b in zip(h, h[1:]):
            out = set(a["starters"] + a["relievers"] + a["batters"]) - set(b["starters"] + b["relievers"] + b["batters"]) - hurt
            for later in h:
                if b["day"] < later["day"] < b["day"] + 10:
                    assert not out & set(later["starters"] + later["relievers"] + later["batters"])
    for t in s.league.teams:
        a = s.actives[t.id]
        assert len(a.players) == 29 and sum(1 for p in a.batters if p.position == "C") >= 2 and sum(1 for p in a.players if p.foreign) <= 4


def test_swap_rules_and_view(mid):
    g = api.Game(load_game(save_game(mid.state)), dirty=False)
    v = g.roster_view()
    assert v["auto"] and len(v["starters"]) + len(v["relievers"]) + len(v["batters"]) == 29 and v["farm"]
    assert all(r["condition"]["mark"] in "↑↗→↘↓" for r in v["farm"])
    pitcher_out = v["relievers"][-1]
    farm_p = next(r for r in v["farm"] if r["role"] == "pitcher" and r["lock_days"] == 0)
    farm_b = next(r for r in v["farm"] if r["role"] == "batter" and r["lock_days"] == 0)
    with pytest.raises(ValueError, match="投手は投手と"):
        g.roster_swap(pitcher_out["player_id"], farm_b["player_id"])
    catchers = [r for r in v["batters"] if r["position"] == "C"]
    if len(catchers) == 2 and farm_b["position"] != "C":
        with pytest.raises(ValueError, match="捕手"):
            g.roster_swap(catchers[0]["player_id"], farm_b["player_id"])
    v = g.roster_swap(pitcher_out["player_id"], farm_p["player_id"])
    assert farm_p["player_id"] in {r["player_id"] for r in v["relievers"]} and v["recent"]["in"] == [farm_p["name"]]
    back = next(r for r in v["farm"] if r["player_id"] == pitcher_out["player_id"])
    assert back["lock_days"] == 10
    with pytest.raises(ValueError, match="戻せません"):
        g.roster_swap(farm_p["player_id"], pitcher_out["player_id"])
    g.roster_set_auto(False)
    data = save_game(g.state)
    again = api.Game(load_game(data), dirty=False)
    assert again.roster_view()["auto"] is False and again.state.season.demoted[pitcher_out["player_id"]] == g.state.season.day
    g.advance(10)
    again.advance(10)
    assert [p.result.home_runs for p in g.state.season.played] == [p.result.home_runs for p in again.state.season.played]  # 保存して読んでも同じ続き
    assert farm_p["player_id"] in {p.id for p in again.state.season.actives["T01"].players}  # AI に任せていないので、そのまま
    n = g.state.season.played[-1].scheduled.number
    r = g.state.season.replay_game(n)
    assert (r.home_runs, r.away_runs) == (g.state.season.played[-1].result.home_runs, g.state.season.played[-1].result.away_runs)  # 入れ替えと調子を含めて再現できる


def test_version_24_save_is_read_with_the_old_roster(mid):
    data = save_game(mid.state)
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            files[name] = z.read(name)
    m = json.loads(files["manifest.json"])
    m["format_version"] = 24
    st = json.loads(files["state.json"])
    for key in ("condition", "demoted", "roster_auto", "active_history", "running_start"):
        st["season"].pop(key, None)
    files["manifest.json"] = json.dumps(m).encode()
    files["state.json"] = json.dumps(st, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, b in files.items():
            z.writestr(name, b)
    state = load_game(buf.getvalue())
    s = state.season
    assert SAVE_FORMAT_VERSION == 34 and s.legacy_roster and s.conditions.start == 20 and s.legacy_form and s.running_start == 20  # 版 25 への変換で、盗塁なども読んだ日から(D-433)
    s.play_days(6)
    assert s.day == 26


def test_condition_and_roster_settings_are_checked():
    from pennant.config import ConfigError
    from pennant.game_config import default_game_data, validate_game_config

    g = load_game_config()
    assert settings(g)["period_days"] == 5 and g.get("roster")["lock_days"] == 10  # data/game.json から読む
    bad = default_game_data()
    bad["condition"]["initial"] = [0.5, 0.5]
    bad["roster"]["lock_days"] = -1
    with pytest.raises(ConfigError) as e:
        validate_game_config(bad)
    assert "condition.initial" in str(e.value) and "roster.lock_days" in str(e.value)
