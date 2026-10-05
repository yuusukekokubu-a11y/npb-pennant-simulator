"""UI の整理①b(D-295〜D-301):市場の提示の方式・判断の画面の表(FA・ドラフト・市場)・加入後の序列・手薄なポジション・自球団の状況。"""

import copy
import io
import json
import zipfile

import pytest

from pennant import api
from pennant import fa as famod
from pennant import market as marketmod
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

HIDDEN_KEYS = ("ratings", "potential", "growth_type", "archetype", "preference")


@pytest.fixture(scope="module")
def season_end():
    """お金のルール「標準」で、自球団 T01 の 1 シーズン目を終えて年度を確定した直後(契約の段階)。"""
    g = api.Game.new(3, [None] * 12, 0, season_seed=11, baselines="default", money_rule="standard")
    g.advance(125)
    g.year_end()
    return g


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def _to_stage(g, stage):
    """その段階の入口まで進める(契約・FA は「この段階をおまかせ」、ドラフトは自球団の番をパスして空き枠を残す)。"""
    while g.offseason_view()["stage"] != stage:
        if g.offseason_view()["stage"] == "draft":
            g.offseason_next()
        else:
            g.offseason_stage_auto()
    return g


@pytest.mark.slow
def test_market_is_one_round_of_offers(season_end):
    g = _to_stage(_fresh(season_end), "market")
    proc = g.state.procedure
    v = g.offseason_view()
    m = v["market"]
    assert m["uses_offers"] and not m["done"] and m["counts"]["pool"] == len(proc.market) > 0
    assert not v["is_my_turn"] and v["total_rounds"] == 0  # 指名の順番はない(D-300)
    t = g.decision_table("market")
    assert [c["label"] for c in t["columns"]] == ["WAR", "総合(推定)", "天井", "年齢", "ポジション", "加入後の序列", "算定年俸", "出場", "前の所属", "状態"]
    assert {r["values"]["former"] for r in t["rows"]} >= {"候補"}  # 指名されなかった候補
    cheap = next(r for r in t["rows"] if r["deal"]["calc_salary"] <= 2000)
    v = g.offseason_market_offer(cheap["player_id"], 2)
    assert v["market"]["offers"][0]["player_id"] == cheap["player_id"] and v["market"]["offers"][0]["years"] == 2
    with pytest.raises(ValueError, match="年数"):
        g.offseason_market_offer(cheap["player_id"], 9)
    before = {t2.id: len(t2.players) for t2 in g.state.league.teams}
    v = g.offseason_market_close()
    signed = v["last_market"]["signed"]
    assert proc.market_done and v["market"]["done"] and signed == [x | {"team_name": g._team_names()[x["team_id"]], "salary_text": f"{x['salary']:,} 万円", "is_mine": x["team_id"] == "T01"} for x in proc.market_results]
    for x in signed:  # 成立した選手は入団し、契約は市場(提示した年数と年俸)
        p = next(p for p in g._team(x["team_id"]).players if p.id == x["player_id"])
        assert p.contract["salary"] == x["salary"] and p.contract["history"][-1]["reason"] == "market" and p.scouting is not None
    assert all(len(t2.players) <= 70 for t2 in g.state.league.teams)
    assert sum(len(t2.players) for t2 in g.state.league.teams) == sum(before.values()) + len(signed)
    assert {x["team_id"] for x in proc.market_log} - {"T01"}  # AI 球団も提示する(FA と同じ方針。D-300)
    with pytest.raises(ValueError):
        g.offseason_market_offer(cheap["player_id"], 1)  # 締めた後は提示できない
    r = g.offseason_next()  # 完了:残った選手はリーグを去り、70 人まで自動補充
    assert r["finished"] and all(len(t2.players) == 70 for t2 in g.state.league.teams)
    rows = [x for x in g.state.transactions if x["phase"] == "market"]
    assert len(rows) == len(signed) and all(x["round"] == 1 and x["player_id"] for x in rows)


@pytest.mark.slow
def test_market_ai_uses_the_fa_policy(season_end):
    """AI の市場の提示は FA と同じ方針(fa.choose_offers。一軍に入る見込みと見込みの WAR・空き枠・予算)。同じ操作なら同じ結果。"""
    g = _to_stage(_fresh(season_end), "market")
    proc = g.state.procedure
    ctx = g._contract_ctx()
    sizes = marketmod.league_sizes(g.state.league)
    fa = famod.fa_settings(ctx.negotiation)
    for team in g.state.league.teams[1:4]:
        offers = marketmod.ai_market_offers(team, proc, ctx, sizes)
        assert len(offers) <= int(fa["ai_max_offers"])
        for pid, (years, salary) in offers.items():
            p = next(p for p in proc.market if p.id == pid)
            calc, _, exp = marketmod.offer_terms(p, team.id, ctx)
            assert exp >= float(fa["ai_min_expected"]) and salary == calc and 1 <= years <= 5
    g2 = _to_stage(_fresh(season_end), "market")
    g.offseason_stage_auto()
    g2.offseason_stage_auto()
    assert [p.id for p in g._team("T05").players] == [p.id for p in g2._team("T05").players]


