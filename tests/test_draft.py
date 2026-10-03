"""F3-1(ドラフトと自由契約、スカウト評価。D-198〜D-211)の確認。"""

import copy
import io
import json
import statistics
import zipfile

import pytest

from pennant import answers, api
from pennant.abilities import BATTER, PITCHER, strength_items_for
from pennant.config import ConfigError, load_generation_config, load_name_parts
from pennant.draft import (
    MAX_ROSTER,
    PHASES,
    ai_release,
    can_release,
    candidate_slots,
    draft_order,
    load_draft_settings,
    minimum_batters,
    minimum_positions,
    run_ai_offseason,
    scout_report,
    shortages,
    validate_draft_settings,
)
from pennant.generate import generate_league
from pennant.newgame import new_league
from pennant.offseason import age_update_retire, load_offseason_settings
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game
from pennant.scouting import GRADES, ceiling_cuts, grade_of, quick_value, report
from pennant.stats import overall, potential_overall

CONFIG = load_generation_config()
PARTS = load_name_parts()
OFF = load_offseason_settings()
DS = load_draft_settings()
MINS = minimum_positions()
MIN_BATTERS = minimum_batters()


# ---- 設定 ----

def test_settings_and_validation():
    assert DS.rounds == 6 and DS.market_rounds == 3 and DS.default_level == "medium"
    assert DS.level_sd("small") < DS.level_sd("medium") < DS.level_sd("large")
    assert abs(sum(DS.ceiling_shares.values()) - 1) < 1e-9
    data = copy.deepcopy(DS.data)
    data["scouting"]["ceiling_shares"]["S"] = 0.5
    with pytest.raises(ConfigError, match="ceiling_shares"):
        validate_draft_settings(data)
    data = copy.deepcopy(DS.data)
    data["draft"]["rounds"] = 0
    with pytest.raises(ConfigError, match="draft.rounds"):
        validate_draft_settings(data)
    assert set(OFF.levels) == {"small", "medium", "large"}
    assert MINS["SP"] == 6 and MINS["RP"] == 8 and MINS["C"] == 2 and MINS["SS"] == 1 and MIN_BATTERS == 15


# ---- スカウト評価(D-199):同じ候補は同じ値、球団で違う、ずれの大きさ、ふれ幅 ----

@pytest.fixture(scope="module")
def pool():
    league = generate_league(5, CONFIG, PARTS)
    players = league.all_players()[:300]
    cuts = ceiling_cuts(players, DS.ceiling_shares)
    return players, cuts


def test_report_is_deterministic_and_team_specific(pool):
    players, cuts = pool
    p = players[0]
    a = report(p, "T01", 99, 5.0, cuts, 1.28)
    b = report(p, "T01", 99, 5.0, cuts, 1.28)
    c = report(p, "T02", 99, 5.0, cuts, 1.28)
    assert a.to_dict() == b.to_dict() and a.to_dict() != c.to_dict()
    q = quick_value(p, "T01", 99, 5.0, cuts)
    assert abs(q[0] - a.overall) < 1e-9 and q[1] == a.ceiling  # AI が使う速い値と、画面の評価が一致する
    assert abs(sum(a.items.values()) / len(a.items) - a.overall) < 1e-9
    assert a.margin == pytest.approx(5.0 / len(a.items) ** 0.5 * 1.28) and a.item_margin == pytest.approx(6.4)
    pub = a.to_public()
    assert 20 <= pub["overall"] <= 80 and all(20 <= i["estimate"] <= 80 for i in pub["items"]) and pub["ceiling"] in GRADES
    assert "potential" not in json.dumps(pub) and "growth" not in json.dumps(pub)
    assert report(p, "T01", 100, 5.0, cuts, 1.28).to_dict() != a.to_dict()  # シードが違えば違う


@pytest.mark.parametrize("level", ["small", "medium", "large"])
def test_error_sd_and_coverage_match_settings(pool, level):
    players, cuts = pool
    sd = DS.level_sd(level)
    errs = []
    covered = 0
    for p in players:
        r = report(p, "T03", 7, sd, cuts, DS.margin_z)
        for item in strength_items_for(p.role):
            e = r.items[item] - p.ratings[item]
            errs.append(e)
            covered += abs(e) <= r.item_margin
    assert abs(statistics.pstdev(errs) - sd) < 0.15 * sd  # 設定どおりのずれ
    assert 0.75 <= covered / len(errs) <= 0.85  # ふれ幅に真の値が入る割合


