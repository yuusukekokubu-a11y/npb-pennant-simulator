"""投手の起用の操作(D-449〜D-452):先発ローテーションの順と救援の役割、抜けた枠の組み直し、保存と再現。"""

import io
import json
import zipfile
from collections import Counter
from types import SimpleNamespace

import pytest

from pennant import api
from pennant.ability_model import abilities_for
from pennant.manager import RELIEF_ROLES, ActiveRoster
from pennant.pitching_plan import arrange
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game


def _p(pid: str, ov: float, pos: str = "RP"):
    """総合が ov になる投手(arrange が使うのは ID と総合だけ)。"""
    return SimpleNamespace(id=pid, role="pitcher", position=pos, abilities={k: ov for k in abilities_for("pitcher")})


def _active(starters, relievers) -> ActiveRoster:
    return ActiveRoster(starters=list(starters), relievers=list(relievers), relief_roles={}, batters=[])


@pytest.fixture(scope="module")
def mid():
    """自球団 T01 で 10 日進めた状態。"""
    g = api.Game.new(5, [None] * 12, 0, season_seed=5, baselines="default")
    g.advance(10)
    return g


def _fresh(mid) -> api.Game:
    return api.Game(load_game(save_game(mid.state)), dirty=False)


def _my_plan(g):
    v = g.roster_view()
    starters = [r["player_id"] for r in v["starters"]]
    roles = {r["player_id"]: r["role_label"] for r in v["relievers"]}
    return v, starters, roles


# ---- 抜けた枠の組み直し(計算だけ。D-450) ----

def test_starter_newcomer_takes_the_slot_only_if_better():
    s = [_p(f"S{i}", 60 - i, "SP") for i in range(6)]
    plan = {"starters": ["S5", "S4", "S3", "S2", "S1", "S0"], "roles": {}}  # 弱い順に並べた計画
    strong, weak = _p("N1", 90, "SP"), _p("N2", 10, "SP")
    rest = s[:3] + s[4:]  # 3 番手の S3 が抜ける
    a = arrange(dict(plan), _active([strong] + rest, []), 3)  # 強い投手は、抜けた番手に入る
    assert [p.id for p in a.starters] == ["S5", "S4", "N1", "S2", "S1", "S0"]
    b = arrange(dict(plan), _active([weak] + rest, []), 3)  # 弱い投手は、下の先発を上回らなければ最後
    assert [p.id for p in b.starters] == ["S5", "S4", "S2", "S1", "S0", "N2"]
    plan2 = {"starters": ["S0", "S1", "S2", "S3", "S4", "S5"], "roles": {}}  # 強い順の計画から 2 番手の S1(59)が抜ける
    c = arrange(plan2, _active([_p("N3", 57.5, "SP"), s[0]] + s[2:], []), 3)  # S2(58)より下、S3(57)より上
    assert [p.id for p in c.starters] == ["S0", "S2", "N3", "S3", "S4", "S5"] and plan2["starters"] == [p.id for p in c.starters]


def test_closer_leaves_setup_moves_up_and_middle_fills_setup():
    r = {k: _p(k, v) for k, v in {"C": 80, "U1": 70, "U2": 72, "M1": 60, "M2": 65, "M3": 50, "L1": 40, "L2": 45}.items()}
    roles = {"C": "closer", "U1": "setup", "U2": "setup", "M1": "middle", "M2": "middle", "M3": "middle", "L1": "mopup", "L2": "mopup"}
    plan = {"starters": [], "roles": dict(roles)}
    new = _p("N", 55)
    a = arrange(plan, _active([], [new] + [r[k] for k in roles if k != "C"]), 12)
    assert a.relief_roles["U2"] == "closer"  # 勝ちパターンの一番手(総合の高い方)が抑えに
    assert a.relief_roles["U1"] == "setup" and a.relief_roles["M2"] == "setup"  # 勝ちパターンの空きは中継ぎの一番手から
    assert [a.relief_roles[k] for k in ("M1", "M3", "N")] == ["middle"] * 3 and a.relief_roles["L1"] == a.relief_roles["L2"] == "mopup"
    assert Counter(a.relief_roles.values()) == Counter(roles.values())  # 役割ごとの人数は計画のまま
    assert plan["roles"] == a.relief_roles and plan["notice"] == {"day": 12, "out": ["C"], "in": ["N"]}
    again = arrange(plan, a, 13)  # 同じ一軍に 2 回通しても変わらない(知らせも増えない)
    assert again.relief_roles == a.relief_roles and plan["notice"]["day"] == 12


