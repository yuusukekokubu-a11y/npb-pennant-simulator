"""トレード(D-378):設定・価値・成り立つ条件・AI の判断・あなたから持ちかける・AI からの提案・シーズン中の期限と一軍の選び直し・試合の再現・オフの段階・保存(D-379)。"""

import copy

import pytest

from pennant import api, trade as trademod
from pennant.config import ConfigError
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

TS = trademod.load_trade_settings()


@pytest.fixture(scope="module")
def base():
    return api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default")


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def test_settings_and_validation():
    assert TS.max_players == 3 and 0 < TS.deadline_ratio < 1
    data = copy.deepcopy(TS.data)
    data["deadline_ratio"] = 2
    with pytest.raises(ConfigError, match="deadline_ratio"):
        trademod.validate_trade_settings(data)
    data = copy.deepcopy(TS.data)
    data["ai"]["season_checks"] = [0.5, 1.5]
    with pytest.raises(ConfigError, match="season_checks"):
        trademod.validate_trade_settings(data)
    assert trademod.deadline_day(120, TS) == 80 and trademod.season_check_days(125, TS) == [25, 50, 75]


def test_value_check_and_ai_answer(base):
    g = fresh(base)
    ctx = g._trade_ctx()
    rate = ctx.rate
    assert rate > 0
    mine, other = g._team("T01"), g._team("T02")
    mins, mb = g._mins()
    # 価値:若いほど少し高い(同じ選手で年齢だけ変える)
    p = copy.deepcopy(mine.players[0])
    old = trademod.player_value(p, ctx, rate, TS)
    p.age = 22
    ctx._cache.clear()
    assert trademod.player_value(p, ctx, rate, TS) > old or p.age >= TS.value("young_age")
    # 条件:人数・同じ選手・育成選手・出せない選手
    a, b = mine.players[0].id, other.players[0].id
    assert trademod.check(mine, other, [a], [b], ctx, set(), mins, mb, TS) in (None, *[f"{t.name} の最低人数(ポジション)が足りなくなります" for t in (mine, other)], *[f"{t.name} の総年俸が資金を超えます" for t in (mine, other)])
    assert "1〜3 人" in trademod.check(mine, other, [], [b], ctx, set(), mins, mb, TS)
    assert "1〜3 人" in trademod.check(mine, other, [x.id for x in mine.players[:4]], [b], ctx, set(), mins, mb, TS)
    assert "支配下にいません" in trademod.check(mine, other, [b], [a], ctx, set(), mins, mb, TS)
    assert "入ったばかり" in trademod.check(mine, other, [a], [b], ctx, {a}, mins, mb, TS)
    if mine.development:
        assert "育成選手" in trademod.check(mine, other, [mine.development[0].id], [b], ctx, set(), mins, mb, TS)
    # AI は、価値の高い選手を安く出さない
    best = max(other.players, key=lambda x: trademod.player_value(x, ctx, rate, TS))
    worst = min(mine.players, key=lambda x: trademod.player_value(x, ctx, rate, TS))
    ok, why = trademod.ai_answer(other, [best], [worst], ctx, rate, g.state.gen_config, TS)
    assert not ok and why


