"""オフの手続きの契約の段階(UI の整理①a。D-270〜D-272。作り直し②で更改は査定の自動に。D-353〜D-361):
資金の増減・査定の更改と FA 宣言・契約の画面の表・資金の超過の関所・「この段階をおまかせ」と「次の手続きへ」の意味。"""

import copy
import json

import pytest

from pennant.facilities import team_upkeep

from pennant import api
from pennant.contracts import base_funds, changed_funds, contract_years, remaining_years, team_salary
from pennant.savegame import load_game, save_game


@pytest.fixture(scope="module")
def contract_game():
    g = api.Game.new(3, [None] * 12, 0, season_seed=11, baselines="default")
    funds_before = dict(g.state.team_funds)
    g.advance(125)
    g.year_end()
    g.funds_before = funds_before
    return g


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def _picks(g, phase):
    return [x for x in g.state.procedure.picks if x["team_id"] == g.state.my_team_id and x["phase"] == phase and x["player_id"]]


def _clear_overrun(g):
    while g.offseason_view()["renewal"]["over"] > 0:
        rows = [r for r in g.contract_table("all", sort="contract", order="desc")["rows"] if r["can_release"] and r["in_team"]]
        g.offseason_contract_release(rows[0]["player_id"])


@pytest.mark.slow
def test_funds_change_and_automatic_renewal(contract_game):
    g = _fresh(contract_game)
    state = g.state
    proc = state.procedure
    cs = state.contract_settings
    # 資金は前のシーズンの順位で増減した(D-354)
    for t in state.league.teams:
        assert state.team_funds[t.id] == changed_funds(contract_game.funds_before[t.id], state.budget_tiers[t.id], proc.ranks[t.id], cs)
    assert any(state.team_funds[t] != contract_game.funds_before[t] for t in state.team_funds)
    # 満了した全員が、全球団とも査定で更改済か FA 宣言(未決定はない。D-358、D-359)
    negs = list(proc.negotiations.values())
    assert negs and all(e["status"] in ("accepted", "declared") for e in negs)
    assert {e["team_id"] for e in negs if e["status"] == "accepted"} == {t.id for t in state.league.teams}
    players = {p.id: p for p in state.league.all_players()}
    for e in negs:
        if e["status"] != "accepted":
            assert e["context"].get("fa_holder") and e["player_id"] in proc.fa_info  # 宣言できるのは FA 権保持者だけ
            continue
        p = players[e["player_id"]]
        assert p.contract["salary"] == e["auto_salary"] and p.contract["until"] == proc.year + e["ai_years"] and p.contract["history"][-1]["reason"] == "renew"
        if e["old_salary"]:
            assert e["auto_salary"] >= e["old_salary"] * cs.decline_floor  # 1 年で下がるのは 25% まで(D-357)
        assert e["ai_years"] in {contract_years(e["age"], e["expected"] + d, cs) for d in (-0.005, 0.005)}  # 主力級だけ複数年(D-360。expected は小数 2 桁に丸めてある)
    assert any(e["ai_years"] > 1 for e in negs if e["status"] == "accepted")
    assert any(e["old_salary"] and e["auto_salary"] > e["old_salary"] for e in negs) and any(e["old_salary"] and e["auto_salary"] < e["old_salary"] for e in negs)
    assert 3 <= sum(1 for e in negs if e["status"] == "declared") <= 50  # 1 年目は初期選手の年数の補い方で多め(このシードは育成・外国人の後で 46。30 年の平均は年 12.5 人)