def test_role_counts_can_be_zero_and_closer_is_always_one():
    r = [_p(f"R{i}", 50 + i) for i in range(8)]
    plan = {"starters": [], "roles": {"R0": "closer", **{f"R{i}": "middle" for i in range(1, 8)}}}  # 勝ちパターン・敗戦処理 0 人
    a = arrange(plan, _active([], r[1:] + [_p("N", 99)]), 1)
    assert Counter(a.relief_roles.values()) == {"closer": 1, "middle": 7} and a.relief_roles["N"] == "closer"  # 上がってきた投手が最も高ければ抑えに


# ---- 操作と画面(D-449、D-451) ----

def test_set_plan_and_ai_back(mid):
    g = _fresh(mid)
    v, starters, roles = _my_plan(g)
    assert v["pitching"]["source"] == "ai" and [x["key"] for x in v["pitching"]["roles"]] == list(RELIEF_ROLES)
    assert [x["label"] for x in v["pitching"]["roles"]] == ["抑え", "勝ちパターン", "中継ぎ", "敗戦処理"]
    s = g.state.season
    nxt = s.rotation["T01"]
    new_order = starters[::-1]
    closer = next(k for k, x in roles.items() if x == "closer")
    other = next(k for k, x in roles.items() if x == "mopup")
    new_roles = {k: "middle" for k in roles} | {other: "closer"}
    v2 = g.roster_set_pitching(new_order, new_roles)
    assert v2["pitching"]["source"] == "user" and [r["player_id"] for r in v2["starters"]] == new_order
    assert {r["player_id"]: r["role_label"] for r in v2["relievers"]} == new_roles and s.actives["T01"].relief_roles[closer] == "middle"
    assert s.rotation["T01"] == nxt and v2["pitching"]["next_index"] == nxt % 6  # 次に投げる番手は変えない
    assert s.active_history["T01"][-1]["starters"] == new_order and s.active_history["T01"][-1]["roles"] == new_roles  # 試合の再現用の記録に残る
    with pytest.raises(ValueError, match="抑えは 1 人"):
        g.roster_set_pitching(new_order, {k: "middle" for k in roles})
    with pytest.raises(ValueError, match="先発ローテーション"):
        g.roster_set_pitching(new_order[:5], new_roles)
    with pytest.raises(ValueError, match="救援の役割"):
        g.roster_set_pitching(new_order, {**new_roles, other: "ace"})
    g.advance(8)  # 7 日目の見直し(AI に任せる)を越えても、自分で決めた起用は続く(顔ぶれが変わった枠だけ組み直す)
    plan = s.pitching_plans["T01"]
    a = s.actives["T01"]
    assert [p.id for p in a.starters] == plan["starters"] and a.relief_roles == plan["roles"] and sum(1 for x in plan["roles"].values() if x == "closer") == 1
    kept = [x for x in new_order if x in plan["starters"]]
    assert [x for x in plan["starters"] if x in kept] == kept  # 残った先発の順は変わらない
    v3 = g.roster_pitching_auto()
    assert v3["pitching"]["source"] == "ai" and "T01" not in s.pitching_plans
    assert s.actives["T01"].relief_roles == s.manager._relief_roles(s.actives["T01"].relievers)


def test_ai_back_restores_the_ai_arrangement_and_survives_save(mid):
    """「AI に戻す」は、AI が決めていた起用(先発の順と役割)にそのまま戻る。保存して読み込んでも同じ。"""
    g = _fresh(mid)
    s = g.state.season
    before = s.actives["T01"]
    ai = ([p.id for p in before.starters], dict(before.relief_roles))
    _, starters, roles = _my_plan(g)
    g.roster_set_pitching(starters[::-1], {k: ("closer" if v == "mopup" and k == min(x for x, y in roles.items() if y == "mopup") else "middle") for k, v in roles.items()})
    other = api.Game(load_game(save_game(g.state)), dirty=False)
    for game in (g, other):
        game.roster_pitching_auto()
        a = game.state.season.actives["T01"]
        assert ([p.id for p in a.starters], a.relief_roles) == ai


