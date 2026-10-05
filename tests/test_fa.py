"""F3-2c(FA:権利の取得・FA 市場・残留交渉。D-257〜D-264)の確認。"""

import copy
import io
import json
import zipfile

import pytest

from pennant import answers, api
from pennant import fa as famod
from pennant.config import ConfigError
from pennant.contracts import team_salary
from pennant.negotiation import judge, load_negotiation_settings, validate_negotiation_settings
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

NEG = load_negotiation_settings()
FA = famod.fa_settings(NEG)
HIDDEN_KEYS = ("ratings", "potential", "growth_type", "archetype", "preference")


def finish_contract(g):
    """契約の段階(更改と自由契約。D-272)を自分の操作で済ませる:自動案 → 断った選手は自由契約 → 上限を超えていれば年俸の高い選手から自由契約 →「次の手続きへ」。"""
    proc = g.state.procedure
    if g.offseason_view()["stage"] != "contract":
        return
    g.offseason_renew_auto()
    for e in [e for e in proc.negotiations.values() if e["team_id"] == g.state.my_team_id and e["status"] == "pending"]:
        g.offseason_renew_release(e["player_id"])
    while g.offseason_view()["renewal"]["over"] > 0:
        rows = [r for r in g.contract_table("all", sort="contract", order="desc")["rows"] if r["can_release"] and r["in_team"]]
        g.offseason_contract_release(rows[0]["player_id"])
    g.offseason_next()


def to_fa(g):
    """契約の段階を済ませて、FA の段階まで進める。"""
    finish_contract(g)
    return g.offseason_view()


# ---- 設定・年数 ----

def test_settings_and_validation():
    assert FA["seasons_required"] == 7 and FA["rounds"] == 3 and FA["round_multipliers"] == [1.0, 1.1, 1.2] and FA["ai_max_offers"] == 3 and FA["compensation"] is None
    data = copy.deepcopy(NEG.data)
    data["fa"]["round_multipliers"] = [1.0]
    with pytest.raises(ConfigError, match="round_multipliers"):
        validate_negotiation_settings(data)
    data = copy.deepcopy(NEG.data)
    data["fa"]["init"]["p_active"] = 2
    with pytest.raises(ConfigError, match="p_active"):
        validate_negotiation_settings(data)


def test_seasons_counting_and_initial_estimate():
    g = api.Game.new(3, [None] * 12, None, season_seed=4, baselines="default")
    league = g.state.league
    actives = {p.id for a in g.state.season.actives.values() for p in a.players}
    players = league.all_players()
    assert all(isinstance(p.fa_seasons, int) and 0 <= p.fa_seasons <= max(0, p.age - 21) for p in players)
    # 一軍の選手のほうが年数が多い(確率 0.7 と 0.25)。同じシードなら同じ値
    act = [p.fa_seasons / max(1, p.age - 21) for p in players if p.id in actives and p.age > 25]
    other = [p.fa_seasons / max(1, p.age - 21) for p in players if p.id not in actives and p.age > 25]
    assert sum(act) / len(act) > sum(other) / len(other) + 0.3
    again = api.Game.new(3, [None] * 12, None, season_seed=4, baselines="default")
    assert [p.fa_seasons for p in again.state.league.all_players()] == [p.fa_seasons for p in players]
    holders = [p for p in players if famod.is_holder(p, NEG)]
    assert 0.08 < len(holders) / len(players) < 0.3
    # 年度の確定で、そのシーズンの一軍だけ 1 足す(29 人 × 12 球団)
    before = {p.id: p.fa_seasons for p in players}
    assert famod.count_active_seasons(league, g.state.season.actives) == 29 * 12
    assert all(p.fa_seasons == before[p.id] + (1 if p.id in actives else 0) for p in players)


def test_holder_threshold_in_judge():
    pref = {"salary": 0.3, "playing_time": 0.4, "winning": 0.3}
    ctx = {"rank": 1, "slots": 2.0, "standing": 3, "league_size": 6}
    a = judge(NEG, pref, 1, 1000, 1000, 30, ctx, "loose", 0.0)
    b = judge(NEG, pref, 1, 1000, 1000, 30, {**ctx, "fa_holder": True, "threshold_add": FA["threshold_add"]}, "loose", 0.0)
    assert b["score"] == pytest.approx(a["score"] - FA["threshold_add"])


# ---- 宣言と FA 市場 ----

