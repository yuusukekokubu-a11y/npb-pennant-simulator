"""UI の整理①a(D-270〜D-273):オフの手続きの段階・契約の画面の表・「この段階をおまかせ」と「次の手続きへ」の意味・「なし」の年俸。"""

import copy
import json

import pytest

from pennant import api
from pennant.contracts import remaining_years
from pennant.savegame import load_game, save_game


@pytest.fixture(scope="module")
def contract_games():
    out = {}
    for rule in ("none", "standard"):
        g = api.Game.new(3, [None] * 12, 0, season_seed=11, baselines="default", money_rule=rule)
        g.advance(125)
        g.year_end()
        out[rule] = g
    return out


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def _picks(g, phase):
    return [x for x in g.state.procedure.picks if x["team_id"] == g.state.my_team_id and x["phase"] == phase and x["player_id"]]


def test_stages_and_contract_table(contract_games):
    g = _fresh(contract_games["none"])
    v = g.offseason_view()
    assert v["stage"] == "contract" and v["phase_label"] == "契約" and [x["label"] for x in v["phases"]] == ["契約", "FA", "ドラフト", "市場", "完了"]
    assert "note" not in v["renewal"] and not v["renewal"]["can_next"] and v["renewal"]["none_max_ratio"] == 1.3
    t = g.contract_table()
    assert [c["label"] for c in t["columns"]] == ["WAR", "ポジション", "年齢", "今の契約", "今回の提示", "状態", "出場"]  # 「全員」は共通の列だけ
    assert t["sort"]["key"] == "war_all" and t["order"] == "asc" and not t["kinds"]  # 初期は WAR の低い順
    team = g._team("T01")
    assert len(t["rows"]) == len(team.players) == v["renewal"]["players"]
    wars = [float(r["values"]["war_all"]) for r in t["rows"] if r["values"]["war_all"] not in ("—", "-")]
    assert wars == sorted(wars)
    assert {r["values"]["status"] for r in t["rows"]} <= {"未提示", "契約中"}
    # 種類を選べるのは、投手・捕手・内野手・外野手を選んだとき。選んだ指標は名前のすぐ右(D-272)
    p = g.contract_table("pitcher", "saber", "fip")
    assert [c["key"] for c in p["columns"]][:4] == ["fip", "pos", "age", "contract"] and len(p["kinds"]) == 3
    assert all(r["position"] in ("SP", "RP") for r in p["rows"])
    c = g.contract_table("catcher", "basic")
    assert c["rows"] and all(r["position"] == "C" for r in c["rows"]) and c["columns"][0]["key"] == "war"
    ages = [r["age"] for r in g.contract_table("all", sort="age", order="desc")["rows"]]
    assert ages == sorted(ages, reverse=True)
    with pytest.raises(ValueError):
        g.contract_table("bench")
    with pytest.raises(ValueError):
        g.contract_table("all", status="unknown")
    text = json.dumps(t, ensure_ascii=False)
    for key in ("ratings", "potential", "growth_type", "archetype", "preference"):
        assert f'"{key}"' not in text


def test_contract_stage_gate_release_and_ai_at_the_end(contract_games):
    g = _fresh(contract_games["standard"])
    proc = g.state.procedure
    with pytest.raises(ValueError, match="決まっていない"):
        g.offseason_next()
    g.offseason_renew_auto()
    st = g.contract_table("all", status="refused")
    assert all(r["values"]["status"].startswith("保留") for r in st["rows"])
    for r in st["rows"]:
        g.offseason_contract_release(r["player_id"])  # 交渉中の選手は、交渉をやめて自由契約
    released = g.contract_table("all", status="released")["rows"]
    assert {r["player_id"] for r in st["rows"]} <= {r["player_id"] for r in released} and all(not r["in_team"] and not r["can_release"] for r in released)
    # 契約が残る選手(複数年の途中。いなければ更改済)も、自由契約にできる(残りの契約は消える)
    rows = [r for r in g.contract_table("all", status="contracted")["rows"] + g.contract_table("all", status="accepted")["rows"] if r["can_release"]]
    target = next(p for p in g._team("T01").players if p.id == rows[0]["player_id"])
    assert remaining_years(target.contract, proc.year + 1) >= 1
    g.offseason_contract_release(target.id)
    assert target.contract is None and target in proc.market and g.contract_table("all")["rows"]
    assert not proc.budget_releases and not proc.ai_release_done  # AI の超過の解消と自由契約は、まだ(契約の段階の終わりに。D-272)
    while g.offseason_view()["renewal"]["over"] > 0:
        rows = [r for r in g.contract_table("all", sort="contract", order="desc")["rows"] if r["can_release"] and r["in_team"]]
        g.offseason_contract_release(rows[0]["player_id"])
    assert g.offseason_view()["renewal"]["can_next"]
    v = g.offseason_next()
    assert v["stage"] in ("fa", "draft") and proc.ai_release_done
    assert any(x["team_id"] != "T01" and x.get("note") not in ("negotiation", "budget") for x in proc.released)  # AI の自由契約


