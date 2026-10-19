"""球団職員(D-468〜D-480):初期の職員・効き(コーチ・スカウト・監督)・職員の段階(市場・希望・解任・締める)・
引退した選手が候補になる・入れ替えと「あなたが決めた」・保存と読み込み・版 32 の読み込み・記録室の監督。"""

import copy
import io
import json
import random
import zipfile

import pytest

from pennant import api, offseason, staff as staffmod
from pennant.config import load_generation_config
from pennant.procedure import OffseasonProcedure
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

S = staffmod.default_settings()


@pytest.fixture(scope="module")
def base():
    """自球団 T01 の新規開始(試合なし)。"""
    return api.Game.new(11, [None] * 12, 0, season_seed=11, baselines="default")


def _fresh(g) -> api.Game:
    return api.Game(load_game(save_game(g.state)), dirty=False)


def _proc(g, ranks=None) -> OffseasonProcedure:
    order = [t.id for t in g.state.league.teams]
    return OffseasonProcedure(g.state.year, 4242, order, 10, 3, [], ranks=ranks or {tid: 3 for tid in order})


# ---- 初期の職員(D-469、D-478) ----

def test_every_team_has_five_staff_and_the_same_seed_gives_the_same_staff(base):
    league = base.state.league
    for t in league.teams:
        assert list(t.staff) == list(staffmod.ROLES)
        for role, m in t.staff.items():
            assert m["role"] == role and 30 <= m["skill"] <= 99 and 1 <= m["years"] <= 3 and m["until"] == m["years"]
            assert m["salary"] == S.salary(role, m["skill"]) and (m["trait"] == "none") == (role != "manager" or m["trait"] == "none")
    assert len(league.staff_free) == 15 and all(m["until"] is None for m in league.staff_free)
    ids = [m["id"] for m in staffmod.all_staff(league)]
    assert len(ids) == len(set(ids))
    again = api.Game.new(11, [None] * 12, 0, season_seed=99, baselines="default").state.league
    assert [t.staff for t in again.teams] == [t.staff for t in league.teams]
    skills = [m["skill"] for t in league.teams for m in t.staff.values()]
    assert 55 <= sum(skills) / len(skills) <= 65


def test_staff_salary_comes_out_of_the_funds(base):
    for t in base.state.league.teams:
        b = base.budget_info(t.id)
        cost = staffmod.team_cost(t, 1)
        assert cost == sum(m["salary"] for m in t.staff.values()) > 0
        assert b["staff"] == cost and b["cap"] == base.state.team_funds[t.id] - b["upkeep"] - cost
        assert b["total"] <= b["cap"]  # 初期の年俸は職員の年俸を引いた額に収まる


def test_ranks_and_levels():
    assert [S.rank(v) for v in (99, 90, 89, 80, 70, 60, 50, 49, 30)] == ["S", "S", "A", "A", "B", "C", "D", "E", "E"]
    assert [staffmod.level_of(v) for v in (99, 90, 82, 68, 60, 52, 45, 37)] == [2, 2, 1, 1, 0, -1, -1, -2]
    assert S.salary("manager", 95) == 2 * S.salary("batting", 95) == 12000


# ---- 効き(D-470〜D-472) ----

def test_coach_raises_growth_and_softens_decline_relative_to_the_league():
    cfg = load_generation_config()
    league = api.Game.new(3, [None] * 12, None, baselines="default").state.league
    t = league.teams[0]
    t.staff["batting"]["skill"] = 90
    means = staffmod.coach_means(league)
    f = staffmod.coach_factors(t, means)
    gain = 0.005 * (90 - means["batting"])
    assert f["meet"][0] == pytest.approx(1 + gain) and f["power"][1] == pytest.approx(1 - gain / 2) and "control" in f
    young = next(p for p in t.players if p.role == "batter" and p.age <= 22)
    a, b = copy.deepcopy(young), copy.deepcopy(young)
    ca = offseason.age_and_update(a, cfg, random.Random(1), coach={k: f[k] for k in ("meet", "power")})  # 打撃コーチの分だけ
    cb = offseason.age_and_update(b, cfg, random.Random(1))
    assert ca["meet"] > cb["meet"] and ca["speed"] == pytest.approx(cb["speed"])
    for x in league.teams:  # 全球団が同じ腕なら、だれも標準(リーグの水準を動かさない)
        x.staff["batting"]["skill"] = 75
    assert staffmod.coach_factors(t, staffmod.coach_means(league))["meet"] == (1.0, 1.0)
    assert staffmod.coach_factors(type(t)("X", 0, "a", "b", "c")) == {}  # 職員がいなければ標準


