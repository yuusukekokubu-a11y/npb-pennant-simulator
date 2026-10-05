"""F3-2b(契約更改:単年が基本・複数年・志望の判定。D-242〜D-253)の確認。"""

import copy
import io
import json
import zipfile

import pytest

from pennant import answers, api
from pennant.config import ConfigError
from pennant.contracts import load_contract_settings, remaining_years
from pennant.generate import generate_league
from pennant.config import load_generation_config, load_name_parts
from pennant.negotiation import (
    ai_offer,
    ai_years,
    depth_ranks,
    depth_slots,
    draw_preference,
    judge,
    load_negotiation_settings,
    multi_year_bonus,
    satisfaction,
    standing_ranks,
    validate_negotiation_settings,
)
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

NEG = load_negotiation_settings()
CS = load_contract_settings()
HIDDEN_KEYS = ("ratings", "potential", "growth_type", "archetype", "preference")


# ---- 設定と志望 ----

def test_settings_and_validation():
    assert NEG.max_offers == 3 and list(NEG.axes) == ["salary", "playing_time", "winning"]
    assert NEG.reason("salary") == "年俸が低い" and NEG.reason("playing_time") == "出場機会が見込めない" and NEG.reason("winning") == "優勝を争えない"
    data = copy.deepcopy(NEG.data)
    data["axes"]["winning"]["kind"] = "money"
    with pytest.raises(ConfigError, match="kind"):
        validate_negotiation_settings(data)
    data = copy.deepcopy(NEG.data)
    data["ai"]["raises"] = [1.0]
    with pytest.raises(ConfigError, match="raises"):
        validate_negotiation_settings(data)
    # 軸はあとから足せる(設定に書くだけで、種類の関数は共通)
    data = copy.deepcopy(NEG.data)
    data["axes"]["home"] = {"label": "地元", "reason": "地元を離れたくない", "kind": "standing", "weight_shape": 1.0, "strength": 0.1, "needs_money": False}
    more = validate_negotiation_settings(data)
    assert set(draw_preference("X1", 5, more)) == {"salary", "playing_time", "winning", "home"}


def test_preference_is_separate_stream_and_deterministic():
    w = draw_preference("B01C001", 7, NEG)
    assert w == draw_preference("B01C001", 7, NEG) and w != draw_preference("B01C002", 7, NEG) and w != draw_preference("B01C001", 8, NEG)
    assert abs(sum(w.values()) - 1.0) < 1e-3 and all(v >= 0 for v in w.values())
    # 志望を付けても、選手の生成(能力)は変わらない(別の乱数系列。D-245)
    config, parts = load_generation_config(), load_name_parts()
    a, b = generate_league(3, config, parts), generate_league(3, config, parts)
    from pennant.negotiation import ensure_preferences

    ensure_preferences(a.all_players(), a.seed, NEG)
    assert [p.ratings for p in a.all_players()] == [p.ratings for p in b.all_players()]
    assert all(p.preference for p in a.all_players()) and all(p.preference is None for p in b.all_players())


