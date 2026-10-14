"""F3-2c(FA:権利の取得・FA 市場。D-257〜D-264)と、作り直し②の宣言の確率(D-359)の確認。"""

import copy
import io
import json
import zipfile

import pytest

from pennant import api
from pennant import fa as famod
from pennant.config import ConfigError
from pennant.contracts import team_salary
from pennant.negotiation import load_negotiation_settings, offer_score, validate_negotiation_settings
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

NEG = load_negotiation_settings()
FA = famod.fa_settings(NEG)
HIDDEN_KEYS = ("ratings", "potential", "growth_type", "archetype", "preference", "ease", "trajectory_bias")


def finish_contract(g):
    """契約の段階(更改は自動。D-358)を自分の操作で済ませる:資金を超えていれば年俸の高い選手から自由契約 →「次の手続きへ」。"""
    if g.offseason_view()["stage"] != "contract":
        return
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
    assert FA["declare"] == {"base": 0.12, "per_war": 0.05, "min": 0.03, "max": 0.45} and "threshold_add" not in FA
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


def test_declare_probability():
    """宣言の確率は見込みの WAR が高いほど上がり、下限〜上限に収まる(D-359)。"""
    p = [famod.declare_probability(NEG, x) for x in (-3.0, 0.0, 1.0, 3.0, 20.0)]
    assert p == sorted(p) and p[0] == FA["declare"]["min"] and p[-1] == FA["declare"]["max"]
    assert famod.declare_probability(NEG, 1.0) == pytest.approx(0.12 + 0.05)


# ---- 宣言と FA 市場 ----

@pytest.fixture(scope="module")
def season_end():
    g = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default")
    g.advance(125)
    g.year_end()
    return g


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


@pytest.mark.slow
def test_declaration_at_renewal(season_end):
    g = _fresh(season_end)
    proc = g.state.procedure
    declared = [e for e in proc.negotiations.values() if e["status"] == "declared"]
    assert declared and {e["player_id"] for e in declared} == set(proc.fa_info)  # 宣言は契約の段階の始めに、全球団で(D-359)
    assert all(e["context"].get("fa_holder") for e in declared)
    holders = [e for e in proc.negotiations.values() if e["context"].get("fa_holder")]
    assert len(declared) < len(holders)  # 保持者の全員ではない(宣言しなければ更改)
    assert all(e["status"] == "accepted" for e in proc.negotiations.values() if not e["context"].get("fa_holder"))
    for e in declared:
        p = next(p for p in proc.fa_pool if p.id == e["player_id"])
        assert p.team_id is None and p.contract is None and p.fa_seasons == 0 and proc.fa_info[p.id]["former_team"] == e["team_id"]
        assert proc.fa_info[p.id]["calc_salary"] == e["auto_salary"]
    mine = [e for e in declared if e["team_id"] == "T01"]
    assert g.offseason_view()["renewal"]["counts"]["declared"] == len(mine)
    # 同じシードなら同じ選手が宣言する
    again = api.Game(load_game(save_game(g.state)), dirty=False)
    assert set(again.state.procedure.fa_info) == set(proc.fa_info)


@pytest.mark.slow
def test_fa_market_rounds_offers_and_constraints(season_end):
    g = _fresh(season_end)
    v = to_fa(g)
    proc = g.state.procedure
    assert v["phase"] == "fa" and v["fa"]["round"] == 1 and v["fa"]["rounds"] == 3 and v["fa"]["counts"]["declared"] == len(proc.fa_info) > 0
    assert "none_max_ratio" not in v["fa"]
    t = g.offseason_table("fa", "pitcher", "war")
    assert [c["label"] for c in t["columns"][:6]] == ["ポジション", "年齢", "投球回", "前の所属", "算定年俸", "状態"] and t["sort"]["key"] == "calc"
    target = max(proc.fa_pool, key=lambda p: proc.fa_info[p.id]["expected"])
    calc = proc.fa_info[target.id]["calc_salary"]
    with pytest.raises(ValueError, match="年数"):
        g.offseason_fa_offer(target.id, 0)
    room = g.budget_info("T01")["cap"] - team_salary(g._team("T01"))
    with pytest.raises(ValueError, match="資金"):
        g.offseason_fa_offer(target.id, 2, room + 100)
    pay = min(int(calc * 1.5), room) // 100 * 100  # 資金の中で、できれば算定より高い提示(算定が資金の残りを超える選手もいる)
    v = g.offseason_fa_offer(target.id, 3, pay)
    assert v["fa"]["offers"][0]["years"] == 3
    g.offseason_fa_cancel(target.id)
    assert not g.offseason_view()["fa"]["offers"]
    g.offseason_fa_offer(target.id, 3, pay)
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
    assert v["phase"] == "foreign" and proc.fa_done and not proc.fa_pool  # FA の次は外国人市場(D-376)
    assert not any(x["team_id"] == "T01" and x["round"] > 1 for x in proc.fa_log)  # 2 ラウンド目以降、自球団は提示していない
    unsigned = [pid for pid, x in proc.fa_info.items() if x["status"] == "unsigned"]
    assert all(any(p.id == pid for p in proc.market) for pid in unsigned)  # 決まらなかった選手は市場へ
    assert all(len(t.players) <= 70 for t in g.state.league.teams)
    assert all(team_salary(t) <= g.budget_info(t.id)["cap"] for t in g.state.league.teams)
    g.offseason_auto()
    assert g.status()["year"] == 2
    assert any(x["phase"] == "fa_declare" for x in g.state.transactions) and all(x["team_id"] for x in g.state.transactions if x["phase"] == "fa")