def test_stage_auto_and_next_semantics(contract_games):
    g = _fresh(contract_games["standard"])
    v = g.offseason_stage_auto()  # 契約の段階だけを AI の方針で(自球団の分も)
    log = v["auto_log"]
    assert log["stage"] == "contract" and log["items"] and v["stage"] in ("fa", "draft")
    assert all(x["kind"] in ("renew", "declare", "release") for x in log["items"])
    assert not g.state.procedure.negotiations or all(e["status"] != "pending" for e in g.state.procedure.negotiations.values())
    if v["stage"] == "fa":
        v = g.offseason_stage_auto()
        assert v["auto_log"]["stage"] == "fa" and v["stage"] == "draft"
    # 「次の手続きへ」は AI の代行をしない:ドラフトの自球団の番はパス
    v = g.offseason_next()
    assert v["stage"] == "market" and not _picks(g, "draft")
    assert any(x["team_id"] == "T01" and x["phase"] == "draft" and x["player_id"] is None for x in g.state.procedure.picks)
    r = g.offseason_stage_auto()  # 市場は、AI の方針で自球団も獲得して、完了まで
    assert r["finished"] and r["auto_log"]["stage"] == "market" and g.status()["year"] == 2
    assert all(len(t.players) == 70 for t in g.state.league.teams)
    # 同じ操作なら同じ結果
    g2 = _fresh(contract_games["standard"])
    g2.offseason_stage_auto()
    if g2.offseason_view()["stage"] == "fa":
        g2.offseason_stage_auto()
    g2.offseason_next()
    g2.offseason_stage_auto()
    assert [p.id for p in g2._team("T01").players] == [p.id for p in g._team("T01").players]


def test_fa_next_makes_no_more_offers(contract_games):
    g = _fresh(contract_games["standard"])
    g.offseason_stage_auto()
    proc = g.state.procedure
    if g.offseason_view()["stage"] != "fa":
        pytest.skip("宣言した選手がいない")
    g.offseason_next()
    assert proc.fa_done and not any(x["team_id"] == "T01" for x in proc.fa_log)  # 自球団は提示しない


def test_none_salary_range_and_reasons(contract_games):
    g = _fresh(contract_games["none"])
    proc = g.state.procedure
    e = next(e for e in proc.negotiations.values() if e["team_id"] == "T01")
    auto = int(e["auto_salary"])
    with pytest.raises(ValueError, match="倍"):
        g.offseason_offer(e["player_id"], 1, int(auto * 1.3) + 100)
    with pytest.raises(ValueError, match="低い"):
        g.offseason_offer(e["player_id"], 1, auto - 100)
    g.offseason_renew_auto()
    reasons = {o["reason"] for x in proc.negotiations.values() for o in x["offers"] if not o["accepted"]}
    assert "salary" in reasons  # 「なし」でも「年俸が低い」が出る(全球団の最初の提示。D-273)


def test_old_release_phase_resumes_as_contract(contract_games):
    """旧版で「自由契約」の段階にいたセーブは、「契約」の段階として再開する(D-272)。保存形式は版 12 のまま。"""
    g = _fresh(contract_games["none"])
    g.offseason_renew_auto()
    for e in [e for e in g.state.procedure.negotiations.values() if e["team_id"] == "T01" and e["status"] == "pending"]:
        g.offseason_renew_release(e["player_id"])
    g.state.procedure.phase = "release"  # 旧版の手続き(更改の後、自由契約の段階)
    again = api.Game(load_game(save_game(g.state)), dirty=False)
    v = again.offseason_view()
    assert v["phase"] == "release" and v["stage"] == "contract" and v["phase_label"] == "契約" and v["renewal"]["can_next"]
    assert again.contract_table()["rows"]
    v = again.offseason_next()
    assert v["stage"] in ("fa", "draft") and again.state.procedure.ai_release_done


def test_bridge_has_new_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_ui", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"stage_auto", "contract_release", "auto", "next"} <= set(bridge._OFFSEASON) and "contract_table" in bridge._QUERIES