def test_satisfaction_and_judge():
    ctx = {"rank": 0, "slots": 2.0, "standing": 1, "league_size": 6}
    assert satisfaction("salary", NEG, 1000, 1000, ctx) == pytest.approx(-0.2)  # 算定どおりはやや不満
    assert satisfaction("salary", NEG, 1250, 1000, ctx) == pytest.approx(0.8) and satisfaction("salary", NEG, 3000, 1000, ctx) == 1.0
    assert satisfaction("playing_time", NEG, 1000, 1000, ctx) == 1.0 and satisfaction("playing_time", NEG, 1000, 1000, {**ctx, "rank": 2}) == 0.0
    assert satisfaction("playing_time", NEG, 1000, 1000, {**ctx, "rank": 9}) == -1.0
    assert satisfaction("winning", NEG, 1000, 1000, ctx) == 1.0 and satisfaction("winning", NEG, 1000, 1000, {**ctx, "standing": 6}) == -1.0
    assert satisfaction("winning", NEG, 1000, 1000, {**ctx, "standing": None}) == 0.0  # 順位がない(事前運転)
    pref = {"salary": 0.5, "playing_time": 0.3, "winning": 0.2}
    bad = {"rank": 9, "slots": 2.0, "standing": 6, "league_size": 6}
    r1 = judge(NEG, pref, 1, 1000, 1000, 25, bad, "loose", 0.0)
    r2 = judge(NEG, pref, 1, 1300, 1000, 25, bad, "loose", 0.0)
    assert r2["score"] > r1["score"] and r1["reason"] == "playing_time"
    # 「なし」でも年俸の軸が効き、「ゆるい」と同じ判定(D-273)。設定 money_none.salary_axis を false にすると、以前どおり効かない(D-246)
    heavy = {"salary": 0.9, "playing_time": 0.05, "winning": 0.05}
    good = {**bad, "rank": 0, "standing": 1}
    r3 = judge(NEG, heavy, 1, 1000, 1000, 25, good, "none", 0.0)
    r4 = judge(NEG, heavy, 1, 1000, 1000, 25, good, "loose", 0.0)
    assert r4["reason"] == "salary" and r3 == r4
    off = copy.deepcopy(NEG.data)
    off["money_none"] = {"salary_axis": False, "max_ratio": 1.3}
    old_neg = validate_negotiation_settings(off)
    r5 = judge(old_neg, heavy, 1, 1000, 1000, 25, good, "none", 0.0)
    assert "salary" not in r5["values"] and r5["reason"] != "salary"
    with pytest.raises(ConfigError, match="max_ratio"):
        validate_negotiation_settings({**NEG.data, "money_none": {"max_ratio": 0.9}})
    # 複数年の加点:長い年数・高い年齢ほど大きい
    assert multi_year_bonus(1, 35, NEG) == 0 and 0 < multi_year_bonus(3, 25, NEG) < multi_year_bonus(5, 25, NEG) < multi_year_bonus(5, 33, NEG)
    assert judge(NEG, pref, 5, 1000, 1000, 33, bad, "loose", 0.0)["score"] > r1["score"]


def test_depth_and_standing_ranks():
    league = generate_league(4, load_generation_config(), load_name_parts())
    team = league.teams[0]
    ranks = depth_ranks(team.players, lambda p: p.age)  # 評価の代わりに年齢で
    sp = sorted((p for p in team.players if p.position == "SP"), key=lambda p: -p.age)
    assert ranks[sp[0].id] == 0 and ranks[sp[-1].id] == sum(1 for p in sp if p.age > sp[-1].age)
    slots = depth_slots(load_generation_config())
    assert slots["SP"] == pytest.approx(14 * 14 / 32) and slots["C"] == pytest.approx(15 * 9 / 38)
    rec = {t.id: (60 - i, 60 + i) for i, t in enumerate(league.teams)}
    st = standing_ranks(league.teams, rec)
    assert sorted(st[t.id] for t in league.teams if t.league_index == 0) == [1, 2, 3, 4, 5, 6] and standing_ranks(league.teams, None) == {}


def test_ai_policy():
    assert ai_years(24, 3.5, NEG, 1) == 3 and ai_years(28, 2.2, NEG, 1) == 2 and ai_years(29, 5.0, NEG, 1) == 1 and ai_years(22, 1.0, NEG, 1) == 1
    assert ai_offer(0, 5000, 1, 0.5, NEG, "loose", 5, 100) == (1, 5000)
    assert ai_offer(1, 5000, 1, 0.5, NEG, "loose", 5, 100) is None  # 見込みが低い選手には再提示しない
    assert ai_offer(1, 5000, 1, 2.0, NEG, "loose", 5, 100) == (2, 5800) and ai_offer(2, 5000, 1, 2.0, NEG, "loose", 5, 100) == (3, 6500)
    assert ai_offer(1, 5000, 1, 2.0, NEG, "none", 5, 100) == (2, 5800)  # 「なし」も年俸を上げる(D-273)
    assert ai_offer(3, 5000, 1, 2.0, NEG, "loose", 5, 100) is None


