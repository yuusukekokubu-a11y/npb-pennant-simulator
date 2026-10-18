"""育成選手(D-375)と外国人選手(D-376):名前・新規開始の人数・一軍の外国人の上限・外国人市場・育成ドラフト・支配下・3 年で自由契約・保存(D-377)。"""

import copy
import json

import pytest

from pennant import api
from pennant.config import load_name_parts
from pennant.draft import load_draft_settings
from pennant.game_config import load_game_config
from pennant.generate import generate_league
from pennant.manager import SimpleManager
from pennant.models import Team
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

DS = load_draft_settings()


@pytest.fixture(scope="module")
def base():
    return api.Game.new(4, [None] * 12, 0, season_seed=6, baselines="default")


@pytest.fixture(scope="module")
def at_offseason(base):
    g = api.Game(copy.deepcopy(base.state), dirty=False)
    g.advance_to_end()
    g.year_end()
    return g


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def go_to(g, stage):
    while g.offseason_view()["stage"] != stage:
        if g.offseason_view()["stage"] == "contract":  # 資金を超えているときもあるので、契約は AI の方針で
            g.offseason_stage_auto()
        else:
            g.offseason_next()
    return g.offseason_view()


def test_foreign_names_and_initial_counts(base):
    parts = load_name_parts()
    assert len(parts.foreign_surnames) >= 150 and parts.foreign_given_names
    league = generate_league(3)
    for t in league.teams:
        fs = [p for p in t.players if p.foreign]
        assert DS.foreign["initial_min"] <= len(fs) <= DS.foreign["initial_max"]
        assert all(p.name == p.family_name and p.family_name in parts.foreign_surnames for p in fs)  # 画面は姓だけ
        assert len({p.family_name for p in fs}) == len(fs)
    # 新規開始(事前運転の後)でも各球団に外国人がいる
    counts = [sum(p.foreign for p in t.players) for t in base.state.league.teams]
    # トレード(D-378)で外国人が偏り、0 人の球団が出ることもある(バランス調整で見る。D-364)
    assert sum(c >= 2 for c in counts) >= len(counts) - 2 and 4 <= sum(counts) / len(counts) <= 7
    assert all(60 <= len(t.players) <= 70 for t in base.state.league.teams)  # 70 人は上限だけ(D-391)
    assert any(t.development for t in base.state.league.teams)


def test_active_roster_has_at_most_four_foreign(base):
    team = copy.deepcopy(base.state.league.teams[0])
    for p in team.players[:10]:  # 外国人を 10 人にする
        p.foreign = True
    cfg = load_game_config()
    active = SimpleManager(cfg).select_active(team)
    assert sum(p.foreign for p in active.players) <= cfg["active_roster"]["foreign_max"] == 4
    # 上限がなければ選び方は変わらない(外国人が 4 人以下の球団)
    t2 = copy.deepcopy(base.state.league.teams[1])
    keep = [p for p in t2.players if p.foreign][:4]
    for p in t2.players:
        p.foreign = p in keep
    a = SimpleManager(cfg).select_active(t2)
    for p in t2.players:
        p.foreign = False
    b = SimpleManager(cfg).select_active(t2)
    assert [p.id for p in a.players] == [p.id for p in b.players]


def test_foreign_market_shows_estimates_and_signs_in_order(at_offseason):
    g = fresh(at_offseason)
    v = go_to(g, "foreign")
    assert v["phase"] == "foreign" and v["foreign"]["counts"]["pool"] == DS.foreign["candidates"]
    t = g.decision_table("foreign")
    assert [c["key"] for c in t["columns"]] == ["ability", "age", "pos", "depth", "salary", "status"]
    text = json.dumps(t, ensure_ascii=False)
    for key in ("ratings", "growth_type", "ease", "grade", "comment"):
        assert f'"{key}"' not in text  # 隠し情報と、内部の評価・一言は見せない
    proc = g.state.procedure
    real = {p.id: p for p in proc.foreign_pool}
    est = {r["player_id"]: {a["key"]: a["value"] for a in r["scouting"]["abilities"]} for r in t["rows"]}
    shown = {pid: {a["key"]: a["value"] for a in g.player_abilities(real[pid])} for pid in est}
    assert any(est[pid] != shown[pid] for pid in est)  # 見立ては本当の数字とずれる(D-418)
    tp = g.decision_table("foreign", group="pitcher")
    assert [c["key"] for c in tp["columns"]][:6] == ["ability", "velocity", "control", "stamina", "breaking", "skills"]
    proc = g.state.procedure
    assert all(proc.foreign_salary[p.id] in DS.foreign["salary"].values() for p in proc.foreign_pool)
    pid = min(t["rows"], key=lambda r: (r["deal"]["calc_salary"], r["player_id"]))["player_id"]  # 資金の残りで結べる、年俸の最も安い候補
    g.offseason_foreign_offer(pid)
    with pytest.raises(ValueError, match="候補ではありません"):
        g.offseason_foreign_offer("nobody")
    view = g.offseason_foreign_close()
    signed = view["last_foreign"]["signed"]
    assert signed and all(x["years"] == 1 for x in signed)
    for x in signed:
        team = g._team(x["team_id"])
        p = next(p for p in team.players if p.id == x["player_id"])
        assert p.foreign and p.contract["salary"] == x["salary"] and p.contract["history"][-1]["reason"] == "foreign"
    assert g.state.procedure.foreign_pool == [] and view["phase_finished"]
    v = g.offseason_next()
    assert v["stage"] == "draft"


