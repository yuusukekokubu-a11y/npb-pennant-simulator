"""現役ドラフト(D-380)と仕組みの選択肢(D-381):候補の条件・AI の候補・指名の順番と 1 人出て 1 人入る・あなたの候補と指名・
自動 FA・なしにした段階を飛ばす・外国人と育成なしの新規開始・保存(版 21。D-382)。"""

import copy

import pytest

from pennant import active_draft as admod, api
from pennant.rules import DEFAULT_RULES, GameRules, rules_from
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game


@pytest.fixture(scope="module")
def season_end():
    g = api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default")
    g.advance_to_end()
    g.year_end()
    return g


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def to_active(g):
    while g.offseason_view()["stage"] != "active_draft":
        g.offseason_stage_auto()
    return g.offseason_view()


def test_rules_from_and_defaults():
    assert rules_from(None) == DEFAULT_RULES and not DEFAULT_RULES.auto_fa and DEFAULT_RULES.active_draft and DEFAULT_RULES.trade
    assert rules_from({"trade": False}) == GameRules(trade=False)
    assert GameRules(trade=False, active_draft=False, foreign_development=False).skipped_phases() == ("foreign", "dev_draft", "trade", "active_draft")
    with pytest.raises(ValueError, match="知らない"):
        rules_from({"nothing": True})
    with pytest.raises(ValueError, match="トレード"):
        rules_from({"trade": "yes"})
    with pytest.raises(ValueError):
        api.Game.new(5, [None] * 12, 0, baselines="default", rules={"auto_fa": 1})


def test_offers_and_picks(season_end):
    g = fresh(season_end)
    v = to_active(g)
    a = v["active_draft"]
    proc = g.state.procedure
    assert not a["offers_done"] and len(a["recommended"]) == 2 and set(a["recommended"]) <= {p["player_id"] for p in a["eligible"]}
    text = str(a)
    for key in ("ratings", "growth_type", "ease", "archetype", "expected"):
        assert f"'{key}'" not in text  # 隠し情報と見込みの数値は出さない
    # 出せない選手:一軍にいた選手・外国人
    elig = {p["player_id"] for p in a["eligible"]}
    assert not elig & set(proc.season_actives)
    assert not any(p.foreign for p in g._team("T01").players if p.id in elig)
    with pytest.raises(ValueError, match="出せない"):
        g.active_draft_offer([proc.season_actives[0]])
    with pytest.raises(ValueError, match="1〜2"):
        g.active_draft_offer([])
    before = {t.id: len(t.players) for t in g.state.league.teams}
    mine = list(a["recommended"])
    v = g.active_draft_offer(mine)
    a = v["active_draft"]
    assert a["offers_done"] and proc.active_offers["T01"] == mine
    if a["is_my_turn"]:
        assert a["choices"] and all(c["team_id"] != "T01" for c in a["choices"])
        with pytest.raises(ValueError, match="指名できません"):
            g.active_draft_pick(mine[0])  # 自分の候補は指名できない
        v = g.active_draft_pick(a["recommended_pick"])
        a = v["active_draft"]
    assert a["finished"] and v["phase_finished"]
    picks = [x for x in proc.active_picks if x["player_id"]]
    teams = [t for t in proc.order if proc.active_offers.get(t)]
    assert sorted(x["team_id"] for x in picks) == sorted(teams) and sorted(x["from_team"] for x in picks) == sorted(teams)  # 1 人出て 1 人入る
    assert all(x["team_id"] != x["from_team"] and x["player_id"] in proc.active_offers[x["from_team"]] for x in picks)
    assert [x["team_id"] for x in picks] == teams  # 前年の順位が低い球団から
    assert {t.id: len(t.players) for t in g.state.league.teams} == before
    for x in picks:
        p = next(p for p in g._team(x["team_id"]).players if p.id == x["player_id"])
        assert p.contract and int(p.contract["salary"]) == x["salary"]  # 契約はそのまま
    r = g.offseason_next()
    assert r["finished"]
    assert sum(1 for x in g.state.transactions if x["phase"] == "active_draft") == len(picks)


def test_stage_auto_and_save(season_end):
    g = fresh(season_end)
    to_active(g)
    again = api.Game(load_game(save_game(g.state)), dirty=False)
    assert SAVE_FORMAT_VERSION == 30 and again.state.rules == g.state.rules
    r = g.offseason_stage_auto()
    assert r["finished"]
    log = r["auto_log"]["items"]
    assert {x["kind"] for x in log} <= {"active_draft", "active_draft_out"} and len(log) == 2
    # 保存して読み込んでも、同じ結果
    r2 = again.offseason_stage_auto()
    assert [x["player_id"] for x in again.state.transactions if x["phase"] == "active_draft"] == [x["player_id"] for x in g.state.transactions if x["phase"] == "active_draft"]
    assert r2["finished"]


def test_auto_fa_and_skipped_stages():
    g = api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default", rules={"auto_fa": True, "trade": False, "active_draft": False})
    assert g.state.rules.auto_fa and g._trade_status() is None
    with pytest.raises(ValueError, match="トレードはありません"):
        g.trade_view() and g.trade_propose("T02", [], [])
    g.advance_to_end()
    assert not g.state.season.trades
    g.year_end()
    proc = g.state.procedure
    assert proc.game_rules().auto_fa
    declared = [e for e in proc.negotiations.values() if e["status"] == "declared"]
    assert declared and all(e["context"].get("auto_fa") for e in declared)  # FA 権を取った選手は必ず FA(D-390)
    assert all(e["status"] == "accepted" for e in proc.negotiations.values() if not e["context"].get("auto_fa"))  # 権利のない選手は更改
    assert all(p.fa_free for p in proc.fa_pool if proc.fa_info[p.id].get("auto"))
    mine = [p.id for p in proc.fa_pool if proc.fa_info[p.id]["former_team"] == "T01"]
    assert mine and set(proc.fa_offers) == set(mine)  # あなたの残留の提示は最初から入っている
    stages = []
    while True:
        v = g.offseason_view()
        stages.append(v["stage"])
        r = g.offseason_stage_auto()
        if r.get("finished"):
            break
    assert "trade" not in stages and "active_draft" not in stages
    stay = sum(1 for x in g.last_fa["results"] if x["team_id"] == x["former_team"])
    assert stay > len(g.last_fa["info"]) * 0.5  # 半分以上は元の球団に残る
    assert all(60 <= len(t.players) <= 70 for t in g.state.league.teams)


def test_no_foreign_and_development():
    g = api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default", rules={"foreign_development": False})
    assert not any(p.foreign for p in g.state.league.all_players()) and not g.state.league.development_players()
    loaded = load_game(save_game(g.state))
    assert loaded.rules.foreign_development is False


def test_eligible_rules_unit(season_end):
    g = fresh(season_end)
    to_active(g)
    proc = g.state.procedure
    ctx = g._contract_ctx()
    mins, mb = g._mins()
    team = g._team("T02")
    first = admod.first_team_ids(g.state.league, proc, ctx)
    limit = 3 * int(ctx.settings.minimum)
    for p in admod.eligible(team, proc, ctx, mins, mb, first):
        assert not p.foreign and p.id not in first and (not p.contract or int(p.contract["salary"]) <= limit)