# ---- ゲームの流れ ----

@pytest.fixture(scope="module")
def renewal_games():
    out = {}
    for rule in ("none", "loose", "standard"):
        g = api.Game.new(4, [None] * 12, 0, season_seed=9, baselines="default", money_rule=rule)
        g.advance(125)
        g.year_end()
        out[rule] = g
    return out


def _fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


@pytest.mark.slow
def test_new_game_contracts_are_single_year_by_default(renewal_games):
    g = api.Game.new(4, [None] * 12, 0, season_seed=9, baselines="default")
    players = g.state.league.all_players()
    years = [p.contract["until"] for p in players]
    assert years.count(1) > 0.9 * len(players) and max(years) <= 3  # 1 年が基本。見込みの高い若手だけ 2〜3 年(D-243、D-251)
    assert all(p.preference and set(p.preference) == set(NEG.axes) for p in players)


@pytest.mark.slow
def test_renewal_phase_auto_offer_and_gate(renewal_games):
    for rule, base in renewal_games.items():
        g = _fresh(base)
        proc = g.state.procedure
        v = g.offseason_view()
        assert v["phase"] == "renewal" and v["stage"] == "contract" and [x["label"] for x in v["phases"]] == ["契約", "FA", "ドラフト", "市場", "完了"] and v["renewal"]["unoffered"] == v["renewal"]["total"] > 30
        assert all(e["status"] != "pending" for e in proc.negotiations.values() if e["team_id"] != "T01")  # AI 球団は交渉を終えている
        with pytest.raises(ValueError, match="決まっていない"):
            g.offseason_next()
        v = g.offseason_renew_auto()
        c = v["renewal"]["counts"]
        assert c["pending"] == 0 and c["refused"] + c["accepted"] + c["declared"] == v["renewal"]["total"] and 0 < c["refused"] + c["declared"] < 25  # FA 権保持者は断ると宣言する(F3-2c)
        t = g.offseason_table("renewal", "batter", "war")
        assert [c2["label"] for c2 in t["columns"][:7]] == ["ポジション", "年齢", "打席", "現在の年俸", "自動案の年俸", "状態", "理由"]
        refused = [r for r in t["rows"] if r["renewal"]["status"] == "refused"]
        assert all(r["values"]["reason"] in ("年俸が低い", "出場機会が見込めない", "優勝を争えない") and r["values"]["status"].startswith("断られた") for r in refused)
        assert t["sort"]["key"] == "status" and all(t["rows"][i]["renewal"]["status"] == "refused" for i in range(len(refused) if t["rows"] and t["rows"][0]["role"] == "batter" else 0))
        # 断った選手は、自由契約にするまで「次の手続きへ」は押せない
        for e in [e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["status"] == "pending"]:
            g.offseason_renew_release(e["player_id"])
            assert e["player_id"] in {p.id for p in proc.market} and proc.released[-1]["note"] == "negotiation"
        _clear_overrun(g)
        v = g.offseason_next()
        assert v["stage"] in ("fa", "draft") and proc.ai_release_done and proc.my_release_done  # AI の超過の解消と自由契約は、契約の段階の終わりに(D-272)
        accepted = [e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["status"] == "accepted"]
        p = next(p for p in g._team("T01").players if p.id == accepted[0]["player_id"])
        h = p.contract["history"][-1]
        assert h["reason"] == "renew" and h["offers"] == 1 and h["years"] == 1 and p.contract["until"] == 2 and p.contract["salary"] == accepted[0]["auto_salary"]


def _clear_overrun(g):
    """標準以上で上限を超えていれば、年俸の高い選手から自由契約にして下回らせる(契約の画面の「自由契約にする」)。"""
    while g.offseason_view()["renewal"]["over"] > 0:
        rows = [r for r in g.contract_table("all", sort="contract", order="desc")["rows"] if r["can_release"] and r["in_team"]]
        g.offseason_contract_release(rows[0]["player_id"])


