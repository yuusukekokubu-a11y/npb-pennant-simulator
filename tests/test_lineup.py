"""スタメンと打順の操作(D-373):打順の型・自分で組んだスタメン・守備位置の不利・見直し・試合の再現・保存。"""

import copy
import json
import random

import pytest

from pennant import api
from pennant.game_config import load_game_config
from pennant.manager import DH, LINEUP_POSITIONS, LINEUP_STYLES, SimpleManager
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game


@pytest.fixture(scope="module")
def base():
    return api.Game.new(3, [None] * 12, 0, season_seed=5, baselines="default")


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def slots_of(v):
    return [{"player_id": s["player_id"], "position": s["position"]} for s in v["slots"]]


def test_styles_make_different_orders_and_power_is_the_default(base):
    g = fresh(base)
    v = g.lineup_view()
    assert v["style"] == "power" and v["source"] == "ai" and len(v["slots"]) == 9 and len(v["bench"]) == 6
    assert [s["position"] for s in v["slots"]].count(DH) == 1 and sorted(s["position"] for s in v["slots"]) == sorted(LINEUP_POSITIONS)
    assert [x["key"] for x in v["styles"]] == list(LINEUP_STYLES)
    # 長打重視は今までの AI の組み方そのまま(AI 球団と同じ)
    season = g.state.season
    m = season.manager
    active = season.actives["T01"]
    assert [p.id for p, _ in m.planned_order(active, None)] == [p.id for p, _ in m.batting_order(m.regular_lineup(active))]
    orders = {}
    for style in LINEUP_STYLES:
        v = g.set_lineup_style(style)
        assert v["style"] == style and v["source"] == "ai"
        orders[style] = [s["player_id"] for s in v["slots"]]
        assert sorted(orders[style]) == sorted(orders["power"])  # 顔ぶれは同じ(各ポジションで最も強い野手 + 指名打者)
    assert len({tuple(o) for o in orders.values()}) >= 2  # 型によって打順が変わる(何通りになるかは選手しだい。D-373)
    # 二番強打者:2 番は打撃の総合がいちばん高い打者
    nine = m.regular_lineup(active)
    best = max(nine, key=lambda x: sum(x[0].ratings[k] for k in ("contact", "eye", "power", "batted_ball_quality")))
    assert orders["slugger_second"][1] == best[0].id
    with pytest.raises(ValueError, match="打順の型"):
        g.set_lineup_style("bunt")


def test_custom_lineup_validation_and_position_penalty(base):
    g = fresh(base)
    v = g.lineup_view()
    s = slots_of(v)
    s[0], s[3] = s[3], s[0]
    catcher_bench = next((b for b in v["bench"] if b["position"] == "C"), None) or next(b for b in v["bench"] if b["position"] != "SS")  # 控えの捕手(いなければ遊撃手でない控え)
    dh = next(i for i, x in enumerate(s) if x["position"] == DH)
    ss = next(i for i, x in enumerate(s) if x["position"] == "SS")
    s[dh]["player_id"], s[ss]["player_id"] = s[ss]["player_id"], catcher_bench["player_id"]  # 控えの選手を遊撃手に
    v2 = g.set_lineup(s)
    assert v2["source"] == "user" and [x["player_id"] for x in v2["slots"]] == [x["player_id"] for x in s]
    row = v2["slots"][ss]
    from pennant.abilities import POSITION_LABELS

    assert row["penalty"] in (5, 10) and f"本来は{POSITION_LABELS[catcher_bench['position']]}" in row["warning"]  # 近いポジションは −5、遠いポジションは −10
    if catcher_bench["position"] == "C":
        assert row["penalty"] == 10
    assert v2["slots"][dh]["penalty"] == 0  # 指名打者は下げない
    # 試合で使う顔ぶれ:守備の能力が下がった写し
    season = g.state.season
    m = season.manager
    order = m.planned_order(season.actives["T01"], season.lineup_plans["T01"])
    penal = order[ss][0]
    orig = next(p for p in season.actives["T01"].batters if p.id == catcher_bench["player_id"])
    assert penal.id == orig.id and penal.ratings["range"] < orig.ratings["range"]
    # 休養なし(確率 0)なら、その打順のまま試合に出る
    cfg = copy.deepcopy(load_game_config())
    cfg.data["rest"]["probability"] = cfg.data["rest"]["catcher_probability"] = 0.0
    lineup = SimpleManager(cfg).starting_lineup(season.actives["T01"], random.Random(1), season.lineup_plans["T01"])
    assert [x.player.id for x in lineup.slots] == [x["player_id"] for x in s] and [x.position for x in lineup.slots] == [x["position"] for x in s]
    # 不正な入力は理由つきで断る
    bad = copy.deepcopy(s)
    bad[1]["position"] = bad[0]["position"]
    with pytest.raises(ValueError, match="守備位置が足りません"):
        g.set_lineup(bad)
    bad = copy.deepcopy(s)
    bad[1]["player_id"] = bad[0]["player_id"]
    with pytest.raises(ValueError, match="同じ選手"):
        g.set_lineup(bad)
    bad = copy.deepcopy(s)
    bad[2]["player_id"] = next(p.id for p in g._team("T01").players if p.role == "pitcher")
    with pytest.raises(ValueError, match="一軍の野手"):
        g.set_lineup(bad)
    with pytest.raises(ValueError, match="9 人"):
        g.set_lineup(s[:8])
    text = json.dumps(v2, ensure_ascii=False)
    for key in ("ratings", "growth_type", "ease", "archetype", "trajectory_bias"):
        assert f'"{key}"' not in text