def test_ceiling_grades_follow_shares_and_potential(pool):
    players, cuts = pool
    assert cuts == sorted(cuts, reverse=True)
    grades = [grade_of(potential_overall(p), cuts) for p in players]
    assert abs(grades.count("S") / len(players) - 0.05) < 0.03 and abs(grades.count("D") / len(players) - 0.20) < 0.05
    by = {g: [potential_overall(p) for p, gr in zip(players, grades) if gr == g] for g in GRADES}
    means = [statistics.fmean(by[g]) for g in GRADES if by[g]]
    assert means == sorted(means, reverse=True)


# ---- ドラフトの順番・人数の規則(D-202、D-203) ----

def test_draft_order_is_worst_first_and_snake():
    league = generate_league(1, CONFIG, PARTS)
    records = {t.id: (i * 5 + 40, 85 - i * 5) for i, t in enumerate(league.teams)}  # T01 が最も弱い
    order = draft_order(league, records)
    assert order[0] == "T01" and order[-1] == "T12"
    assert draft_order(league, None) == sorted(t.id for t in league.teams)
    slots = candidate_slots(CONFIG, 108)
    assert len(slots) == 108 and sum(1 for r, _ in slots if r == PITCHER) == round(108 * 32 / 70)


def test_ai_offseason_keeps_rules_and_is_reproducible():
    results = []
    for _ in range(2):
        league = generate_league(2, CONFIG, PARTS)
        age_update_retire(league, 55, CONFIG, OFF, 1, None)
        sd = {t.id: 5.0 for t in league.teams}
        proc, joined = run_ai_offseason(league, 55, CONFIG, PARTS, OFF, DS, 1, None, sd, [t.id for t in league.teams])
        results.append(([(x["phase"], x["round"], x["team_id"], x["player_id"]) for x in proc.picks], [p.id for p in league.all_players()]))
        assert proc.phase == "done" and not proc.candidates and not proc.market
        assert all(len(t.players) == MAX_ROSTER for t in league.teams)
        assert all(not shortages(t.players, MINS, MIN_BATTERS) for t in league.teams)
        # 往復:1 巡目と 2 巡目の順番が逆
        r1 = [x["team_id"] for x in proc.picks if x["phase"] == "draft" and x["round"] == 1]
        r2 = [x["team_id"] for x in proc.picks if x["phase"] == "draft" and x["round"] == 2]
        assert r1 == proc.order and r2 == list(reversed(proc.order))
        assert max(x["round"] for x in proc.picks if x["phase"] == "draft") <= DS.rounds
        # 空き枠がない球団はパス(note=full)、指名は空き枠の分まで
        for t in league.teams:
            drafted = [x for x in proc.picks if x["team_id"] == t.id and x["player_id"]]
            assert len(drafted) <= MAX_ROSTER
        assert all(p.scouting is not None for n in joined for p in [next(p for t in league.teams for p in t.players if p.id == n.player_id)])
        assert all(p.scouting["team_id"] == p.team_id for t in league.teams for p in t.players if p.scouting)
    assert results[0] == results[1]  # 同じシードなら同じ結果


def test_ai_release_respects_minimums_and_limit():
    league = generate_league(3, CONFIG, PARTS)
    age_update_retire(league, 9, CONFIG, OFF, 1, None)
    proc, _ = run_ai_offseason(copy.deepcopy(league), 9, CONFIG, PARTS, OFF, DS, 1, None, {t.id: 5.0 for t in league.teams}, [t.id for t in league.teams])
    per_team = {}
    for x in proc.released:
        per_team[x["team_id"]] = per_team.get(x["team_id"], 0) + 1
    assert all(n <= DS.ai("release_max") for n in per_team.values())
    assert all(x["age"] >= DS.ai("release_min_age") for x in proc.released)
    team = league.teams[0]
    chosen = ai_release(team, proc, 5.0, DS, MINS, MIN_BATTERS)
    rest = [p for p in team.players if p not in chosen]
    assert not shortages(rest, MINS, MIN_BATTERS)
    # 最低人数ぎりぎりの選手は外せない
    catchers = [p for p in team.players if p.position == "C"]
    squad = [p for p in team.players if p.position != "C"] + catchers[:2]
    assert not can_release(squad, catchers[0], MINS, MIN_BATTERS)


def test_selection_effect_is_positive():
    league = generate_league(4, CONFIG, PARTS)
    age_update_retire(league, 21, CONFIG, OFF, 1, None)
    from pennant.draft import make_candidates

    whole = make_candidates(league, 21, CONFIG, PARTS, DS, 1, None)
    pool_mean = statistics.fmean(overall(p) for p in whole)
    proc, _ = run_ai_offseason(league, 21, CONFIG, PARTS, OFF, DS, 1, None, {t.id: 5.0 for t in league.teams}, [t.id for t in league.teams])
    drafted = {x["player_id"] for x in proc.picks if x["phase"] == "draft" and x["player_id"]}
    players = {p.id: p for t in league.teams for p in t.players}
    assert statistics.fmean(overall(players[pid]) for pid in drafted) > pool_mean + 2