@pytest.mark.slow
def test_salary_editable_only_with_money_rules(renewal_games):
    g = _fresh(renewal_games["none"])
    e = next(e for e in g.state.procedure.negotiations.values() if e["team_id"] == "T01")
    auto = e["auto_salary"]
    with pytest.raises(ValueError, match="1.3 倍"):  # 「なし」は算定の 1.0〜1.3 倍(D-273)
        g.offseason_offer(e["player_id"], 1, int(auto * 1.3) + 100)
    with pytest.raises(ValueError, match="低い年俸"):
        g.offseason_offer(e["player_id"], 1, auto - 100)
    v = g.offseason_offer(e["player_id"], 2, int(auto * 1.2))  # 年数も年俸も選べる
    assert v["last_offer"]["salary"] == int(auto * 1.2) and v["last_offer"]["years"] == 2
    g = _fresh(renewal_games["loose"])
    e = next(e for e in g.state.procedure.negotiations.values() if e["team_id"] == "T01" and e["context"]["rank"] == 0)  # 出場機会のある選手
    v = g.offseason_offer(e["player_id"], 1, e["auto_salary"] * 2)
    assert v["last_offer"]["salary"] == e["auto_salary"] * 2 and v["last_offer"]["accepted"]  # 年俸を大きく上げれば受ける
    with pytest.raises(ValueError, match="最低年俸"):
        g.offseason_offer(next(x for x in g.state.procedure.negotiations.values() if x["team_id"] == "T01" and x["status"] == "pending")["player_id"], 1, 100)
    with pytest.raises(ValueError, match="年数"):
        g.offseason_offer(next(x for x in g.state.procedure.negotiations.values() if x["team_id"] == "T01" and x["status"] == "pending")["player_id"], 6)
    # 標準:算定より高い年俸は、見込みの総年俸が上限を超えない範囲だけ
    g = _fresh(renewal_games["standard"])
    proc = g.state.procedure
    e = next(e for e in proc.negotiations.values() if e["team_id"] == "T01")
    from pennant.draft import projected_total

    room = g.budget_info("T01")["cap"] - projected_total(g._team("T01"), proc)
    with pytest.raises(ValueError, match="上限"):
        g.offseason_offer(e["player_id"], 1, e["auto_salary"] + max(0, room) + 100)  # すでに上限を超えていれば、算定より高い年俸は出せない


@pytest.mark.slow
def test_three_refusals_release_and_answers_are_stable(renewal_games):
    g = _fresh(renewal_games["none"])
    g.offseason_renew_auto()
    proc = g.state.procedure
    e = next(e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["status"] == "pending")
    pid = e["player_id"]
    # 保存して読み込んでも、同じ提示には同じ答え(乱数は選手ごと・オフごとに 1 つ)
    again = api.Game(load_game(save_game(g.state)), dirty=False)
    assert again.state.procedure.negotiations[pid]["offers"] == e["offers"] and again.offseason_view()["phase"] == "renewal"
    a1 = g.offseason_offer(pid, 1)["last_offer"]
    a2 = again.offseason_offer(pid, 1)["last_offer"]
    assert a1 == a2 and not a1["accepted"] and a1["reason"]
    v = g.offseason_offer(pid, 1)
    assert v["last_offer"]["released"] and proc.negotiations[pid]["status"] == "released" and pid in {p.id for p in proc.market}
    with pytest.raises(ValueError):
        g.offseason_offer(pid, 1)
    assert any(r["player_id"] == pid for r in v["renewal"]["released"])