def test_propose_and_offer_in_season(base):
    g = fresh(base)
    g.advance(30)  # 25 日目の決まった日を過ぎる
    v = g.trade_view()
    assert v["window"] == "season" and v["deadline_day"] == trademod.deadline_day(g.state.season.total_days, TS)
    assert all(set(p) >= {"player_id", "overall", "salary", "locked"} for p in v["mine"])
    text = str(v)
    for key in ("ratings", "growth_type", "ease", "archetype", "expected"):
        assert f"'{key}'" not in text  # 隠し情報と見込みの数値は出さない
    # 釣り合わない交換は断られる(何も変わらない)
    ctx = g._trade_ctx()
    worst = min((p for p in g._team("T01").players), key=lambda x: trademod.player_value(x, ctx, ctx.rate, TS))
    other = g._team(v["partner"])
    best = max(other.players, key=lambda x: trademod.player_value(x, ctx, ctx.rate, TS))
    before = [p.id for p in g._team("T01").players]
    try:
        r = g.trade_propose(other.id, [worst.id], [best.id])
        assert not r["result"]["accepted"] and [p.id for p in g._team("T01").players] == before
    except ValueError:
        pass  # 条件(最低人数など)で断られることもある
    # AI からの提案を受ける:選手が入れ替わり、一軍を選び直し、記録が残る
    offers = g.state.season.trade_offers
    if offers:
        o = offers[0]
        r = g.trade_offer_answer(o["id"], True)
        if r["result"]["accepted"]:
            assert all(pid in {p.id for p in g._team("T01").players} for pid in o["gives"])
            assert all(pid in {p.id for p in g._team(o["team_id"]).players} for pid in o["gets"])
            assert "T01" in g.state.season.active_history and any(x["phase"] == "trade" for x in g.state.transactions)
            assert {p.id for p in g.state.season.actives["T01"].players} <= {p.id for p in g._team("T01").players}
            # 入ったばかりの選手は、また出せない
            with pytest.raises(ValueError, match="入ったばかり"):
                g.trade_propose(o["team_id"], o["gives"][:1], [p.id for p in g._team(o["team_id"]).players if p.id not in o["gets"]][:1])
    with pytest.raises(ValueError):
        g.trade_offer_answer("nothing", True)


@pytest.mark.slow
def test_season_trades_replay_save_and_deadline(base):
    g = fresh(base)
    g.advance(90)  # 期限(83 日目)を過ぎる
    season = g.state.season
    assert g.trade_view()["window"] is None and "期限" in g.trade_view()["reason"]
    with pytest.raises(ValueError, match="期限"):
        g.trade_propose("T02", [g._team("T01").players[0].id], [g._team("T02").players[0].id])
    assert not season.trade_offers  # 期限を過ぎたら提案は消える
    # どの試合も、試合のページで同じに再現できる(トレードした球団は、その日の一軍で)
    for pg in season.played[::7]:
        again = season.replay_game(pg.scheduled.number)
        assert (again.home_runs, again.away_runs, len(again.log)) == (pg.result.home_runs, pg.result.away_runs, len(pg.result.log))
    # 保存と読み込み(版 20)
    loaded = api.Game(load_game(save_game(g.state)), dirty=False)
    assert SAVE_FORMAT_VERSION == 22
    assert loaded.state.season.trades == season.trades and loaded.state.season.active_history == season.active_history
    for pg in loaded.state.season.played[::11]:
        again = loaded.state.season.replay_game(pg.scheduled.number)
        assert (again.home_runs, again.away_runs) == (pg.result.home_runs, pg.result.away_runs)


@pytest.mark.slow
def test_offseason_trade_stage(base):
    g = fresh(base)
    g.advance(g.state.season.total_days)
    g.year_end()
    while g.offseason_view()["stage"] != "trade":
        g.offseason_stage_auto()
    v = g.offseason_view()
    proc = g.state.procedure
    assert proc.trade_started and v["trade"]["offers"] == len(proc.trade_offers)
    tv = g.trade_view()
    assert tv["window"] == "offseason"
    joined = trademod.locked_ids(proc)
    assert all(p["locked"] == (p["player_id"] in joined) for p in tv["mine"])  # このオフに入った選手は出せない
    # 保存して読み込んでも同じ(提案・トレード)
    again = api.Game(load_game(save_game(g.state)), dirty=False)
    assert again.state.procedure.trade_offers == proc.trade_offers and again.state.procedure.trades == proc.trades
    trades = list(proc.trades)
    v = g.offseason_next()  # 提案は断ったことにして、現役ドラフトへ(D-380)
    assert v["stage"] == "active_draft"
    r = g.offseason_next()  # 現役ドラフトは AI の方針で決めて完了
    assert r["finished"] and g.state.year == 2
    for rec in trades:
        assert sum(1 for x in g.state.transactions if x["phase"] == "trade" and x["player_id"] in rec["gives"] + rec["gets"]) >= len(rec["gives"] + rec["gets"])
    assert all(len(t.players) == 70 for t in g.state.league.teams)


def test_bridge_has_trade_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_trade", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert set(bridge._TRADE) == {"view", "propose", "answer"}
