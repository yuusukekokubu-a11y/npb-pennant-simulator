"""契約の土台(F3-2a。D-232〜D-236)と、作り直し②の資金・査定(D-353〜D-357、D-360)の確認。
更改の流れ(自動更改・FA 宣言・資金の超過の解消・契約の画面)は test_renewal.py。"""

import copy
import io
import json
import statistics
import zipfile

import pytest

from pennant.facilities import team_upkeep

from pennant import api
from pennant.config import ConfigError
from pennant.contracts import (
    TIERS,
    ability_war,
    age_factor,
    assessed_salary,
    assign_tiers,
    base_funds,
    changed_funds,
    contract_years,
    expected_war,
    history_parts,
    history_war,
    initial_funds,
    is_expiring,
    load_contract_settings,
    rate_for,
    remaining_years,
    salary_for,
    team_salary,
    validate_contract_settings,
)
from pennant.savegame import SAVE_FORMAT_VERSION, SaveDataError, load_game, save_game

CS = load_contract_settings()


# ---- 設定と算定の式 ----

def test_settings_and_validation():
    assert CS.minimum == 500 and CS.base_budget == 400000 and CS.payroll_divisor == 1.1 and CS.decline_floor == 0.75
    assert [CS.tier_factor(t) for t in TIERS] == [1.2, 1.0, 0.8] and CS.tier_counts == {"large": 3, "medium": 6, "small": 3}
    assert CS.change_by_rank == [0.05, 0.03, 0.01, -0.01, -0.03, -0.05] and CS.funds_range == (0.85, 1.15)
    assert CS.rookie_salary(1) == 1500 and CS.rookie_salary(6) == 500 and CS.rookie_salary(9) == 500 and CS.rookie_years == 1 and CS.default_years == 1 and CS.max_years == 5
    assert "rules" not in CS.data and "scouting_war" not in CS.data  # お金のルールとスカウト評価からの算定はなくした(D-353、D-356)
    for path, value, message in [
        (("budget", "tier_counts", "large"), 5, "tier_counts"),
        (("budget", "range"), [1.2, 1.1], "budget.range"),
        (("budget", "change_by_rank"), [], "budget.change_by_rank"),
        (("salary", "decline_floor"), 1.5, "salary.decline_floor"),
        (("years", "multi_year", "table"), [[2.0, "2"]], "years.multi_year.table"),
    ]:
        data = copy.deepcopy(CS.data)
        node = data
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        with pytest.raises(ConfigError, match=message):
            validate_contract_settings(data)


def test_expected_war_mixes_history_and_abilities():
    # 成績の分:新しいほど重く、出場の少ない年は軽い
    assert history_war([(3.0, 500), (1.0, 500), (2.0, 500)], "batter", CS) == pytest.approx(0.5 * 3 + 0.3 * 1 + 0.2 * 2)
    assert history_war([(3.0, 30), (1.0, 600)], "batter", CS) == pytest.approx((0.5 * 0.1 * 3 + 0.3 * 1.0 * 1) / (0.5 * 0.1 + 0.3))
    assert history_war([], "batter", CS) is None and history_war([(2.0, 0)], "pitcher", CS) is None
    assert history_parts([(2.0, 600)], "batter", CS) == pytest.approx((1.0, 0.5))
    # 能力の分:見せる能力の平均が高いほど大きい
    assert ability_war("batter", 60, CS) > ability_war("batter", 50, CS) > ability_war("batter", 40, CS)
    # 成績がなければ能力だけ、満の出場なら成績が中心(能力の重みは 0.25。D-356)
    exp, source = expected_war(24, "batter", [], 60, CS)
    assert source == "ability" and exp == pytest.approx(ability_war("batter", 60, CS))
    exp, source = expected_war(28, "batter", [(4.0, 600), (4.0, 600), (4.0, 600)], 50, CS)
    k = CS.ability_weight
    assert source == "history" and exp == pytest.approx((4.0 + k * ability_war("batter", 50, CS)) / (1.0 + k))
    few = expected_war(28, "batter", [(4.0, 30)], 50, CS)[0]  # 出場が少ないと能力に寄る
    assert ability_war("batter", 50, CS) < few < 4.0 and few < exp
    assert expected_war(35, "pitcher", [], None, CS) == (0.0, "none")
    # 年齢の割引
    assert age_factor(29, CS) == 1.0 and age_factor(30, CS) == pytest.approx(0.92) and age_factor(40, CS) == pytest.approx(0.4)
    assert expected_war(35, "batter", [], 60, CS)[0] == pytest.approx(ability_war("batter", 60, CS) * age_factor(35, CS))


