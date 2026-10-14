"""UI の整理①b(D-295〜D-299)と作り直し③(D-370):市場の希望と順番の方式・判断の画面の表(FA・ドラフト・市場)・加入後の序列・手薄なポジション・自球団の状況。"""

import copy
import io
import json
import zipfile

import pytest

from pennant import api
from pennant import fa as famod
from pennant import market as marketmod
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

HIDDEN_KEYS = ("ratings", "potential", "growth_type", "archetype", "preference", "ease", "trajectory_bias")


@pytest.fixture(scope="module")
def season_end():
    """自球団 T01 の 1 シーズン目を終えて年度を確定した直後(契約の段階)。"""
    g = api.Game.new(3, [None] * 12, 0, season_seed=11, baselines="default")
    g.advance(125)
    g.year_end()
    return g


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def _to_stage(g, stage):
    """その段階の入口まで進める(契約・FA は「この段階をおまかせ」、外国人市場とドラフトは自球団は取らずに空き枠と資金を残す)。"""
    while g.offseason_view()["stage"] != stage:
        if g.offseason_view()["stage"] in ("foreign", "draft"):
            g.offseason_next()
        else:
            g.offseason_stage_auto()
    return g


@pytest.mark.slow
def test_market_wishes_and_order(season_end):
    """年俸は自動、選手は断らない。各球団の希望を、前年の順位が低い球団から 1 人ずつ(D-370)。"""
    g = _to_stage(_fresh(season_end), "market")
    proc = g.state.procedure
    v = g.offseason_view()
    m = v["market"]
    assert m["uses_offers"] and not m["done"] and m["counts"]["pool"] == len(proc.market) > 0
    assert not v["is_my_turn"] and v["total_rounds"] == 0
    t = g.decision_table("market")
    assert [c["label"] for c in t["columns"]] == ["WAR", "総合", "年齢", "ポジション", "加入後の序列", "算定年俸", "出場", "前の所属", "状態"]
    cands = [r for r in t["rows"] if r["values"]["former"] == "候補"]
    pros = [r for r in t["rows"] if r["values"]["former"] != "候補"]
    assert cands and pros
    # 指名されなかった候補は評価と一言だけ、年俸は最低年俸(能力から決まる算定を見せない)。プロは能力が見える
    minimum = g.state.contract_settings.minimum
    assert all(r["amateur"] and r["values"]["ability"].startswith("評価 ") and r["deal"]["calc_salary"] == minimum and r["deal"]["expected"] is None for r in cands)
    assert all(not r["amateur"] and r["scouting"] is None and r["values"]["ability"][0] in "SABCDEFG" for r in pros)
    for tid in g.state.team_funds:  # 資金を超えていると希望を入れられないので、全球団に残りを作る(資金をほぼ使い切る球団が多い)
        g.state.team_funds[tid] += 100000
    room = g.budget_info("T01")["cap"] - g.budget_info("T01")["total"]
    cheap = [r for r in t["rows"] if r["deal"]["calc_salary"] <= room][:2]  # 希望は 1 人ずつ資金の中か見る(育成の年俸も総年俸に入るので、残りは少ないことがある)
    for r in cheap:
        v = g.offseason_market_offer(r["player_id"])
    assert [o["player_id"] for o in v["market"]["offers"]] == [r["player_id"] for r in cheap] and [o["rank"] for o in v["market"]["offers"]] == [1, 2]
    assert v["market"]["offers"][0]["years"] == 1
    status = {r["player_id"]: r["values"]["status"] for r in g.decision_table("market")["rows"]}
    assert status[cheap[0]["player_id"]] == "希望 1 番目"
    before = {t2.id: len(t2.players) for t2 in g.state.league.teams}
    v = g.offseason_market_close()
    signed = v["last_market"]["signed"]
    assert proc.market_done and v["market"]["done"] and signed == [x | {"team_name": g._team_names()[x["team_id"]], "salary_text": f"{x['salary']:,} 万円", "is_mine": x["team_id"] == "T01"} for x in proc.market_results]
    for x in signed:  # 獲得した選手は入団し、契約は市場(年俸は自動・1 年)
        p = next(p for p in g._team(x["team_id"]).players if p.id == x["player_id"])
        assert p.contract["salary"] == x["salary"] and p.contract["history"][-1]["reason"] == "market" and p.scouting is not None and x["years"] == 1
    # 順番:各周は前年の順位が低い球団から(周ごとに往復)。1 つの周で 1 球団 1 人まで
    for rnd in {x["round"] for x in signed}:
        teams = [x["team_id"] for x in signed if x["round"] == rnd]
        order = proc.order if rnd % 2 == 1 else list(reversed(proc.order))
        assert len(teams) == len(set(teams)) and teams == [tid for tid in order if tid in teams]
    # 自球団の 1 番目の希望は、ほかの球団に先に取られていなければ獲得できる
    first = cheap[0]["player_id"]
    holder = next((x["team_id"] for x in signed if x["player_id"] == first), None)
    assert holder is not None
    assert all(len(t2.players) <= 70 for t2 in g.state.league.teams)
    assert sum(len(t2.players) for t2 in g.state.league.teams) == sum(before.values()) + len(signed) + len(proc.promotions)  # AI は締めるときに育成選手も上げる(D-375)
    assert {x["team_id"] for x in proc.market_log} - {"T01"}  # AI 球団も希望を出す(FA と同じ方針)
    with pytest.raises(ValueError):
        g.offseason_market_offer(cheap[0]["player_id"])  # 締めた後は希望できない
    v = g.offseason_next()  # トレードの段階(D-378)
    assert v["stage"] == "trade"
    r = g.offseason_next()  # 完了:残った選手はリーグを去り、70 人まで自動補充
    assert r["finished"] and all(len(t2.players) == 70 for t2 in g.state.league.teams)
    rows = [x for x in g.state.transactions if x["phase"] == "market"]
    assert len(rows) == len(signed) and all(x["round"] >= 1 and x["player_id"] for x in rows)


