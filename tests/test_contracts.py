"""F3-2a(契約の土台:年俸・契約年数・お金のルール 4 段階・予算。D-229〜D-241)の確認。
F3-2b で契約年数の既定が 1 年になり(D-243)、手続きの最初に契約更改の段階が入った(D-244)。更改そのものの確認は test_negotiation.py。"""

import copy
import io
import json
import statistics
import zipfile

import pytest

from pennant import api
from pennant.config import ConfigError
from pennant.contracts import (
    MONEY_RULES,
    age_factor,
    assign_tiers,
    cap_of,
    contract_years,
    expected_war,
    history_war,
    is_expiring,
    load_contract_settings,
    rate_for,
    remaining_years,
    salary_for,
    scouting_war,
    team_salary,
    validate_contract_settings,
)
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

CS = load_contract_settings()


# ---- 設定と算定の式 ----

def test_settings_and_validation():
    assert CS.minimum == 500 and CS.base_budget == 400000 and CS.cap_factor == 1.1 and CS.default_rule == "none"
    assert CS.rookie_salary(1) == 1500 and CS.rookie_salary(6) == 500 and CS.rookie_salary(9) == 500 and CS.rookie_years == 1 and CS.default_years == 1 and CS.max_years == 5
    data = copy.deepcopy(CS.data)
    data["budget"]["tier_counts"]["large"] = 5
    with pytest.raises(ConfigError, match="tier_counts"):
        validate_contract_settings(data)
    data = copy.deepcopy(CS.data)
    data["rules"]["default"] = "hard"
    with pytest.raises(ConfigError, match="rules.default"):
        validate_contract_settings(data)


def test_salary_formula_uses_only_visible_inputs():
    # 履歴:新しいほど重く、出場の少ない年は軽い
    assert history_war([(3.0, 500), (1.0, 500), (2.0, 500)], "batter", CS) == pytest.approx(0.5 * 3 + 0.3 * 1 + 0.2 * 2)
    assert history_war([(3.0, 30), (1.0, 600)], "batter", CS) == pytest.approx((0.5 * 0.1 * 3 + 0.3 * 1.0 * 1) / (0.5 * 0.1 + 0.3))
    assert history_war([], "batter", CS) is None and history_war([(2.0, 0)], "pitcher", CS) is None
    # 評価:推定値が高いほど、天井が高いほど大きい
    assert scouting_war("batter", 60, "S", CS) > scouting_war("batter", 50, "S", CS) > scouting_war("batter", 50, "D", CS)
    # 年齢の割引
    assert age_factor(29, CS) == 1.0 and age_factor(30, CS) == pytest.approx(0.92) and age_factor(40, CS) == pytest.approx(0.4)
    exp, source = expected_war(28, "batter", [(2.0, 500)], (50, "B"), CS)
    assert exp == pytest.approx(2.0) and source == "history"
    exp2, source2 = expected_war(22, "batter", [], (55, "A"), CS)
    assert source2 == "scouting" and exp2 == pytest.approx(scouting_war("batter", 55, "A", CS))
    assert expected_war(35, "pitcher", [], None, CS) == (0.0, "none")
    # 年俸:最低年俸 + 単価 × 見込み(下限は最低年俸。100 万円に丸める)
    assert salary_for(-1.0, 14000, CS) == 500 and salary_for(2.0, 14000, CS) == 28500 and salary_for(0.004, 14000, CS) == 600
    # 契約年数:既定は 1 年(F3-2b。D-243)。複数年は更改であなたが選んだ選手と AI の若手だけ
    assert contract_years(22, 0.0, CS) == 1 and contract_years(27, 2.5, CS) == 1 and contract_years(36, 5.0, CS) == 1
    # 単価:(基準予算 × 球団数 − 最低年俸 × 選手数)÷ Σ max(0, 見込み)
    assert rate_for([3.0, 2.0, -1.0], 12, 840, CS) == pytest.approx((400000 * 12 - 500 * 840) / 5.0)
    assert rate_for([0.0, -1.0], 12, 840, CS) == 0.0
    assert remaining_years({"salary": 500, "until": 3}, 2) == 2 and remaining_years({"salary": 500, "until": 1}, 2) == 0 and remaining_years(None, 1) == 0
    assert is_expiring({"salary": 500, "until": 1}, 1) and not is_expiring({"salary": 500, "until": 2}, 1) and is_expiring(None, 1)