def test_development_draft_promotion_and_auto(at_offseason):
    g = fresh(at_offseason)
    go_to(g, "draft")
    v = g.offseason_next()  # ドラフトの残りをパスして、育成ドラフトへ
    assert v["phase"] == "dev_draft" and v["stage"] == "draft" and v["development_draft"]
    v = g.offseason_advance()
    my = g._team("T01")
    before = len(my.development)
    if v["is_my_turn"]:
        pid = g.decision_table("draft")["rows"][0]["player_id"]
        g.offseason_pick(pid)
        p = next(p for p in g._team("T01").development if p.id == pid)
        assert p.dev_seasons == 0 and p.contract["salary"] == DS.development["salary"] and p.contract["history"][-1]["reason"] == "development"
        assert len(g._team("T01").development) == before + 1
    v = g.offseason_next()
    assert v["stage"] == "market" and v["development"] is not None
    my = g._team("T01")
    assert all(len(t.development) <= DS.development["max"] for t in g.state.league.teams)
    # 支配下に上げる(空きがあれば)
    if my.development and len(my.players) < 70:
        pid = my.development[0].id
        g.offseason_promote(pid)
        p = next(p for p in g._team("T01").players if p.id == pid)
        assert p.dev_seasons is None and p.contract["history"][-1]["reason"] == "promote"
    with pytest.raises(ValueError, match="育成選手ではありません"):
        g.offseason_promote("nobody")
    g.offseason_auto()
    assert g.state.year == 2 and all(60 <= len(t.players) <= 70 for t in g.state.league.teams)
    tv = g.team("T01")
    assert "development" in tv and tv["foreign_count"] == sum(p.foreign for p in g._team("T01").players)
    if g._team("T01").development:
        dp = g._team("T01").development[0]
        pv = g.player(dp.id)["player"]
        assert pv["development"]["label"].startswith("育成")


def test_development_players_leave_after_three_seasons(base):
    from pennant.offseason import age_update_retire

    g = fresh(base)
    league = g.state.league
    team = league.teams[0]
    p = team.development[0] if team.development else None
    if p is None:
        pytest.skip("育成選手がいない")
    p.dev_seasons = DS.dev_seasons - 1
    result, _ = age_update_retire(league, 1, g.state.gen_config, g.state.offseason_settings, 1, g.state.calibration, DS.dev_seasons)
    assert p.id in result.dev_released and p not in team.development and p.id in {n.player_id for n in result.retired}


def test_save_round_trip_and_old_version(at_offseason):
    g = fresh(at_offseason)
    go_to(g, "foreign")
    data = save_game(g.state)
    s = load_game(data)
    assert SAVE_FORMAT_VERSION == 28
    assert [[p.id for p in t.development] for t in s.league.teams] == [[p.id for p in t.development] for t in g.state.league.teams]
    assert [p.foreign for p in s.league.all_players()] == [p.foreign for p in g.state.league.all_players()]
    assert [p.id for p in s.procedure.foreign_pool] == [p.id for p in g.state.procedure.foreign_pool] and s.procedure.foreign_salary == g.state.procedure.foreign_salary
    # 版 18 の手続き(外国人市場・育成ドラフトの項目なし)は、それらを済ませた形で読む
    from pennant.procedure import OffseasonProcedure

    d = g.state.procedure.to_dict(lambda p: p.to_dict())
    for k in ("dev_rounds", "foreign_pool", "foreign_cuts", "foreign_stats", "foreign_salary", "foreign_offers", "foreign_results", "foreign_done", "promotions"):
        d.pop(k)
    old = OffseasonProcedure.from_dict(d, lambda x: x)
    assert old.foreign_done and old.dev_rounds == 0


def test_team_has_development_field():
    assert Team("T", 0, "a", "b", "c").development == []


def test_bridge_has_foreign_and_promote_operations():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("bridge_for_foreign", Path(__file__).resolve().parents[1] / "web" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    src = (Path(__file__).resolve().parents[1] / "web" / "bridge.py").read_text(encoding="utf-8")
    for op in ("foreign_offer", "foreign_cancel", "foreign_close", "promote"):
        assert f'"{op}"' in src