def test_player_prefers_better_offer():
    """同じ球団の条件で、年俸を上げた提示の方が点数が高い。元の球団は愛着の分だけ高い(D-365)。"""
    ctx = {"rank": 0, "slots": 2.0, "standing": 3, "league_size": 6}
    low = offer_score(NEG, 10000, 10000, 1.0, False, 1, 30, ctx, 0.0)
    high = offer_score(NEG, 12000, 10000, 1.0, False, 1, 30, ctx, 0.0)
    home = offer_score(NEG, 10000, 10000, 1.0, True, 1, 30, ctx, 0.0)
    assert high > home > low
    assert famod.round_salary(10000, 1.2, 100) == 12000 and famod.round_salary(10000, 0.9, 100) == 10000  # 算定を下回らない


@pytest.mark.slow
def test_ai_offers_respect_limits(season_end):
    g = _fresh(season_end)
    to_fa(g)
    proc = g.state.procedure
    ctx = g._contract_ctx()
    sizes = {0: 6, 1: 6}
    for team in g.state.league.teams:
        offers = famod.ai_offers(team, proc, ctx, sizes)
        assert len(offers) <= FA["ai_max_offers"]
        assert all(proc.fa_info[pid]["expected"] >= FA["ai_min_expected"] for pid in offers)
        assert team_salary(team) + sum(s for _, s in offers.values()) <= ctx.cap(team.id)
        # 年俸は 算定 × ラウンドの倍率 × 主力級の期待の倍率(D-325)
        assert all(abs(s - proc.fa_info[pid]["calc_salary"] * FA["round_multipliers"][0] * famod.star_multiplier(NEG, proc.fa_info[pid]["expected"])) <= 100 for pid, (_, s) in offers.items())


@pytest.mark.slow
def test_fa_hidden_info_is_not_shown(season_end):
    g = _fresh(season_end)
    to_fa(g)
    pid = g.state.procedure.fa_pool[0].id
    text = json.dumps([g.offseason_view(), g.offseason_table("fa", "batter", "war"), g.offseason_table("fa", "pitcher", "basic"), g.player(pid)], ensure_ascii=False)
    for key in HIDDEN_KEYS:
        assert f'"{key}"' not in text
    assert g.player(pid)["player"]["team_name"] == "FA 宣言中" and g.player(g.state.procedure.market[0].id)["player"]["team_name"] == "自由契約"
    page = g.player(next(p for p in g._team("T02").players).id)
    assert "FA 権" in page["player"]["contract"]["fa_text"] or "一軍" in page["player"]["contract"]["fa_text"]


@pytest.mark.slow
def test_save_round_trip(season_end):
    g = _fresh(season_end)
    data = save_game(g.state)
    assert SAVE_FORMAT_VERSION == 20
    again = load_game(data)
    assert [p.fa_seasons for p in again.league.all_players()] == [p.fa_seasons for p in g.state.league.all_players()]
    assert again.procedure.fa_info == g.state.procedure.fa_info and [p.id for p in again.procedure.fa_pool] == [p.id for p in g.state.procedure.fa_pool]


def test_web_bridge_fa_actions():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_fa", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"fa_offer", "fa_cancel", "fa_close"} <= set(bridge._OFFSEASON)