@pytest.mark.slow
def test_multi_year_contract_is_fixed_and_skipped_until_expiry(renewal_games):
    g = _fresh(renewal_games["loose"])
    proc = g.state.procedure
    e = next(e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["context"]["rank"] == 0 and e["age"] <= 30)
    pid = e["player_id"]
    v = g.offseason_offer(pid, 3, e["auto_salary"] * 2)
    assert v["last_offer"]["accepted"]
    p = next(p for p in g._team("T01").players if p.id == pid)
    salary = p.contract["salary"]
    assert p.contract["until"] == 4 and remaining_years(p.contract, 2) == 3
    g.offseason_auto()
    for year in (2, 3):  # 複数年のあいだは更改の対象外。年俸は固定
        g.advance(125)
        g.year_end()
        assert pid not in g.state.procedure.negotiations
        if any(q.id == pid for q in g._team("T01").players):
            assert next(q for q in g._team("T01").players if q.id == pid).contract["salary"] == salary
        g.offseason_auto()
    g.advance(125)
    g.year_end()
    if any(q.id == pid for q in g._team("T01").players):  # 満了の年のオフに更改の対象になる(引退していなければ)
        assert pid in g.state.procedure.negotiations


@pytest.mark.slow
def test_preference_is_hidden_unless_answer_mode(renewal_games):
    g = _fresh(renewal_games["loose"])
    g.offseason_renew_auto()
    e = next(e for e in g.state.procedure.negotiations.values() if e["team_id"] == "T01")
    texts = [g.offseason_view(), g.offseason_table("renewal", "pitcher", "basic"), g.offseason_table("renewal", "batter", "war"), g.player(e["player_id"]), g.team("T01")]
    text = json.dumps(texts, ensure_ascii=False)
    for key in HIDDEN_KEYS:
        assert f'"{key}"' not in text
    assert '"score"' not in text and '"values": {"salary"' not in text  # 判定の点数・満足度も出さない
    a = answers.player_answers(g, e["player_id"], 1)
    assert [x["key"] for x in a["preference"]] == list(NEG.axes) and abs(sum(x["weight"] for x in a["preference"]) - 1) < 0.01
    na = answers.negotiation_answers(g, 1)
    assert na["available"] and e["player_id"] in na["players"]