def test_budget_and_tiers():
    assert cap_of("none", None, CS) is None and cap_of("loose", None, CS) == 440000 and cap_of("standard", None, CS) == 440000
    assert cap_of("strict", "large", CS) == 528000 and cap_of("strict", "medium", CS) == 440000 and cap_of("strict", "small", CS) == 374000
    ids = [f"T{i:02d}" for i in range(1, 13)]
    tiers = assign_tiers(ids, 7, CS)
    assert sorted(tiers.values()).count("large") == 3 and sorted(tiers.values()).count("small") == 3 and len(tiers) == 12
    assert tiers == assign_tiers(ids, 7, CS) and tiers != assign_tiers(ids, 8, CS)  # シードから決まる


# ---- ゲームの流れ(観戦・操作) ----

@pytest.fixture(scope="module")
def games():
    out = {}
    for rule in ("none", "standard", "strict"):
        g = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default", money_rule=rule)
        out[rule] = g
    return out


def test_new_game_has_contracts_within_budget(games):
    for rule, g in games.items():
        state = g.state
        assert state.money_rule == rule and g.status()["money_rule"] == rule
        assert all(p.contract and p.contract["salary"] >= CS.minimum and p.contract["until"] >= 1 for p in state.league.all_players())
        assert all(h["reason"] == "initial" for p in state.league.all_players() for h in p.contract["history"])
        assert state.contract_rates["1"] > 0
        totals = [team_salary(t) for t in state.league.teams]
        assert abs(statistics.fmean(totals) - CS.base_budget) / CS.base_budget < 0.1  # 全球団の平均は基準予算の ±10%
        if rule == "none":
            assert state.budget_tiers == {} and g.budget_info("T01")["cap"] is None
        else:
            assert all(team_salary(t) <= g.budget_info(t.id)["cap"] for t in state.league.teams)  # 開始時は上限の中
        if rule == "strict":
            assert sorted(set(state.budget_tiers.values())) == ["large", "medium", "small"] and len({g.budget_info(t.id)["cap"] for t in state.league.teams}) == 3
        else:
            assert state.budget_tiers == {}
    # ルール「なし」と「ゆるい」では、契約が AI の判断に入らないので、同じシードなら選手(真の能力)も同じ(D-237)
    loose = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default", money_rule="loose")
    a, b = games["none"].state.league.all_players(), loose.state.league.all_players()
    assert [p.id for p in a] == [p.id for p in b] and all(p.ratings == q.ratings for p, q in zip(a, b))
    assert [p.contract for p in a] == [p.contract for p in b]


def test_salary_ignores_hidden_info(games):
    """隠し情報(潜在能力・成長タイプ・型)を変えても、評価・成績が同じなら年俸は変わらない(D-232)。"""
    g = games["none"]
    from pennant.draft import ContractContext, state_war_history

    state = g.state
    p = state.league.teams[0].players[0]
    ctx = ContractContext(state.contract_settings, "none", {}, state_war_history(state), state.scout_sd_of)
    ctx.scout = lambda player, tid: (55.0, "A")  # 評価を固定
    ctx.rate = 14000.0
    before = ctx.salary(p, "T01")
    p2 = copy.deepcopy(p)
    for item in p2.hidden.potential:
        p2.hidden.potential[item] += 20
    p2.hidden.growth_type = "late" if p2.hidden.growth_type != "late" else "early"
    ctx2 = ContractContext(state.contract_settings, "none", {}, state_war_history(state), state.scout_sd_of)
    ctx2.scout = lambda player, tid: (55.0, "A")
    ctx2.rate = 14000.0
    assert ctx2.salary(p2, "T01") == before