def test_scout_skill_changes_the_estimate_spread(base):
    g = _fresh(base)
    t = g._team("T02")
    base_sd = g.state.draft_settings.level_sd(g.state.scout_level)
    t.staff["scout"]["skill"] = 90
    good = g.state.scout_sd_of("T02")
    t.staff["scout"]["skill"] = 40
    bad = g.state.scout_sd_of("T02")
    assert good["common"] == pytest.approx(base_sd["common"] * 0.79) and bad["item"] == pytest.approx(base_sd["item"] * 1.14)


def test_manager_preference_enters_the_ai_policy(base):
    from pennant import policy

    g = _fresh(base)
    t = g._team("T04")
    t.staff["manager"]["trait"] = "bunt"
    policy.refresh_ai(g.state.league)
    assert policy.policy_of(t)["bunt"] == "many"
    g.set_team_policy("T04", {"bunt": "few"})  # あなたが決めた方針には効かない
    policy.refresh_ai(g.state.league)
    assert policy.policy_of(t)["bunt"] == "few"


def test_good_manager_pulls_tired_starters_earlier(base):
    def starter_batters(skill):
        g = _fresh(base)
        for t in g.state.league.teams:
            t.staff["manager"]["skill"] = skill
        g.advance(12)
        lines = [ln for p in g.state.season.played for ln in p.result.pitchers if ln.role == "starter"]
        plans = [p.context.lineups.get(p.scheduled.home_id, {}).get("manager") for p in g.state.season.played]
        return sum(ln.batters_faced for ln in lines) / len(lines), plans

    good, plans_good = starter_batters(99)
    bad, plans_bad = starter_batters(30)
    assert set(plans_good) == {2} and set(plans_bad) == {-2}  # 監督の腕の段階は試合の前の状態に記録する(再現のため)
    assert good < bad


# ---- 職員の段階(D-474、D-475) ----

def test_stage_expires_contracts_fires_bad_managers_and_adds_candidates(base):
    g = _fresh(base)
    league = g.state.league
    proc = _proc(g, {t.id: (6 if t.id == "T03" else 2) for t in league.teams})
    settings = staffmod.validate_staff_settings({**copy.deepcopy(S.data), "ai": {"wishes": 3, "fire": {"6": 1.0}}})
    expiring = {(t.id, r) for t in league.teams for r, m in t.staff.items() if m["until"] <= proc.year}
    retired = [{"player_id": "P_OLD", "family_name": "元野", "given_name": "太郎", "role": "batter", "age": 37, "team_id": "T05", "foreign": False}]
    staffmod.start_stage(league, proc, g.state.name_parts, retired, {"P_OLD": 40.0}, "T01", settings)
    assert proc.staff_on and expiring
    for tid, role in expiring:
        assert role not in g._team(tid).staff
    assert "manager" not in g._team("T03").staff and any(e["kind"] == "fired" and e["team_id"] == "T03" for e in proc.staff_events)
    former = next(m for m in league.staff_free if m.get("player_id") == "P_OLD")
    assert former["origin"] == "former" and former["role"] in ("batting", "fielding", "manager", "scout") and former["age"] == 37
    assert len(league.staff_free) <= S.data["generate"]["pool_max"]
    if g._team("T03").staff_dead:  # 解任した監督の残りの年俸は払う
        assert staffmod.team_cost(g._team("T03"), proc.year + 1) >= sum(d["salary"] for d in g._team("T03").staff_dead)