@pytest.mark.slow
def test_none_rule_market_salary_range():
    g = api.Game.new(3, [None] * 12, 0, season_seed=11, baselines="default", money_rule="none")
    g.advance(125)
    g.year_end()
    _to_stage(g, "market")
    row = g.decision_table("market")["rows"][0]
    calc = row["deal"]["calc_salary"]
    with pytest.raises(ValueError, match="倍"):
        g.offseason_market_offer(row["player_id"], 1, int(calc * 1.3) + 1000)
    with pytest.raises(ValueError, match="低い"):
        g.offseason_market_offer(row["player_id"], 1, calc - 100)
    v = g.offseason_market_offer(row["player_id"], 1, int(calc * 1.2))
    assert v["market"]["offers"][0]["salary"] >= calc


@pytest.mark.slow
def test_decision_tables_columns_depth_and_thin(season_end):
    g = _fresh(season_end)
    g.offseason_stage_auto()  # 契約 → FA(宣言した選手がいなければドラフト)
    v = g.offseason_view()
    if v["stage"] == "fa":
        t = g.decision_table("fa")
        assert [c["label"] for c in t["columns"]] == ["WAR", "年齢", "ポジション", "加入後の序列", "算定年俸", "出場", "前の所属", "状態"]
        assert t["sort"]["key"] == "war_all" and t["order"] == "desc" and not t["kinds"]
        p = g.decision_table("fa", "pitcher", "saber")
        assert p["kinds"] and all(r["position"] in ("SP", "RP") for r in p["rows"]) and p["columns"][0]["key"] == "war_ra"
        # 加入後の序列は、志望の出場機会の軸と同じ計算(D-297)
        ctx = g._contract_ctx()
        team = g._my_team()
        sizes = marketmod.league_sizes(g.state.league)
        for r in t["rows"][:5]:
            player = next(x for x in g.state.procedure.fa_pool if x.id == r["player_id"])
            c = famod._context(player, team, g.state.procedure, ctx, sizes)
            assert r["values"]["depth"].startswith(f"{c['rank'] + 1} 番手") and r["in_slots"] == (c["rank"] < c["slots"])
        g.offseason_stage_auto()
    d = g.decision_table("draft")
    assert [c["label"] for c in d["columns"]] == ["総合(推定)", "天井", "年齢", "出身", "ポジション", "加入後の序列"]
    assert d["sort"]["key"] == "overall" and d["show"] == "open" and not d["kinds"] and not d["seasons"]
    overall = [r["scouting"]["overall"] for r in d["rows"]]
    assert overall == sorted(overall, reverse=True)
    ages = [r["age"] for r in g.decision_table("draft", sort="age", order="asc")["rows"]]
    assert ages == sorted(ages)
    grades = [r["scouting"]["ceiling"] for r in g.decision_table("draft", sort="ceiling", order="desc")["rows"]]
    assert grades == sorted(grades, key="SABCD".index)  # S が先
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


@pytest.mark.slow
def test_old_save_in_the_middle_of_the_waiver_market_resumes_with_offers(season_end):
    """版 12 で市場の段階(指名の途中)にいたセーブは、版 13 の提示の方式で再開できる(D-300)。それまでの獲得はそのまま。"""
    g = _to_stage(_fresh(season_end), "market")
    proc = g.state.procedure
    proc.round, proc.index = 2, 3  # 旧方式の巡の途中
    old_pick = {"phase": "market", "round": 1, "team_id": "T02", "player_id": None, "name": "", "role": "", "position": "", "age": None, "note": "pass"}
    proc.picks.append(old_pick)
    data = save_game(g.state)
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    state = json.loads(files["state.json"])
    manifest["format_version"] = 12
    for key in ("market_offers", "market_results", "market_log", "market_done"):
        state["procedure"].pop(key)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, b in files.items():
            z.writestr(n, json.dumps(manifest).encode() if n == "manifest.json" else json.dumps(state).encode() if n == "state.json" else b)
    again = api.Game(load_game(out.getvalue()), dirty=False)
    assert SAVE_FORMAT_VERSION == 13
    v = again.offseason_view()
    assert v["stage"] == "market" and not v["market"]["done"] and v["market"]["counts"]["pool"] == len(proc.market)
    assert old_pick in again.state.procedure.picks
    r = again.offseason_next()
    assert r["finished"] and again.state.year == 2


def test_market_needs_negotiation_settings():
    """志望の判定の設定がない手続き(事前運転。D-254)では、提示の方式は使わない(今までの順番の方式。D-301)。"""
    assert not marketmod.uses_offers(None)

    class Ctx:
        negotiation = None

    assert not marketmod.uses_offers(Ctx())
    Ctx.negotiation = object()
    assert marketmod.uses_offers(Ctx())


def test_bridge_has_market_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_market", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"market_offer", "market_cancel", "market_close"} <= set(bridge._OFFSEASON) and {"decision_table", "team_outlook"} <= set(bridge._QUERIES)