def test_salary_floor_years_and_rate():
    # 年俸:最低年俸 + 単価 × 見込み(下限は最低年俸。100 万円に丸める)
    assert salary_for(-1.0, 14000, CS) == 500 and salary_for(2.0, 14000, CS) == 28500 and salary_for(0.004, 14000, CS) == 600
    # 査定:前の年俸の 75% を下回らない(切り上げ。D-357)。上がる幅に上限はない
    assert assessed_salary(0.0, 14000, CS, 20000) == 15000 and assessed_salary(0.0, 14000, CS, 10100) == 7600
    assert assessed_salary(5.0, 14000, CS, 1000) == salary_for(5.0, 14000, CS) and assessed_salary(1.0, 14000, CS, None) == salary_for(1.0, 14000, CS)
    # 契約年数:主力級(見込み 2.0 以上・32 歳以下)は 2〜3 年、ほかは 1 年(D-360)
    assert contract_years(25, 3.2, CS) == 3 and contract_years(30, 2.5, CS) == 2 and contract_years(32, 2.0, CS) == 2
    assert contract_years(33, 5.0, CS) == 1 and contract_years(22, 1.9, CS) == 1
    # 単価:(資金の合計 ÷ 1.1 − 最低年俸 × 選手数)÷ Σ max(0, 見込み)(D-355)
    assert rate_for([3.0, 2.0, -1.0], 4800000, 840, CS) == pytest.approx((4800000 / 1.1 - 500 * 840) / 5.0)
    assert rate_for([0.0, -1.0], 4800000, 840, CS) == 0.0
    assert remaining_years({"salary": 500, "until": 3}, 2) == 2 and remaining_years({"salary": 500, "until": 1}, 2) == 0 and remaining_years(None, 1) == 0
    assert is_expiring({"salary": 500, "until": 1}, 1) and not is_expiring({"salary": 500, "until": 2}, 1) and is_expiring(None, 1)


def test_funds_by_tier_and_rank():
    assert [base_funds(t, CS) for t in TIERS] == [480000, 400000, 320000] and base_funds(None, CS) == 400000
    ids = [f"T{i:02d}" for i in range(1, 13)]
    tiers = assign_tiers(ids, 7, CS)
    assert sorted(tiers.values()).count("large") == 3 and sorted(tiers.values()).count("small") == 3 and len(tiers) == 12
    assert tiers == assign_tiers(ids, 7, CS) and tiers != assign_tiers(ids, 8, CS)  # シードから決まる
    assert sum(initial_funds(tiers, CS).values()) == 400000 * 12
    # 順位で増減(1 位 +5% 〜 6 位 −5%)。規模の基準額の 0.85〜1.15 倍に収める(D-354)
    assert changed_funds(400000, "medium", 1, CS) == 420000 and changed_funds(400000, "medium", 6, CS) == 380000 and changed_funds(400000, "medium", 4, CS) == 396000
    assert changed_funds(455000, "medium", 1, CS) == 460000 and changed_funds(342000, "medium", 6, CS) == 340000
    assert changed_funds(320000, "small", None, CS) == 320000  # 順位がない(事前運転)


# ---- ゲームの流れ ----

@pytest.fixture(scope="module")
def game():
    return api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default")