@pytest.mark.slow
def test_market_ai_uses_the_fa_policy(season_end):
    """AI の市場の希望は FA と同じ方針(fa.choose_offers。一軍に入る見込みと見込みの WAR・空き枠・資金)。同じ操作なら同じ結果。"""
    g = _to_stage(_fresh(season_end), "market")
    proc = g.state.procedure
    ctx = g._contract_ctx()
    sizes = marketmod.league_sizes(g.state.league)
    fa = famod.fa_settings(ctx.negotiation)
    for team in g.state.league.teams[1:4]:
        wishes = marketmod.ai_wishes(team, proc, ctx, sizes)
        assert len(wishes) <= int(fa["ai_max_offers"])
        exps = []
        for pid in wishes:
            p = next(p for p in proc.market if p.id == pid)
            _, _, exp = marketmod.offer_terms(p, team.id, ctx)
            assert exp >= float(fa["ai_min_expected"])
            exps.append(exp)
        assert exps == sorted(exps, reverse=True)  # 見込みの高い順
    g2 = _to_stage(_fresh(season_end), "market")
    g.offseason_stage_auto()
    g2.offseason_stage_auto()
    assert [p.id for p in g._team("T05").players] == [p.id for p in g2._team("T05").players]


@pytest.mark.slow
def test_market_wish_needs_room_in_funds(season_end):
    g = _to_stage(_fresh(season_end), "market")
    room = g.budget_info("T01")["cap"] - g.budget_info("T01")["total"]
    rows = g.decision_table("market")["rows"]
    over = next((r for r in rows if r["deal"]["calc_salary"] > room), None)
    if over is not None:
        with pytest.raises(ValueError, match="資金"):
            g.offseason_market_offer(over["player_id"])
    g.state.team_funds["T01"] += 100000  # 残りを作る(資金を超えていることがある)
    room = g.budget_info("T01")["cap"] - g.budget_info("T01")["total"]
    ok = next(r for r in rows if r["deal"]["calc_salary"] <= room)
    v = g.offseason_market_offer(ok["player_id"])
    assert v["market"]["offers"][0]["salary"] == ok["deal"]["calc_salary"]
    v = g.offseason_market_offer(ok["player_id"])  # 2 回入れても 1 つ
    assert len(v["market"]["offers"]) == 1
    v = g.offseason_market_cancel(ok["player_id"])
    assert v["market"]["offers"] == []