def test_plan_is_rearranged_after_swap_with_notice(mid):
    g = _fresh(mid)
    _, starters, roles = _my_plan(g)
    g.roster_set_pitching(starters, roles)
    s = g.state.season
    closer = next(k for k, x in roles.items() if x == "closer")
    v = g.roster_view()
    farm_p = next(r for r in v["farm"] if r["role"] == "pitcher" and not r["injury"] and r["lock_days"] == 0)
    g.roster_swap(closer, farm_p["player_id"])
    plan = s.pitching_plans["T01"]
    assert closer not in plan["roles"] and farm_p["player_id"] in plan["roles"] and sum(1 for x in plan["roles"].values() if x == "closer") == 1
    setups = [k for k, x in roles.items() if x == "setup"]
    new_closer = next(k for k, x in plan["roles"].items() if x == "closer")
    assert new_closer in setups or new_closer == farm_p["player_id"]
    text = g.roster_view()["pitching"]["notice"]
    assert f"{s.day + 1}日目から" in text and s.players[closer].name in text and farm_p["name"] in text
    assert g.status()["pitching_notice"] == text  # 進行の画面にも出る(直近 7 日)


# ---- 保存と再現(D-452) ----

def test_save_load_continue_and_replay_are_the_same(mid):
    g = _fresh(mid)
    _, starters, roles = _my_plan(g)
    order = starters[1:] + starters[:1]
    roles2 = dict(roles)
    a, b = [k for k, x in roles.items() if x == "closer"][0], [k for k, x in roles.items() if x == "middle"][0]
    roles2[a], roles2[b] = "middle", "closer"
    g.roster_set_pitching(order, roles2)
    data = save_game(g.state)
    manifest_plan = json.loads(_state_json(data))["season"]["pitching_plans"]["T01"]
    assert manifest_plan["starters"] == order and manifest_plan["roles"] == roles2
    other = api.Game(load_game(data), dirty=False)
    assert SAVE_FORMAT_VERSION == 32 and other.state.season.pitching_plans["T01"]["roles"] == roles2
    g.advance(6)
    other.advance(6)
    s1, s2 = g.state.season, other.state.season
    assert [p.scheduled.number for p in s1.played] == [p.scheduled.number for p in s2.played]
    assert [(p.result.home_runs, p.result.away_runs) for p in s1.played] == [(p.result.home_runs, p.result.away_runs) for p in s2.played]
    mine = [p for p in s1.played[-30:] if "T01" in (p.scheduled.home_id, p.scheduled.away_id)]
    assert mine
    for p in mine:
        again = s1.replay_game(p.scheduled.number)
        assert (again.home_runs, again.away_runs) == (p.result.home_runs, p.result.away_runs)
    used = {pid for p in mine for pid in p.result.batters_faced()}  # 自分で決めた起用で投げている
    assert s1.actives["T01"].starters[0].id in used or any(x in used for x in order)


def test_old_save_without_plan_is_ai(mid):
    data = save_game(mid.state)
    state = load_game(data)
    assert state.season.pitching_plans == {}


def _state_json(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("state.json").decode("utf-8")


def test_bridge_has_pitching_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_pitching", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"pitching", "pitching_auto"} <= set(bridge._ROSTER)


@pytest.mark.slow
def test_plan_carries_over_to_the_next_season(mid):
    g = _fresh(mid)
    _, starters, roles = _my_plan(g)
    g.roster_set_pitching(starters[::-1], roles)
    g.advance_to_end()
    with pytest.raises(ValueError, match="シーズン中"):
        g.roster_pitching_auto()
    last = list(g.state.season.pitching_plans["T01"]["starters"])  # シーズンの終わりの起用
    g.year_end()
    g.offseason_auto()
    s = g.state.season
    plan = s.pitching_plans["T01"]
    a = s.actives["T01"]
    assert g.state.year == 2 and g.roster_view()["pitching"]["source"] == "user"
    assert [p.id for p in a.starters] == plan["starters"] and a.relief_roles == plan["roles"] and sum(1 for x in plan["roles"].values() if x == "closer") == 1
    kept = [x for x in last if x in plan["starters"]]
    assert kept and [x for x in plan["starters"] if x in kept] == kept  # 引き継いだ先発の順は変わらない