def test_renewal_and_hard_rules_flow(games):
    for rule in ("none", "standard", "strict"):
        g = api.Game(copy.deepcopy(games[rule].state), dirty=False)
        g.advance(125)
        expiring = [p.id for t in g.state.league.teams for p in t.players if p.contract["until"] <= 1]
        g.year_end()
        proc = g.state.procedure
        assert g.offseason_view()["phase"] == "renewal"
        g.offseason_renew_auto()
        for e in [e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["status"] == "pending"]:
            g.offseason_renew_release(e["player_id"])
        v = g.offseason_view()
        c = v["contracts"]
        assert v["stage"] == "contract" and c["rule"] == rule and proc.contracts_done and proc.rate > 0 and g.state.contract_rates["2"] == proc.rate
        negotiated = {pid for pid, e in proc.negotiations.items()}
        assert negotiated == {pid for pid in expiring if pid in negotiated or any(p.id == pid for t in g.state.league.teams for p in t.players)}  # 満了者は全員(引退した人を除く)が更改の対象
        renewed = {x["player_id"] for x in proc.renewals}
        assert renewed == {pid for pid, e in proc.negotiations.items() if e["status"] == "accepted"} and renewed
        assert all(p.contract["until"] >= 2 for t in g.state.league.teams for p in t.players)  # 残った選手は全員、次のシーズンの契約がある
        assert not proc.budget_releases  # AI 球団の予算超過の解消は、契約の段階の終わりに(D-272)
        if rule == "none":
            assert c["mine"]["cap"] is None and not c["mine"]["blocked"]
        elif c["mine"]["blocked"]:
            with pytest.raises(ValueError, match="予算の上限"):
                g.offseason_next()
            t = g.contract_table("pitcher", "basic", "contract", "desc")
            ids = []
            for r in t["rows"]:
                if r["can_release"] and r["in_team"]:
                    ids.append(r["player_id"])
                if team_salary(g._team("T01")) - sum(int(p.contract["salary"]) for p in g._team("T01").players if p.id in ids) <= c["mine"]["cap"]:
                    break
            g.offseason_release(ids)
            assert g.offseason_view()["contracts"]["mine"]["over_now"] == 0
        assert all(x.get("salary") is not None for x in proc.released)  # 手放した選手の年俸は履歴に残り、契約は消える
        assert all(p.contract is None for p in proc.market)
        v = g.offseason_next()
        if rule != "none":
            assert all(team_salary(t) <= g.budget_info(t.id)["cap"] for t in g.state.league.teams if t.id != "T01")  # AI は超過を解消
            assert all(x["team_id"] != "T01" for x in proc.budget_releases)
        if v["phase"] == "fa":  # FA の段階(F3-2c)は、自球団は提示せずに締める
            v = g.offseason_next()
        assert v["phase"] == "draft"
        v = g.offseason_advance()
        pick = v["pool"][0]["player_id"]
        t = g.offseason_table("draft", "batter", "basic")
        assert all(r["offer"]["salary"] == CS.rookie_salary(1) and r["offer"]["years"] == CS.rookie_years for r in t["rows"])
        g.offseason_pick(pick)
        chosen = next(p for p in g._team("T01").players if p.id == pick)
        assert chosen.contract["salary"] == CS.rookie_salary(1) and chosen.contract["until"] == 2 + CS.rookie_years - 1 and chosen.contract["history"][-1]["reason"] == "draft"
        g.offseason_next()
        m = g.offseason_table("market", "batter", "war")
        assert all(r["offer"] is not None and r["values"]["salary"] != "—" for r in m["rows"])
        if rule != "none":
            assert all(r["offer"]["affordable"] == (team_salary(g._team("T01")) + r["offer"]["salary"] <= g.budget_info("T01")["cap"]) for r in m["rows"])
        g.offseason_auto()
        assert g.status()["year"] == 2 and all(len(t.players) == 70 for t in g.state.league.teams)
        assert all(p.contract and p.contract["until"] >= 2 for p in g.state.league.all_players())
        fills = [x for x in g.state.transactions if x["year"] == 1 and x["phase"] == "fill"]
        assert all(x["salary"] == CS.minimum for x in fills)
        if rule != "none":
            for t in g.state.league.teams:  # 超えているのは最低年俸の自動補充の分だけ(補充は例外。D-236)
                over = team_salary(t) - g.budget_info(t.id)["cap"]
                assert over <= sum(x["salary"] for x in fills if x["team_id"] == t.id)
            assert any(x.get("note") == "budget" for x in g.state.transactions) or not proc.budget_releases