def test_review_resets_to_ai_and_replay_and_save(base):
    g = fresh(base)
    v = g.lineup_view()
    s = slots_of(v)
    s[0], s[8] = s[8], s[0]
    g.set_lineup_style("speed")
    v = g.set_lineup(s)
    assert v["next_review_day"] == 8 and "8日目の見直しまで" in v["source_text"]
    g.advance(3)
    season = g.state.season
    mine = [pg for pg in season.played if "T01" in (pg.result.home_team_id, pg.result.away_team_id)]
    assert mine and all(pg.context.lineups["T01"]["slots"] == [[x["player_id"], x["position"]] for x in s] for pg in mine)
    for pg in mine:  # 自分の打順の試合も、試合のページで同じに再現できる
        again = season.replay_game(pg.scheduled.number)
        assert (again.home_runs, again.away_runs, len(again.log)) == (pg.result.home_runs, pg.result.away_runs, len(pg.result.log))
    # 保存と読み込み(版 17)で、計画と試合ごとの記録が残る
    loaded = api.Game(load_game(save_game(g.state)), dirty=False)
    assert SAVE_FORMAT_VERSION == 28
    assert loaded.state.season.lineup_plans["T01"]["slots"] == [[x["player_id"], x["position"]] for x in s] and loaded.state.season.lineup_plans["T01"]["style"] == "speed"
    assert [pg.context.lineups for pg in loaded.state.season.played] == [pg.context.lineups for pg in season.played]
    # 見直し(7 日ごと):8 日目の試合の前に、型で AI が組み直す
    g.advance(5)
    v = g.lineup_view()
    assert v["source"] == "ai" and v["style"] == "speed" and "8日目の見直し" in v["notice"]
    # 見直しの日に組んだばかりの打順は、その日には外さない
    g2 = fresh(base)
    g2.advance(7)
    v = g2.set_lineup(slots_of(g2.lineup_view()))
    assert v["next_review_day"] == 15
    g2.advance(1)
    assert g2.lineup_view()["source"] == "user"


def test_lineup_with_a_player_not_on_the_active_roster_is_rebuilt(base):
    g = fresh(base)
    s = slots_of(g.lineup_view())
    g.set_lineup(s)
    plan = g.state.season.lineup_plans["T01"]
    plan["slots"][4][0] = next(p.id for p in g._team("T01").players if p.role == "batter" and p.id not in {b.id for b in g.state.season.actives["T01"].batters})
    g.advance(1)
    v = g.lineup_view()
    assert v["source"] == "ai" and "一軍にいない選手" in v["notice"]


def test_lineup_needs_my_team_and_a_running_season():
    g = api.Game.new(1, [None] * 12, None, season_seed=2, baselines="default")
    with pytest.raises(ValueError, match="操作する球団"):
        g.lineup_view()


def test_bridge_has_lineup_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_lineup", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert set(bridge._LINEUP) == {"view", "set", "style", "auto", "steal"}  # 盗塁の作戦(D-429)


@pytest.mark.slow
def test_style_carries_over_to_the_next_season(base):
    g = fresh(base)
    g.set_lineup_style("contact_second")
    g.set_lineup(slots_of(g.lineup_view()))
    g.advance_to_end()
    assert g.lineup_view()["editable"] is False
    with pytest.raises(ValueError, match="シーズン中"):
        g.lineup_auto()
    g.year_end()
    g.offseason_auto()
    v = g.lineup_view()
    assert v["style"] == "contact_second" and v["source"] == "ai" and g.state.year == 2