@pytest.mark.slow
def test_stages_and_contract_table(contract_game):
    g = _fresh(contract_game)
    v = g.offseason_view()
    assert v["stage"] == "contract" and v["phase_label"] == "契約" and [x["label"] for x in v["phases"]] == ["契約", "FA", "外国人", "ドラフト", "市場", "トレード", "現役ドラフト", "完了"]
    rn = v["renewal"]
    assert "note" not in rn and rn["can_next"] == (rn["over"] <= 0) and rn["cap"] == g.state.team_funds["T01"] - team_upkeep(g._team("T01"))  # 年俸に使える額 = 資金 − 設備の維持費(D-383)
    for gone in ("none_max_ratio", "max_offers", "unoffered", "axes", "projected_total"):
        assert gone not in rn
    t = g.contract_table()
    assert [c["label"] for c in t["columns"]] == ["WAR", "状態", "新しい年俸", "増減", "ポジション", "年齢", "今の契約", "出場"]  # 「全員」は共通の列だけ
    assert t["sort"]["key"] == "war_all" and t["order"] == "asc" and not t["kinds"]  # 初期は WAR の低い順
    team = g._team("T01")
    assert len(t["rows"]) == len(team.players) + rn["counts"]["declared"]
    wars = [float(r["values"]["war_all"]) for r in t["rows"] if r["values"]["war_all"] not in ("—", "-")]
    assert wars == sorted(wars)
    assert {r["values"]["status"] for r in t["rows"]} <= {"更改済", "契約中", "FA 宣言"}
    assert [x["key"] for x in t["statuses"]] == ["all", "accepted", "declared", "released", "contracted"]
    accepted = g.contract_table("all", status="accepted")["rows"]
    assert accepted and all(r["renewal"]["status"] == "accepted" and r["values"]["new"].endswith("年") for r in accepted)
    assert all("can_offer" not in r for r in t["rows"])
    # 種類を選べるのは、投手・捕手・内野手・外野手を選んだとき。選んだ指標は名前のすぐ右(D-272)
    p = g.contract_table("pitcher", "saber", "fip")
    assert [c["key"] for c in p["columns"]][:5] == ["fip", "status", "new", "change", "pos"] and len(p["kinds"]) == 3
    assert all(r["position"] in ("SP", "RP") for r in p["rows"])
    c = g.contract_table("catcher", "basic")
    assert c["rows"] and all(r["position"] == "C" for r in c["rows"]) and c["columns"][0]["key"] == "war"
    ages = [r["age"] for r in g.contract_table("all", sort="age", order="desc")["rows"]]
    assert ages == sorted(ages, reverse=True)
    with pytest.raises(ValueError):
        g.contract_table("bench")
    with pytest.raises(ValueError):
        g.contract_table("all", status="refused")
    text = json.dumps([t, v], ensure_ascii=False)
    for key in ("ratings", "potential", "growth_type", "archetype", "preference", "ease", "trajectory_bias"):
        assert f'"{key}"' not in text
    rt = g.offseason_table("renewal", "batter", "basic")
    assert [c["label"] for c in rt["columns"]][3:8] == ["前の年俸", "新しい年俸", "増減", "状態", "FA 権"]


@pytest.mark.slow
def test_overrun_gate_release_and_ai_at_the_end(contract_game):
    g = _fresh(contract_game)
    proc = g.state.procedure
    team = g._team("T01")
    # 資金を超えるようにして、関所を確かめる(D-361)
    g.state.team_funds["T01"] = team_salary(team) + team_upkeep(team) - 1000  # 維持費を引いた額を 1,000 万円超える(D-383)
    assert g.offseason_view()["renewal"]["over"] == 1000 and not g.offseason_view()["renewal"]["can_next"]
    with pytest.raises(ValueError, match="資金"):
        g.offseason_next()
    # 契約が残る選手(更改済・複数年の途中)も自由契約にできる(残りの契約は消える)
    rows = [r for r in g.contract_table("all", status="contracted")["rows"] + g.contract_table("all", status="accepted")["rows"] if r["can_release"]]
    target = next(p for p in team.players if p.id == rows[0]["player_id"])
    assert remaining_years(target.contract, proc.year + 1) >= 1
    g.offseason_contract_release(target.id)
    assert target.contract is None and target in proc.market
    assert not proc.budget_releases and not proc.ai_release_done  # AI の超過の解消と自由契約は、まだ(契約の段階の終わりに。D-272)
    _clear_overrun(g)
    assert g.offseason_view()["renewal"]["can_next"]
    v = g.offseason_next()
    assert v["stage"] in ("fa", "draft") and proc.ai_release_done
    assert all(team_salary(t) <= g.state.team_funds[t.id] for t in g.state.league.teams)  # AI 球団は資金の中に収めた
    assert all(x["team_id"] != "T01" for x in proc.budget_releases)
    assert any(x["team_id"] != "T01" and x.get("note") != "budget" for x in proc.released)  # AI の自由契約


@pytest.mark.slow
def test_released_after_renewal_counts_as_released(contract_game):
    """更改済の選手を自由契約にすると、状態の内訳でも「自由契約」に数える(更改の記録は更改済のまま)。"""
    g = _fresh(contract_game)
    before = g.offseason_view()["renewal"]["roster_counts"]
    row = next(r for r in g.contract_table("all", status="accepted")["rows"] if r["can_release"])
    g.offseason_contract_release(row["player_id"])
    after = g.offseason_view()["renewal"]["roster_counts"]
    assert after["released"] == before["released"] + 1 and after["accepted"] == before["accepted"] - 1