def test_close_fills_every_vacancy_in_order_and_respects_the_budget(base):
    g = _fresh(base)
    league = g.state.league
    order = sorted(t.id for t in league.teams)[::-1]
    proc = _proc(g)
    proc.order = order
    staffmod.start_stage(league, proc, g.state.name_parts, [], {}, "T01")
    mine = g._team("T01")
    for role in list(mine.staff):
        staffmod.dismiss(league, mine, role, proc.year + 1)
    target = next(m for m in sorted(league.staff_free, key=lambda m: m["salary"]) if m["role"] == "pitching")
    proc.staff_offers = [target["id"]]
    results = staffmod.close_stage(league, proc, g.state.team_funds, None, "T01", False, g.state.name_parts)
    assert all(list(t.staff) == list(staffmod.ROLES) or set(t.staff) == set(staffmod.ROLES) for t in league.teams)
    assert mine.staff["pitching"]["id"] == target["id"]
    firsts = [r for r in results if r["round"] == 1]
    assert [r["team_id"] for r in firsts] == [tid for tid in order if any(r["team_id"] == tid for r in firsts)]  # 前年の順位が低い球団から
    for r in results:
        if r["by"] == "wish":
            t = g._team(r["team_id"])
            assert t.staff[r["role"]]["until"] == proc.year + r["years"]
    assert proc.staff_done and staffmod.team_cost(mine, proc.year + 1) >= 5 * min(S.data["salary"]["ranks"].values())


def test_screen_flow_offer_cancel_dismiss_close_and_swap(base):
    g = _fresh(base)
    league = g.state.league
    proc = _proc(g)
    g.state.procedure = proc
    staffmod.start_stage(league, proc, g.state.name_parts, [], {}, "T01")
    proc.phase = "staff"
    v = g.offseason_view()
    assert v["stage"] == "staff" and [p["key"] for p in v["phases"]][:3] == ["contract", "staff", "fa"]
    g.offseason_staff_dismiss("scout")
    v = g.offseason_view()["staff"]
    assert "scout" in [x["key"] for x in v["vacancies"]] and all("skill" not in x for x in v["pool"])  # 腕の数は出さない
    cand = next((x for x in v["pool"] if x["open"]), None)
    if cand is not None:
        g.offseason_staff_offer(cand["staff_id"])
        assert g.offseason_view()["staff"]["offers"][0]["staff_id"] == cand["staff_id"]
        g.offseason_staff_cancel(cand["staff_id"])
    with pytest.raises(ValueError, match="空いていません"):
        g.offseason_staff_offer(next(m["id"] for m in league.staff_free if m["role"] in g._team("T01").staff))
    g.offseason_staff_close()
    assert "scout" in g._team("T01").staff and g.state.procedure.phase == "staff"
    g.offseason_next()
    assert g.state.procedure.phase != "staff"


def test_swap_other_team_marks_manual_and_ai_back_clears_it(base):
    g = _fresh(base)
    league = g.state.league
    old = g._team("T06").staff["manager"]
    cand = next(m for m in league.staff_free if m["role"] == "manager")
    v = g.staff_swap("T06", "manager", cand["id"])
    assert v["manual"] and v["staff"][0]["staff_id"] == cand["id"] and g.dirty
    assert any(m["id"] == old["id"] for m in league.staff_free)
    assert g._team("T06").staff_dead and staffmod.team_cost(g._team("T06"), 1) == sum(m["salary"] for m in g._team("T06").staff.values()) + old["salary"]
    proc = _proc(g, {t.id: 6 for t in league.teams})
    settings = staffmod.validate_staff_settings({**copy.deepcopy(S.data), "ai": {"wishes": 3, "fire": {"6": 1.0}}})
    staffmod.start_stage(league, proc, g.state.name_parts, [], {}, "T01", settings)
    if g._team("T06").staff["manager"]["until"] >= proc.year + 1:
        assert g._team("T06").staff["manager"]["id"] == cand["id"]  # あなたが決めた球団の監督は AI が代えない
    back = g.staff_reset("T06")
    assert not back["manual"] and g.team("T06")["staff"]["source_text"].startswith("AI")
    with pytest.raises(ValueError):
        g.staff_swap("T06", "batting", cand["id"])  # 役割が違う