# ---- 画面から呼ぶ流れ(api.Game):観戦のみ、操作あり、保存と再開、旧版 ----

def test_spectator_year_end_completes_automatically():
    g = api.Game.new(1, [None] * 12, None, season_seed=13, baselines="default")
    assert g.state.my_team_id is None and g.status()["my_team"] is None
    g.advance(125)
    s = g.year_end()
    assert g.status()["year"] == 2 and g.state.procedure is None and g.status()["offseason"] is None
    assert s["counts"]["players"] == 840 and all(len(t.players) == 70 for t in g.state.league.teams)
    assert any(x["phase"] == "draft" for x in g.state.transactions) and g.transactions(1)["rows"]


@pytest.fixture(scope="module")
def operated():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default", scout_level="large")
    g.advance(125)
    g.year_end()
    return g


def test_operated_team_goes_through_phases_and_can_resume(operated):
    g = operated
    assert g.status()["offseason"]["active"] and g.status()["offseason"]["phase"] == "release" and not g.status()["can_year_end"]
    assert g.state.scout_level == "large" and g.state.calibration == OFF.calibration("large")
    with pytest.raises(ValueError):
        g.year_end()
    v = g.offseason_view()
    assert v["phase"] == "release" and len(v["roster"]) == len(g._team("T01").players) and v["my_team"]["team_id"] == "T01"
    assert all("potential" not in json.dumps(r) for r in v["roster"])  # 公開用に隠し情報は出ない
    ok = [r["player_id"] for r in v["roster"] if r["can_release"]][:2]
    with pytest.raises(ValueError, match="最低人数"):
        g.offseason_release([r["player_id"] for r in v["roster"] if r["position"] == "C"])  # 捕手を全員は外せない
    v = g.offseason_release(ok)
    assert v["my_release_done"] and v["counts"]["released"] == 2
    v = g.offseason_next()
    assert v["phase"] == "draft" and v["counts"]["released"] >= 2 and v["counts"]["candidates"] == 108
    v = g.offseason_advance()
    assert v["is_my_turn"] and v["round"] == 1
    # 保存して読み込むと、途中から再開できる(版 8)
    data = save_game(g.state)
    again = api.Game(load_game(data), dirty=False)
    assert SAVE_FORMAT_VERSION == 8
    v2 = again.offseason_view()
    assert v2["phase"] == "draft" and v2["round"] == v["round"] and v2["is_my_turn"] and [p["player_id"] for p in v2["pool"]] == [p["player_id"] for p in v["pool"]]
    assert v2["pool"][0]["scouting"] == v["pool"][0]["scouting"]  # 評価も同じ(シードから導く)
    pick = v2["pool"][0]["player_id"]
    v2 = again.offseason_pick(pick)
    assert pick in {p.id for p in again._team("T01").players} and v2["counts"]["picked"] >= 1
    chosen = next(p for p in again._team("T01").players if p.id == pick)
    assert chosen.scouting["team_id"] == "T01" and chosen.scouting["year"] == 2
    with pytest.raises(ValueError):
        again.offseason_pick(pick)  # 一覧にいない
    v2 = again.offseason_pass()
    r = again.offseason_auto()
    assert r["finished"] and again.status()["year"] == 2 and again.state.procedure is None
    assert all(len(t.players) == 70 for t in again.state.league.teams)
    page = again.player(pick)
    assert page["player"]["scouting"]["ceiling"] in GRADES and "ratings" not in json.dumps(page)
    a = answers.scouting_answers(again, pick, 2)
    assert a["available"] and a["items"][0]["potential"]
    # 元のゲーム側は「おまかせ」で全部進めても同じ年に進む
    g.offseason_auto()
    assert g.status()["year"] == 2


def test_v7_save_loads_and_continues_with_new_procedure():
    g = api.Game.new(2, [None] * 12, 1, season_seed=5, baselines="default")
    g.advance(1)
    data = save_game(g.state)
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 7
    state = json.loads(files["state.json"])
    for key in ("scout_level", "scout_sd", "procedure", "transactions"):
        del state[key]
    state["configs"]["offseason"]["calibration"] = state["configs"]["offseason"]["calibration"]["medium"]  # 版 7 の形
    del state["configs"]["draft"]
    for team in state["league"]["teams"]:
        for pd in team["players"]:
            del pd["scouting"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    loaded = load_game(buf.getvalue())
    assert loaded.scout_level == "medium" and loaded.procedure is None and loaded.transactions == []
    old = api.Game(loaded, dirty=False)
    old.advance(124)
    old.year_end()
    assert old.status()["offseason"]["phase"] == "release"
    old.offseason_auto()
    assert old.status()["year"] == 2