@pytest.mark.slow
def test_stage_auto_and_next_semantics(contract_game):
    g = _fresh(contract_game)
    v = g.offseason_stage_auto()  # 契約の段階だけを AI の方針で(自球団の分も。超過の解消と自由契約)
    log = v["auto_log"]
    assert log["stage"] == "contract" and v["stage"] in ("fa", "foreign")
    assert all(x["kind"] == "release" for x in log["items"])
    assert team_salary(g._team("T01")) <= g.state.team_funds["T01"]
    if v["stage"] == "fa":
        v = g.offseason_stage_auto()
        assert v["auto_log"]["stage"] == "fa" and v["stage"] == "foreign"
    v = g.offseason_stage_auto()  # 外国人市場(D-376)
    assert v["auto_log"]["stage"] == "foreign" and v["stage"] == "draft"
    # 「次の手続きへ」は AI の代行をしない:ドラフトの自球団の番はパス(育成ドラフトも。D-375)
    v = g.offseason_next()
    assert v["phase"] == "dev_draft" and v["stage"] == "draft"
    v = g.offseason_next()
    assert v["stage"] == "market" and not _picks(g, "draft") and not _picks(g, "dev_draft")
    assert any(x["team_id"] == "T01" and x["phase"] == "draft" and x["player_id"] is None for x in g.state.procedure.picks)
    v = g.offseason_stage_auto()  # 市場は、AI の方針で自球団も獲得して、トレードの段階へ(D-378)
    assert v["auto_log"]["stage"] == "market" and v["stage"] == "trade"
    v = g.offseason_stage_auto()  # トレード:提案は断ったことにして、現役ドラフトへ(D-380)
    assert v["auto_log"]["stage"] == "trade" and v["stage"] == "active_draft"
    r = g.offseason_stage_auto()  # 現役ドラフト:候補と指名を AI の方針で、完了まで
    assert r["finished"] and r["auto_log"]["stage"] == "active_draft" and g.status()["year"] == 2
    assert all(60 <= len(t.players) <= 70 for t in g.state.league.teams)  # 70 人は上限だけ(D-391)
    # 同じ操作なら同じ結果
    g2 = _fresh(contract_game)
    g2.offseason_stage_auto()
    if g2.offseason_view()["stage"] == "fa":
        g2.offseason_stage_auto()
    g2.offseason_stage_auto()  # 外国人市場
    g2.offseason_next()  # ドラフト
    g2.offseason_next()  # 育成ドラフト
    g2.offseason_stage_auto()
    g2.offseason_stage_auto()  # トレード
    g2.offseason_stage_auto()  # 現役ドラフト
    assert [p.id for p in g2._team("T01").players] == [p.id for p in g._team("T01").players]


@pytest.mark.slow
def test_fa_next_makes_no_more_offers(contract_game):
    g = _fresh(contract_game)
    g.offseason_stage_auto()
    proc = g.state.procedure
    if g.offseason_view()["stage"] != "fa":
        pytest.skip("宣言した選手がいない")
    g.offseason_next()
    assert proc.fa_done and not any(x["team_id"] == "T01" for x in proc.fa_log)  # 自球団は提示しない


@pytest.mark.slow
def test_whole_offseason_keeps_funds_and_save_round_trip(contract_game):
    g = _fresh(contract_game)
    again = api.Game(load_game(save_game(g.state)), dirty=False)  # 手続きの途中で保存しても同じ
    assert again.state.team_funds == g.state.team_funds and again.state.procedure.negotiations == g.state.procedure.negotiations
    g.offseason_auto()
    again.offseason_auto()
    assert [p.contract for p in g.state.league.all_players()] == [p.contract for p in again.state.league.all_players()]
    fills = [x for x in g.state.transactions if x["year"] == 1 and x["phase"] == "fill"]
    assert all(x["salary"] == g.state.contract_settings.minimum for x in fills)
    promoted = [x for x in g.state.transactions if x["year"] == 1 and x["phase"] == "promote"]
    for t in g.state.league.teams:  # 超えているのは、最低年俸の自動補充と、育成の年俸の分だけ(どちらも例外。D-235、D-375)
        over = team_salary(t) - g.state.team_funds[t.id]
        dev = sum(int(p.contract["salary"]) for p in t.development if p.contract)
        assert over <= sum(x["salary"] for x in fills if x["team_id"] == t.id) + dev + g.state.contract_settings.minimum * sum(1 for x in promoted if x["team_id"] == t.id)
    lo, hi = g.state.contract_settings.funds_range
    assert all(base_funds(g.state.budget_tiers[tid], g.state.contract_settings) * lo <= v <= base_funds(g.state.budget_tiers[tid], g.state.contract_settings) * hi for tid, v in g.state.team_funds.items())


def test_bridge_has_new_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_ui", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"stage_auto", "contract_release", "auto", "next"} <= set(bridge._OFFSEASON) and "contract_table" in bridge._QUERIES
    assert not {"renew_auto", "offer", "renew_release"} & set(bridge._OFFSEASON)  # 更改の提示はなくした(D-358)