@pytest.fixture(scope="module")
def season_end():
    out = {}
    for rule in ("none", "standard"):
        g = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default", money_rule=rule)
        g.advance(125)
        g.year_end()
        out[rule] = g
    return out


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def test_declaration_on_refusal(season_end):
    g = _fresh(season_end["standard"])
    proc = g.state.procedure
    ai_declared = [pid for pid, x in proc.fa_info.items() if x["former_team"] != "T01"]
    assert ai_declared and all(proc.negotiations[pid]["status"] == "declared" for pid in ai_declared)  # AI 球団の保持者も、断ったら宣言する
    holders = [e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["context"].get("fa_holder")]
    assert holders and all(e["context"]["threshold_add"] == FA["threshold_add"] for e in holders)
    v = g.offseason_renew_auto()
    declared = [e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["status"] == "declared"]
    assert v["renewal"]["counts"]["declared"] == len(declared)
    for e in declared:
        assert len(e["offers"]) == 1 and not e["offers"][0]["accepted"]  # 断ったその場で宣言(提示の回数が残っていても)
        p = next(p for p in proc.fa_pool if p.id == e["player_id"])
        assert p.team_id is None and p.contract is None and p.fa_seasons == 0 and proc.fa_info[p.id]["former_team"] == "T01"
        assert all(q.id != p.id for q in g._team("T01").players)
    # 受けた選手と、保持者でない選手は宣言しない
    assert all(not e["context"].get("fa_holder") for e in proc.negotiations.values() if e["status"] == "released")