def test_save_and_load_keep_the_staff(base):
    g = _fresh(base)
    cand = next(m for m in g.state.league.staff_free if m["role"] == "scout")
    g.staff_swap("T08", "scout", cand["id"])
    again = _fresh(g)
    assert [t.staff for t in again.state.league.teams] == [t.staff for t in g.state.league.teams]
    assert again._team("T08").staff_manual and again.state.league.staff_free == g.state.league.staff_free
    assert SAVE_FORMAT_VERSION == 33


def _as_v32(data: bytes) -> bytes:
    """版 33 のセーブデータから職員を消して、版 32 の形にする。"""
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    state = json.loads(files["state.json"])
    for t in state["league"]["teams"]:
        for k in ("staff", "staff_manual", "staff_dead"):
            t.pop(k, None)
    state["league"].pop("staff_free", None)
    state["league"].pop("staff_log", None)
    for gm in state["season"]["games"]:
        for plan in (gm.get("lineups") or {}).values():
            plan.pop("manager", None)
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 32
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode()
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_version_32_gets_initial_staff_from_the_seed(base):
    old = _as_v32(save_game(base.state))
    a, b = load_game(old), load_game(old)
    assert [t.staff for t in a.league.teams] == [t.staff for t in b.league.teams]
    assert all(set(t.staff) == set(staffmod.ROLES) for t in a.league.teams) and a.league.staff_log == []
    assert [t.staff for t in a.league.teams] == [t.staff for t in base.state.league.teams]  # 新規開始と同じ決め方(リーグのシード)


def test_bad_staff_data_is_reported(base):
    from pennant.savegame import SaveDataError

    data = save_game(base.state)
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    state = json.loads(files["state.json"])
    state["league"]["teams"][0]["staff"]["manager"]["skill"] = 500
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    with pytest.raises(SaveDataError, match="skill"):
        load_game(buf.getvalue())


# ---- 1 シーズンを終えてから(重い) ----

@pytest.fixture(scope="module")
def after_season():
    g = api.Game.new(21, [None] * 12, 0, season_seed=21, baselines="default")
    g.advance(400)
    g.year_end()
    return g


@pytest.mark.slow
def test_year_end_opens_the_staff_stage_and_records_managers(after_season):
    g = _fresh(after_season)
    proc = g.state.procedure
    assert proc.staff_on and g.state.league.staff_log and {x["role"] for x in g.state.league.staff_log} == set(staffmod.ROLES)
    rec = g.record_managers()
    assert len(rec["managers"]) == 12 and sum(r["pennants"] for r in rec["managers"]) == 2
    assert sum(r["wins"] for r in rec["managers"]) == sum(r["losses"] for r in rec["managers"])
    g.offseason_stage_auto()
    assert g.state.procedure.phase == "staff"
    saved = save_game(g.state)
    a, b = api.Game(load_game(saved), dirty=False), api.Game(load_game(saved), dirty=False)
    a.offseason_stage_auto()
    b.offseason_staff_close()
    b.offseason_next()
    # 「この段階をおまかせ」と「締める → 次へ」は、自球団の希望が違うだけ。どちらも全球団が埋まる
    for x in (a, b):
        assert all(set(t.staff) == set(staffmod.ROLES) for t in x.state.league.teams) and x.state.procedure.phase in ("fa", "foreign", "draft")
    c = api.Game(load_game(saved), dirty=False)
    c.offseason_stage_auto()
    assert [t.staff for t in c.state.league.teams] == [t.staff for t in a.state.league.teams]  # 保存して読み込んでも同じ結果
    a.offseason_auto()
    assert a.state.procedure is None and a.state.year == 2


@pytest.mark.slow
def test_observer_offseason_runs_the_staff_stage():
    g = api.Game.new(31, [None] * 12, None, season_seed=31, baselines="default")
    g.advance(400)
    g.year_end()
    assert g.state.procedure is None and all(set(t.staff) == set(staffmod.ROLES) for t in g.state.league.teams)
    for t in g.state.league.teams:
        for m in t.staff.values():
            assert m["until"] >= 2