@pytest.mark.slow
def test_new_game_has_contracts_within_funds(game):
    state = game.state
    assert "money_rule" not in game.status()
    assert sorted(set(state.budget_tiers.values())) == ["large", "medium", "small"] and state.team_funds == initial_funds(state.budget_tiers, CS)
    assert all(p.contract and p.contract["salary"] >= CS.minimum and p.contract["until"] >= 1 for p in state.league.all_players())
    assert all(h["reason"] == "initial" for p in state.league.all_players() for h in p.contract["history"])
    assert state.contract_rates["1"] > 0
    totals = [team_salary(t) for t in state.league.teams]
    assert abs(statistics.fmean(totals) - 400000 / 1.1) / 400000 < 0.1  # 全球団の平均は資金の平均の約 9 割
    assert all(team_salary(t) + team_upkeep(t) <= state.team_funds[t.id] for t in state.league.teams)  # 開始時は資金(設備の維持費を引いた額)の中
    info = game.budget_info("T01")
    assert info["cap"] == state.team_funds["T01"] - info["upkeep"] and info["funds"] == state.team_funds["T01"] and info["tier_label"] in ("大", "中", "小") and info["hard"] and "rule" not in info


def test_salary_ignores_hidden_info(game):
    """隠し情報(伸びやすさ・成長タイプ・型)を変えても、見せる能力と成績が同じなら年俸は変わらない(D-232、D-356)。"""
    from pennant.draft import ContractContext, state_war_history

    state = game.state
    p = state.league.teams[0].players[0]
    ctx = ContractContext(state.contract_settings, {}, {}, state_war_history(state), state.scout_sd_of)
    ctx.rate = 14000.0
    before = ctx.salary(p)
    p2 = copy.deepcopy(p)
    p2.hidden.ease = 1.7
    p2.hidden.growth_type = "late" if p2.hidden.growth_type != "late" else "early"
    p2.hidden.trajectory_bias = 9.0
    ctx2 = ContractContext(state.contract_settings, {}, {}, state_war_history(state), state.scout_sd_of)
    ctx2.rate = 14000.0
    assert ctx2.salary(p2) == before
    p3 = copy.deepcopy(p)
    p3.abilities = {k: v + 10 for k, v in p3.abilities.items()}  # 見せる能力が上がれば年俸も上がる
    ctx3 = ContractContext(state.contract_settings, {}, {}, state_war_history(state), state.scout_sd_of)
    ctx3.rate = 14000.0
    assert ctx3.salary(p3)[0] > before[0]


def test_contract_pages_are_public_and_without_hidden_info(game):
    p = game.state.league.teams[1].players[0]
    page = game.player(p.id)
    c = page["player"]["contract"]
    assert c["salary"] == p.contract["salary"] and c["remaining"] >= 1 and c["history"][0]["reason_label"] == "開始時"
    team = game.team("T02")
    assert team["budget"]["tier_label"] in ("大", "中", "小") and team["budget"]["usage"] <= 100 and team["salaries"][0]["salary"] >= team["salaries"][-1]["salary"]
    text = json.dumps([page, team, game.budget_info("T02")], ensure_ascii=False)
    for key in ("ratings", "potential", "growth_type", "archetype", "hidden", "ease", "trajectory_bias", "preference"):
        assert f'"{key}"' not in text
    with pytest.raises(ValueError):
        game.offseason_table("release")  # 手続き中でなければ表は出せない


# ---- 保存形式(版 15。D-362) ----

def test_save_has_funds_and_old_versions_are_rejected(game):
    data = save_game(game.state)
    again = load_game(data)
    assert SAVE_FORMAT_VERSION == 30 and again.budget_tiers == game.state.budget_tiers and again.team_funds == game.state.team_funds and again.contract_rates == game.state.contract_rates
    assert all(p.contract == q.contract for p, q in zip(game.state.league.all_players(), again.league.all_players()))
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    state = json.loads(files["state.json"])
    assert "money_rule" not in state and set(state["team_funds"]) == {t.id for t in game.state.league.teams}

    def rezip(change):
        man, st = json.loads(files["manifest.json"]), json.loads(files["state.json"])
        change(man, st)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, b in files.items():
                z.writestr(n, json.dumps(man) if n == "manifest.json" else json.dumps(st) if n == "state.json" else b)
        return buf.getvalue()

    with pytest.raises(SaveDataError, match="作り直しの前の版"):
        load_game(rezip(lambda m, s: m.update(format_version=15)))
    with pytest.raises(SaveDataError, match="team_funds"):
        load_game(rezip(lambda m, s: s["team_funds"].pop("T01")))
    with pytest.raises(SaveDataError, match="team_funds"):
        load_game(rezip(lambda m, s: s["team_funds"].update(T01=-5)))