def test_fa_market_rounds_offers_and_constraints(season_end):
    for rule in ("none", "standard"):
        g = _fresh(season_end[rule])
        v = to_fa(g)
        proc = g.state.procedure
        assert v["phase"] == "fa" and v["fa"]["round"] == 1 and v["fa"]["rounds"] == 3 and v["fa"]["counts"]["declared"] == len(proc.fa_info) > 0
        t = g.offseason_table("fa", "pitcher", "war")
        assert [c["label"] for c in t["columns"][:6]] == ["ポジション", "年齢", "投球回", "前の所属", "算定年俸", "状態"] and t["sort"]["key"] == "calc"
        target = max(proc.fa_pool, key=lambda p: proc.fa_info[p.id]["expected"])
        calc = proc.fa_info[target.id]["calc_salary"]
        with pytest.raises(ValueError, match="年数"):
            g.offseason_fa_offer(target.id, 0)
        if rule == "none":  # 「なし」は算定の 1.0〜1.3 倍(D-273)
            with pytest.raises(ValueError, match="倍"):
                g.offseason_fa_offer(target.id, 2, calc * 2)
            v = g.offseason_fa_offer(target.id, 2)
            assert v["fa"]["offers"][0]["salary"] == calc
            v = g.offseason_fa_offer(target.id, 2, int(calc * 1.25))
            assert v["fa"]["offers"][0]["salary"] == int(calc * 1.25)
        else:
            room = g.budget_info("T01")["cap"] - team_salary(g._team("T01"))
            with pytest.raises(ValueError, match="上限"):
                g.offseason_fa_offer(target.id, 2, room + 100)
            v = g.offseason_fa_offer(target.id, 3, int(calc * 1.5) // 100 * 100)
            assert v["fa"]["offers"][0]["years"] == 3
            g.offseason_fa_cancel(target.id)
            assert not g.offseason_view()["fa"]["offers"]
            g.offseason_fa_offer(target.id, 3, int(calc * 1.5) // 100 * 100)
        # 保存して読み込んでも、同じ結果(乱数は手続きのシードから)
        again = api.Game(load_game(save_game(g.state)), dirty=False)
        assert again.state.procedure.fa_offers == proc.fa_offers and again.offseason_view()["phase"] == "fa"
        r1 = g.offseason_fa_close()["last_round"]
        r2 = again.offseason_fa_close()["last_round"]
        assert r1 == r2 and r1["round"] == 1
        assert proc.fa_round == 2 and not proc.fa_offers and len(proc.fa_results) == len(r1["signed"])
        for x in proc.fa_results:  # 成立した選手は、契約(FA)と球団を持つ
            team = g._team(x["team_id"])
            p = next(p for p in team.players if p.id == x["player_id"])
            assert p.contract["history"][-1]["reason"] == "fa" and p.contract["salary"] == x["salary"] and p.fa_seasons == 0
        with pytest.raises(ValueError):
            g.offseason_fa_offer(proc.fa_results[0]["player_id"], 1) if proc.fa_results else g.offseason_fa_offer("nobody", 1)
        v = g.offseason_next()  # 残りのラウンドは、自球団は追加の提示をせずに締める(D-271)
        assert v["phase"] == "draft" and proc.fa_done and not proc.fa_pool
        assert not any(x["team_id"] == "T01" and x["round"] > 1 for x in proc.fa_log)  # 2 ラウンド目以降、自球団は提示していない
        unsigned = [pid for pid, x in proc.fa_info.items() if x["status"] == "unsigned"]
        assert all(any(p.id == pid for p in proc.market) for pid in unsigned)  # 決まらなかった選手は市場へ
        assert all(len(t.players) <= 70 for t in g.state.league.teams)
        if rule == "standard":
            assert all(team_salary(t) <= g.budget_info(t.id)["cap"] for t in g.state.league.teams)
        g.offseason_auto()
        assert g.status()["year"] == 2
        assert any(x["phase"] == "fa_declare" for x in g.state.transactions) and all(x["team_id"] for x in g.state.transactions if x["phase"] == "fa")


def test_player_prefers_better_offer():
    """同じ球団の条件で、年俸を上げた提示の方が満足度が高い(年俸の軸が効くルール)。"""
    pref = {"salary": 0.6, "playing_time": 0.2, "winning": 0.2}
    ctx = {"rank": 0, "slots": 2.0, "standing": 3, "league_size": 6}
    low = judge(NEG, pref, 1, 10000, 10000, 30, ctx, "standard", 0.0)
    high = judge(NEG, pref, 1, 12000, 10000, 30, ctx, "standard", 0.0)
    assert high["score"] > low["score"]
    none_low = judge(NEG, pref, 1, 10000, 10000, 30, ctx, "none", 0.0)
    none_high = judge(NEG, pref, 1, 12000, 10000, 30, ctx, "none", 0.0)
    assert none_high["score"] > none_low["score"] and none_high == high  # 「なし」でも年俸の軸が効く(D-273)
    assert famod.round_salary(10000, 1.2, "none", 100, True) == 12000 and famod.round_salary(10000, 1.2, "none", 100, False) == 10000


def test_ai_offers_respect_limits(season_end):
    g = _fresh(season_end["standard"])
    to_fa(g)
    proc = g.state.procedure
    ctx = g._contract_ctx()
    sizes = {0: 6, 1: 6}
    for team in g.state.league.teams:
        offers = famod.ai_offers(team, proc, ctx, sizes)
        assert len(offers) <= FA["ai_max_offers"]
        assert all(proc.fa_info[pid]["expected"] >= FA["ai_min_expected"] for pid in offers)
        assert team_salary(team) + sum(s for _, s in offers.values()) <= ctx.cap(team.id)
        assert all(s == proc.fa_info[pid]["calc_salary"] or abs(s - proc.fa_info[pid]["calc_salary"] * FA["round_multipliers"][0]) <= 100 for pid, (_, s) in offers.items())


def test_fa_is_hidden_unless_answer_mode(season_end):
    g = _fresh(season_end["standard"])
    to_fa(g)
    pid = g.state.procedure.fa_pool[0].id
    text = json.dumps([g.offseason_view(), g.offseason_table("fa", "batter", "war"), g.offseason_table("fa", "pitcher", "basic"), g.player(pid)], ensure_ascii=False)
    for key in HIDDEN_KEYS:
        assert f'"{key}"' not in text
    assert g.player(pid)["player"]["team_name"] == "FA 宣言中" and g.player(g.state.procedure.market[0].id)["player"]["team_name"] == "自由契約"
    a = answers.fa_answers(g, 1)
    assert a["available"] and pid in a["players"] and [x["key"] for x in a["players"][pid]] == list(NEG.axes)
    page = g.player(next(p for p in g._team("T02").players).id)
    assert "FA 権" in page["player"]["contract"]["fa_text"] or "一軍" in page["player"]["contract"]["fa_text"]


def test_save_v12_and_v11_loads(season_end):
    g = _fresh(season_end["standard"])
    data = save_game(g.state)
    assert SAVE_FORMAT_VERSION == 12
    again = load_game(data)
    assert [p.fa_seasons for p in again.league.all_players()] == [p.fa_seasons for p in g.state.league.all_players()]
    assert again.procedure.fa_info == g.state.procedure.fa_info and [p.id for p in again.procedure.fa_pool] == [p.id for p in g.state.procedure.fa_pool]
    # 版 11(FA なし)として読む:年数は補い、進行中の手続きはそのまま続けられる
    g2 = api.Game.new(3, [None] * 12, None, season_seed=7, baselines="default")
    g2.advance(125)
    g2.year_end()
    g2.advance(2)
    zin = zipfile.ZipFile(io.BytesIO(save_game(g2.state)))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 11
    state = json.loads(files["state.json"])
    del state["configs"]["negotiation"]["fa"]
    for team in state["league"]["teams"]:
        for pd in team["players"]:
            del pd["fa_seasons"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    old = load_game(buf.getvalue())
    assert all(isinstance(p.fa_seasons, int) for p in old.league.all_players())
    assert [p.fa_seasons for p in load_game(buf.getvalue()).league.all_players()] == [p.fa_seasons for p in old.league.all_players()]  # 同じ乱数系列
    game = api.Game(old, dirty=False)
    game.advance(123)
    game.year_end()
    assert game.status()["year"] == 3


def test_web_bridge_fa_actions():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_fa", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"fa_offer", "fa_cancel", "fa_close"} <= set(bridge._OFFSEASON) and "fa_answers" in bridge._ANSWERS