def test_loose_rule_only_warns():
    g = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default", money_rule="loose")
    g.advance(125)
    g.year_end()
    g.offseason_renew_auto()
    for e in [e for e in g.state.procedure.negotiations.values() if e["team_id"] == "T01" and e["status"] == "pending"]:
        g.offseason_renew_release(e["player_id"])
    g.offseason_next()
    c = g.offseason_view()["contracts"]
    assert c["rule"] == "loose" and not c["hard"] and c["mine"]["cap"] == 440000 and not c["mine"]["blocked"] and not g.state.procedure.budget_releases
    g.offseason_next()  # 超えていても進める
    g.offseason_auto()
    assert g.status()["year"] == 2


def test_contract_pages_are_public_and_without_hidden_info(games):
    g = games["strict"]
    p = g.state.league.teams[1].players[0]
    page = g.player(p.id)
    c = page["player"]["contract"]
    assert c["salary"] == p.contract["salary"] and c["remaining"] >= 1 and c["history"][0]["reason_label"] == "開始時"
    team = g.team("T02")
    assert team["budget"]["tier_label"] in ("大", "中", "小") and team["budget"]["usage"] <= 100 and team["salaries"][0]["salary"] >= team["salaries"][-1]["salary"]
    text = json.dumps([page, team, g.budget_info("T02")], ensure_ascii=False)
    for key in ("ratings", "potential", "growth_type", "archetype", "hidden"):
        assert f'"{key}"' not in text
    t = g.offseason_players  # 手続き中でなければ表は出せない
    with pytest.raises(ValueError):
        g.offseason_table("release")


# ---- 保存形式(版 10。F3-2b で版 11)と旧版 ----

def test_save_round_trip_and_v9_migration(games):
    g = api.Game(copy.deepcopy(games["strict"].state), dirty=False)
    g.advance(125)
    g.year_end()
    data = save_game(g.state)
    again = load_game(data)
    assert SAVE_FORMAT_VERSION == 12 and again.money_rule == "strict" and again.budget_tiers == g.state.budget_tiers and again.contract_rates == g.state.contract_rates
    assert all(p.contract == q.contract for p, q in zip(g.state.league.all_players(), again.league.all_players()))
    assert again.procedure.rate == g.state.procedure.rate and len(again.procedure.renewals) == len(g.state.procedure.renewals)
    # 版 9(契約なし)として読む:ルールは「なし」、契約は算定で補い、残りは 1〜3 年
    g2 = api.Game.new(3, [None] * 12, None, season_seed=7, baselines="default")
    g2.advance(125)
    g2.year_end()
    g2.advance(2)
    data = save_game(g2.state)
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 9
    state = json.loads(files["state.json"])
    for key in ("money_rule", "budget_tiers", "contract_rates"):
        del state[key]
    del state["configs"]["contracts"]
    del state["configs"]["negotiation"]
    for team in state["league"]["teams"]:
        for pd in team["players"]:
            del pd["contract"]
            del pd["preference"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    old = load_game(buf.getvalue())
    assert old.money_rule == "none" and old.budget_tiers == {} and "2" in old.contract_rates
    players = old.league.all_players()
    assert all(p.contract and p.contract["history"][0]["reason"] == "migrate" and 1 <= remaining_years(p.contract, old.year) <= 3 for p in players)
    twice = load_game(buf.getvalue())
    assert [p.contract for p in twice.league.all_players()] == [p.contract for p in players]  # 同じ乱数系列で同じ契約
    game = api.Game(old, dirty=False)
    game.advance(123)
    game.year_end()
    assert game.status()["year"] == 3 and all(p.contract["until"] >= 3 for p in game.state.league.all_players())