@pytest.mark.slow
def test_decision_tables_columns_depth_and_thin(season_end):
    g = _fresh(season_end)
    g.offseason_stage_auto()  # 契約 → FA(宣言した選手がいなければドラフト)
    v = g.offseason_view()
    if v["stage"] == "fa":
        t = g.decision_table("fa")
        assert [c["label"] for c in t["columns"]] == ["WAR", "総合", "年齢", "ポジション", "加入後の序列", "算定年俸", "出場", "前の所属", "状態"]
        assert t["sort"]["key"] == "war_all" and t["order"] == "desc" and not t["kinds"]
        p = g.decision_table("fa", "pitcher", "saber")
        assert p["kinds"] and all(r["position"] in ("SP", "RP") for r in p["rows"]) and p["columns"][0]["key"] == "war_ra"
        # 加入後の序列は、FA の点数の出場機会と同じ計算(D-297、D-365)
        ctx = g._contract_ctx()
        team = g._my_team()
        sizes = marketmod.league_sizes(g.state.league)
        for r in t["rows"][:5]:
            player = next(x for x in g.state.procedure.fa_pool if x.id == r["player_id"])
            c = famod._context(player, team, g.state.procedure, ctx, sizes)
            assert r["values"]["depth"].startswith(f"{c['rank'] + 1} 番手") and r["in_slots"] == (c["rank"] < c["slots"])
        g.offseason_stage_auto()
    if g.offseason_view()["stage"] == "foreign":  # 外国人市場(D-376)
        f = g.decision_table("foreign")
        assert [c["label"] for c in f["columns"]] == ["評価", "スカウトの一言", "年齢", "ポジション", "加入後の序列", "年俸", "状態"]
        g.offseason_next()
    d = g.decision_table("draft")
    assert [c["label"] for c in d["columns"]] == ["評価", "スカウトの一言", "年齢", "出身", "ポジション", "加入後の序列"]
    assert d["sort"]["key"] == "grade" and d["show"] == "open" and not d["kinds"] and not d["seasons"]
    grades = [r["scouting"]["grade"] for r in d["rows"]]
    assert grades == sorted(grades, key="ABCDE".index)  # A が先
    ages = [r["age"] for r in g.decision_table("draft", sort="age", order="asc")["rows"]]
    assert ages == sorted(ages)
    grades = [r["scouting"]["grade"] for r in g.decision_table("draft", sort="grade", order="asc")["rows"]]
    assert grades == sorted(grades, key="EDCBA".index)  # 逆順は E が先
    catchers = g.decision_table("draft", "catcher")
    assert catchers["rows"] and all(r["position"] == "C" for r in catchers["rows"])
    for pos, info in d["thin"].items():  # 手薄の印(D-303):人数が目安より 2 人以上少ない、または一軍相当の見込みが下位 4 球団
        assert info["reasons"] and all(r["values"]["pos"].endswith("◆") for r in d["rows"] if r["position"] == pos)
    o = g.team_outlook()
    assert len(o["positions"]) == 10 and o["space"] == 70 - len(g._my_team().players)
    assert {r["position"] for r in o["positions"] if r["thin"]} == set(d["thin"])
    with pytest.raises(ValueError):
        g.decision_table("market")  # 今の段階ではない表は出せない
    text = json.dumps([d, o], ensure_ascii=False)
    for key in HIDDEN_KEYS:
        assert f'"{key}"' not in text


def test_market_uses_wishes_whenever_contracts_exist():
    """契約の文脈があれば(事前運転も)希望と順番の方式(D-370)。契約の文脈がない古い形の呼び出しだけ、指名の順番の方式。"""
    assert not marketmod.uses_offers(None)

    class Ctx:
        negotiation = None

    assert marketmod.uses_offers(Ctx())


def test_bridge_has_market_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_market", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"market_offer", "market_cancel", "market_close"} <= set(bridge._OFFSEASON) and {"decision_table", "team_outlook"} <= set(bridge._QUERIES)