@pytest.mark.slow
def test_save_v11_and_v10_with_procedure_in_progress(renewal_games):
    g = _fresh(renewal_games["standard"])
    g.offseason_renew_auto()
    data = save_game(g.state)
    again = load_game(data)
    assert SAVE_FORMAT_VERSION == 13 and again.procedure.phase == "renewal"
    assert again.procedure.negotiations == g.state.procedure.negotiations and again.procedure.ranks == g.state.procedure.ranks
    assert [p.preference for p in again.league.all_players()] == [p.preference for p in g.state.league.all_players()]
    # 版 10 の進行中の手続き(更改は済んでいて、自由契約の段階):志望はシードから補い、自由契約の段階から続ける
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 10
    state = json.loads(files["state.json"])
    del state["configs"]["negotiation"]
    state["configs"]["contracts"]["years"] = {"min": 1, "max": 5, "young_until": 25, "young": 3, "prime_until": 29, "prime": [[2.0, 4], [1.0, 3], [0.0, 2]], "veteran_until": 32, "veteran": [[2.0, 2], [0.0, 1]], "old": 1, "rookie": 3}
    for team in state["league"]["teams"]:
        for pd in team["players"]:
            del pd["preference"]
    proc = state["procedure"]
    proc["phase"] = "release"
    proc["renewals"] = [{"team_id": e["team_id"], "player_id": e["player_id"], "name": e["name"], "role": e["role"], "position": e["position"], "age": e["age"], "old_salary": e["old_salary"], "salary": e["auto_salary"], "years": 1, "expected": e["expected"]} for e in proc["negotiations"].values() if e["status"] == "accepted"]
    del proc["negotiations"]
    del proc["ranks"]
    for key in ("candidates", "market"):
        for pd in proc[key]:
            del pd["preference"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    old = load_game(buf.getvalue())
    assert old.procedure.phase == "release" and old.contract_settings.default_years == 1 and old.contract_settings.rookie_years == 1
    assert all(e["status"] == "accepted" for e in old.procedure.negotiations.values()) and old.procedure.renewals
    assert [p.preference for p in old.league.all_players()] == [p.preference for p in g.state.league.all_players()]  # シードから同じ志望
    game = api.Game(old, dirty=False)
    game.offseason_auto()
    assert game.status()["year"] == 2


def test_web_bridge_renewal_actions():
    """画面の操作(F3-2b)は、bridge の手続きの入口から呼べる。"""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_test", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert {"renew_auto", "offer", "renew_release"} <= set(bridge._OFFSEASON) and "negotiation_answers" in bridge._ANSWERS


def test_threshold_without_money():
    """年俸の軸が効かない「なし」(設定 money_none.salary_axis が false)のしきい値。既定では「なし」も「ゆるい」と同じ(D-273)。"""
    pref = {"salary": 0.3, "playing_time": 0.4, "winning": 0.3}
    ctx = {"rank": 3, "slots": 2.0, "standing": 4, "league_size": 6}
    assert judge(NEG, pref, 1, 1000, 1000, 25, ctx, "none", 0.0) == judge(NEG, pref, 1, 1000, 1000, 25, ctx, "loose", 0.0)
    off = copy.deepcopy(NEG.data)
    off["money_none"] = {"salary_axis": False}
    neg = validate_negotiation_settings(off)
    none = judge(neg, pref, 1, 1000, 1000, 25, ctx, "none", 0.0)
    loose = judge(neg, pref, 1, 1000, 1000, 25, ctx, "loose", 0.0)
    base = sum(pref[a] * NEG.axes[a]["strength"] * none["values"][a] for a in none["values"])
    assert none["score"] == pytest.approx(base - NEG.threshold_without_money) and NEG.threshold_without_money > NEG.threshold
    assert loose["score"] == pytest.approx(base + pref["salary"] * NEG.axes["salary"]["offset"] - NEG.threshold)


@pytest.mark.slow
def test_overrun_can_break_minimums_as_last_resort():
    """標準以上で上限を大きく超えたとき、最低人数を守ったままでは解消できなければ、最低人数を割っても外す(不足は自動補充)。
    あなたの球団も、上限を超えている間は最低人数の選手を外せる(F3-2b)。"""
    from pennant.contracts import team_salary
    from pennant.draft import minimum_batters, minimum_positions, resolve_overrun

    g = api.Game.new(4, [None] * 12, 0, season_seed=9, baselines="default", money_rule="standard")
    g.advance(125)
    g.year_end()
    proc = g.state.procedure
    g.offseason_renew_auto()
    for e in [e for e in proc.negotiations.values() if e["team_id"] == "T01" and e["status"] == "pending"]:
        g.offseason_renew_release(e["player_id"])
    team = g._team("T01")
    catchers = [p for p in team.players if p.position == "C"]
    for p in catchers:  # 捕手の年俸を大きくして、上限を超えさせる
        p.contract["salary"] = 200000
    assert g.offseason_view()["contracts"]["mine"]["blocked"]
    t = g.contract_table("all")
    assert all(r["can_release"] for r in t["rows"] if r["in_team"])  # 上限を超えている間は全員外せる
    g.offseason_release([p.id for p in catchers])  # 捕手を全員外す(最低人数を割る)
    assert not [p for p in team.players if p.position == "C"] and not g.offseason_view()["contracts"]["mine"]["blocked"]
    g.offseason_auto()
    assert len([p for p in g._team("T01").players if p.position == "C"]) >= minimum_positions()["C"]  # 完了のときに自動補充
    # AI の方針も同じ:最低人数の選手しか高くなければ、それでも外す
    g2 = api.Game.new(4, [None] * 12, None, season_seed=9, baselines="default", money_rule="standard")
    t2 = g2.state.league.teams[1]
    for p in [p for p in t2.players if p.position == "C"]:
        p.contract["salary"] = 200000
    from pennant.draft import OffseasonProcedure, state_context

    proc2 = OffseasonProcedure(1, 1, [t.id for t in g2.state.league.teams], 6, 3, [70.0, 60.0, 50.0, 40.0])
    ctx = state_context(g2.state, proc2)
    out = resolve_overrun(t2, proc2, ctx, minimum_positions(), minimum_batters())
    assert out and team_salary(t2) <= ctx.cap(t2.id) and any(p.position == "C" for p in out)
