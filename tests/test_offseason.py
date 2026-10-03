"""複数年(F2。D-180〜D-189):年度の確定(加齢・能力の更新・引退・補充)、履歴と通算、保存の版6。"""

import copy
import io
import json
import random
import statistics
from fractions import Fraction
import zipfile
from collections import Counter
from datetime import datetime, timezone

import pytest

from pennant import answers, api
from pennant.abilities import items_for
from pennant.config import ConfigError, load_generation_config, load_name_parts
from pennant.generate import generate_league
from pennant.offseason import (
    OffseasonResult,
    age_and_update,
    load_offseason_settings,
    retirement_probability,
    run_offseason,
    validate_offseason_settings,
)
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, read_manifest, save_game
from pennant.season import derive_seed
from pennant.stats import overall
from pennant.war import war_record

SETTINGS = load_offseason_settings()
AT = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)  # 保存日時を固定(2回の保存を比べるため)
CONFIG = load_generation_config()
PARTS = load_name_parts()


def _position_counts(league):
    return {t.id: Counter(p.position for p in t.players) for t in league.teams}


# ---- 設定 ----

def test_settings_file_is_valid_and_has_a_log_policy():
    assert SETTINGS.log_seasons == 1  # 既定:打席ログは直近1シーズン分(D-189)
    assert 25 <= SETTINGS.retirement("start_age") <= 40 and 0 < SETTINGS.retirement("cap") <= 1


def test_settings_validation():
    data = copy.deepcopy(SETTINGS.data)
    data["log_seasons"] = 0
    with pytest.raises(ConfigError, match="log_seasons"):
        validate_offseason_settings(data)
    data = copy.deepcopy(SETTINGS.data)
    data["retirement"]["cap"] = 1.5
    with pytest.raises(ConfigError, match="retirement.cap"):
        validate_offseason_settings(data)
    data = copy.deepcopy(SETTINGS.data)
    del data["retirement"]["max_age"]
    with pytest.raises(ConfigError, match="max_age"):
        validate_offseason_settings(data)


# ---- 引退の確率(D-187):年齢と能力だけで決まる ----

def test_retirement_probability_shape():
    assert retirement_probability(SETTINGS, 22, 60) == 0  # 若くて普通の能力なら引退しない
    assert retirement_probability(SETTINGS, 22, 30) == 0  # 若手は能力が低くても残る(young_floor_age 未満)
    assert retirement_probability(SETTINGS, 30, 30) > 0  # 伸び悩んだ中堅は確率が出る
    assert retirement_probability(SETTINGS, 33, 60) < retirement_probability(SETTINGS, 36, 60) < retirement_probability(SETTINGS, 39, 60)
    assert retirement_probability(SETTINGS, 36, 40) > retirement_probability(SETTINGS, 36, 60)
    assert retirement_probability(SETTINGS, SETTINGS.retirement("max_age"), 80) == 1.0
    assert retirement_probability(SETTINGS, 40, 20) <= SETTINGS.retirement("cap")


# ---- 1人分の更新(D-186):年齢 +1、固定の項目は変えない、潜在能力は変えない ----

def test_age_and_update_keeps_hidden_potential_and_fixed_items():
    league = generate_league(3, CONFIG, PARTS)
    p = league.teams[0].players[0]
    before_potential = dict(p.hidden.potential)
    before_age = p.age
    rng = random.Random(1)
    changes = age_and_update(p, CONFIG, rng)
    assert p.age == before_age + 1 and p.hidden.potential == before_potential
    assert set(changes) == set(items_for(p.role)) and p.state.fatigue == 0
    for item in items_for(p.role):
        assert 1 <= p.ratings[item] <= 99


# ---- 年度の確定の処理(run_offseason):人数・ポジションを保つ、同じ入力で同じ結果 ----

def test_run_offseason_keeps_roster_sizes_and_positions_and_is_reproducible():
    league = generate_league(7, CONFIG, PARTS)
    before = _position_counts(league)
    sizes = {t.id: len(t.players) for t in league.teams}
    ids_before = {p.id for p in league.all_players()}
    twin = generate_league(7, CONFIG, PARTS)
    r1 = run_offseason(league, 12345, CONFIG, PARTS, SETTINGS, 1)
    r2 = run_offseason(twin, 12345, CONFIG, PARTS, SETTINGS, 1)
    assert _position_counts(league) == before and {t.id: len(t.players) for t in league.teams} == sizes
    assert len(r1.retired) == len(r1.rookies) > 0
    assert r1.to_dict() == r2.to_dict()
    assert [p.id for p in league.all_players()] == [p.id for p in twin.all_players()]
    assert {p.id for p in league.all_players()} - ids_before == {n.player_id for n in r1.rookies}
    for n in r1.rookies:  # 新人の ID は Y<年>R<番号>、年齢は若い
        assert n.player_id.startswith("Y02R") and n.age <= 25
    for n in r1.retired:
        assert n.player_id not in {p.id for p in league.all_players()}
    assert len({(p.family_name, p.given_name) for p in league.all_players()}) == len(league.all_players())  # 名前の重複なし
    # 残った選手は全員 ages に、引退した選手は ages にない
    assert set(r1.ages) == {p.id for p in league.all_players()} - {n.player_id for n in r1.rookies}
    assert OffseasonResult.from_dict(json.loads(json.dumps(r1.to_dict()))).to_dict() == r1.to_dict()


def test_different_seed_gives_different_offseason():
    a = run_offseason(generate_league(7, CONFIG, PARTS), 1, CONFIG, PARTS, SETTINGS, 1)
    b = run_offseason(generate_league(7, CONFIG, PARTS), 2, CONFIG, PARTS, SETTINGS, 1)
    assert {n.player_id for n in a.retired} != {n.player_id for n in b.retired}


# ---- 画面から呼ぶ流れ(api.Game):年度の確定、履歴、通算、保存(版6) ----

@pytest.fixture(scope="module")
def two_seasons():
    g = api.Game.new(1, [None] * 12, None, season_seed=13, baselines="default")
    g.advance(125)
    before = {
        "status": g.status(),
        "batting": g.stats("batter", "basic"),
        "war": war_record(g.war_lines()),
        "standings": g.standings(),
        "players": {p.id: (p.age, dict(p.ratings)) for p in g.state.league.all_players()},
    }
    summary = g.year_end()
    g.advance(3)
    return g, before, summary


def test_year_end_requires_season_over():
    g = api.Game.new(1, [None] * 12, None, season_seed=13, baselines="default")
    g.advance(2)
    assert g.status()["can_year_end"] is False and g.year_end_preview()["is_over"] is False
    with pytest.raises(ValueError, match="まだ終わっていません"):
        g.year_end()


def test_year_end_advances_year_and_runs_offseason(two_seasons):
    g, before, summary = two_seasons
    s = g.status()
    assert s["year"] == 2 and s["day"] == 3 and s["can_year_end"] is False and before["status"]["can_year_end"] is True
    assert [c["key"] for c in s["seasons"]] == ["current", "1", "career"]
    assert summary["available"] and summary["year"] == 1 and summary["next_year"] == 2
    assert summary["counts"]["retired"] == len(summary["retired"]) > 0 and summary["counts"]["rookies"] >= summary["counts"]["retired"]  # F3-1:入団 = 引退 + 自由契約で去った人数
    assert summary["counts"]["players"] == len(g.state.league.all_players()) == 840
    assert all(len(t.players) == 70 for t in g.state.league.teams)
    # 残った選手は年齢が +1
    for p in g.state.league.all_players():
        if p.id in before["players"]:
            assert p.age == before["players"][p.id][0] + 1
    # 次のシーズンのシード・オフのシードは前のシーズンのシードから決まる(D-186)
    assert g.state.season.seed == derive_seed(13, "next-season") and g.state.offseasons[0].seed == derive_seed(13, "offseason")
    # 球場補正の履歴と基準値の出発点が更新されている
    assert len(g.state.park_history) == 1 and g.state.baselines.source == "season" and g.park_estimates() is not None
    assert "前のシーズンまで(1シーズン分)" in g.war_note()


def test_history_keeps_final_season_values(two_seasons):
    g, before, _ = two_seasons
    past = g.stats("batter", "basic", season=1)
    assert past["season"] == "1" and past["day"] == 125
    assert [(r["player_id"], r["values"]) for r in past["rows"]] == [(r["player_id"], r["values"]) for r in before["batting"]["rows"]]
    assert war_record(g.state.history[0].war) == before["war"]
    assert g.stats("batter", "war", season="1")["rows"][0]["values"]["war"] == f"{float(g.state.history[0].war[g.stats('batter', 'war', season='1')['rows'][0]['player_id']].war):.2f}"
    # 引退した選手も、過去シーズンの表と選手のページに出る
    retired = {n["player_id"] for n in g.offseason_summary(1)["retired"]}
    shown = {r["player_id"] for r in g.stats("batter", "basic", season=1, qualified=False)["rows"]} | {r["player_id"] for r in g.stats("pitcher", "basic", season=1, qualified=False)["rows"]}
    assert retired & shown
    pid = next(iter(retired & shown))
    page = g.player(pid)
    assert page["player"]["retired"] is True and page["season"] is None and page["history"]["rows"][-1]["season"] == "career"
    with pytest.raises(ValueError):
        g.stats("batter", "basic", season=9)


def test_career_sums_counts_and_war(two_seasons):
    g, _, _ = two_seasons
    career = g.stats("batter", "basic", season="career", qualified=False)
    assert career["season"] == "career"
    past = {r["player_id"]: r["values"] for r in g.stats("batter", "basic", season=1, qualified=False)["rows"]}
    now = {r["player_id"]: r["values"] for r in g.stats("batter", "basic", qualified=False)["rows"]}
    for r in career["rows"]:
        pid = r["player_id"]
        for k in ("G", "PA", "H", "HR"):
            assert int(r["values"][k]) == int(past.get(pid, {}).get(k, 0)) + int(now.get(pid, {}).get(k, 0))
    cw = {r["player_id"]: r["values"]["war"] for r in g.stats("batter", "war", season="career", qualified=False)["rows"]}
    for pid, text in list(cw.items())[:20]:
        total = (g.state.history[0].war[pid].war if pid in g.state.history[0].war else 0) + (g.war_lines()[pid].war if pid in g.war_lines() else 0)
        assert text == f"{float(total):.2f}"
    page = g.player(next(iter(cw)))
    assert [r["season"] for r in page["history"]["rows"]][-1] == "career" and page["history"]["rows"][0]["year"] == 1


def test_offseason_answers_are_hidden_from_public_functions(two_seasons):
    g, before, summary = two_seasons
    text = json.dumps(summary, ensure_ascii=False) + json.dumps(g.stats("batter", "basic", season="career"), ensure_ascii=False) + json.dumps(g.player(summary["rookies"][0]["player_id"]), ensure_ascii=False)
    for key in ("ratings", "potential", "ability_changes", "growth_type", "archetype", "mean_change"):
        assert key not in text
    a = answers.offseason_answers(g, 1, 1)
    assert a["available"] and a["year"] == 1 and len(a["players"]) == 840 - summary["counts"]["rookies"]
    row = a["players"][0]
    pid = row["player_id"]
    p = next(p for p in g.state.league.all_players() if p.id == pid)
    item = row["items"][0]
    assert item["change"] == f"{p.ratings[item['key']] - before['players'][pid][1][item['key']]:+.1f}"


def test_save_v6_round_trip_and_log_policy(two_seasons):
    g, _, _ = two_seasons
    data = save_game(g.state)
    assert read_manifest(data)["format_version"] == SAVE_FORMAT_VERSION == 8
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "logs/season-2.jsonl" in names and "logs/season-1.jsonl" not in names  # 直近 1 シーズン分だけ(D-189)
    again = api.Game(load_game(data), dirty=False)
    assert again.status() == {**g.status(), "dirty": False}
    assert again.stats("batter", "basic", season=1) == g.stats("batter", "basic", season=1)
    assert again.stats("pitcher", "war", season="career") == g.stats("pitcher", "war", season="career")
    assert again.offseason_summary(1) == g.offseason_summary(1)
    assert [a.year for a in again.state.history] == [1] and again.state.history[0].games is None
    assert again.state.offseason_settings.data == g.state.offseason_settings.data
    # 続きが同じ
    g2 = api.Game(load_game(data), dirty=False)
    g.advance(2)
    g2.advance(2)
    assert war_record(g.war_lines()) == war_record(g2.war_lines())
    assert save_game(g.state, AT) == save_game(g2.state, AT)


def test_v5_save_is_converted_to_v6():
    g = api.Game.new(2, [None] * 12, None, season_seed=5, baselines="default")
    g.advance(2)
    data = save_game(g.state)
    # 版5の形にする:year・history・offseasons と offseason の設定を取り除く
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 5
    state = json.loads(files["state.json"])
    for key in ("year", "history", "offseasons"):
        del state[key]
    del state["configs"]["offseason"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    loaded = load_game(buf.getvalue())
    assert loaded.year == 1 and loaded.history == [] and loaded.offseasons == [] and loaded.offseason_settings.log_seasons == 1
    old = api.Game(loaded, dirty=False)
    assert old.status()["year"] == 1 and old.season_choices() == [{"key": "current", "label": "今シーズン(1シーズン目)"}]
    old.advance(123)
    assert old.status()["can_year_end"]
    old.year_end()
    assert old.status()["year"] == 2


def test_broken_history_is_reported():
    g = api.Game.new(1, [None] * 12, None, season_seed=13, baselines="default")
    g.advance(125)
    g.year_end()
    data = save_game(g.state)
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    state = json.loads(files["state.json"])
    state["history"][0]["year"] = 7  # 今が 2 シーズン目なのに、7 シーズン目の履歴
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    with pytest.raises(api.SaveDataError, match="history\\[0\\]"):
        load_game(buf.getvalue())


# ---- 事前運転と校正(D-034 の完成形。D-190、D-197)、通算の加重平均(D-192) ----

from pennant.abilities import BATTER, PITCHER, STYLE_ITEMS
from pennant.metrics import baseline_dependent, compute
from pennant.newgame import new_league
from pennant.offseason import apply_calibration, prerun
from pennant.stats import first_team


def _first_team_means(league):
    players = [p for t in league.teams for p in first_team(t, CONFIG)]
    return {role: statistics.fmean(overall(p) for p in players if p.role == role) for role in (BATTER, PITCHER)}


def test_prerun_is_reproducible_and_keeps_roster_shape():
    a = generate_league(3, CONFIG, PARTS)
    b = generate_league(3, CONFIG, PARTS)
    before = _position_counts(a)
    assert prerun(a, 3, CONFIG, PARTS, SETTINGS) == SETTINGS.prerun_years == 25
    prerun(b, 3, CONFIG, PARTS, SETTINGS)
    assert [p.id for p in a.all_players()] == [p.id for p in b.all_players()]
    assert [dict(p.ratings) for p in a.all_players()] == [dict(p.ratings) for p in b.all_players()]
    from pennant.draft import minimum_batters, minimum_positions, shortages

    assert all(len(t.players) == 70 for t in a.teams) and all(not shortages(t.players, minimum_positions(), minimum_batters()) for t in a.teams)  # F3-1:最低人数だけ守る(D-203)
    ids = [p.id for p in a.all_players()]
    assert len(set(ids)) == len(ids) and all(i.startswith("B") for i in ids)  # 30 年回すと、初期選手(P…)は全員引退している(41 歳で必ず引退)
    assert len({(p.family_name, p.given_name) for p in a.all_players()}) == len(ids)
    other = generate_league(3, CONFIG, PARTS)
    prerun(other, 4, CONFIG, PARTS, SETTINGS)  # 違うシードなら違う結果
    assert [p.id for p in other.all_players()] != ids


def test_calibration_shifts_strength_items_only_and_hits_50():
    league = generate_league(1, CONFIG, PARTS)
    prerun(league, 1, CONFIG, PARTS, SETTINGS)
    before = {p.id: (dict(p.hidden.potential), dict(p.ratings)) for p in league.all_players()}
    cal = SETTINGS.calibration("medium")
    assert cal[BATTER] > 0 and cal[PITCHER] > 0
    apply_calibration(league, cal, CONFIG)
    for p in league.all_players():
        pot, rat = before[p.id]
        for item in p.ratings:
            if item in STYLE_ITEMS:
                assert p.hidden.potential[item] == pot[item] and p.ratings[item] == rat[item]
            else:
                assert p.hidden.potential[item] == pytest.approx(pot[item] + cal[p.role])
                assert p.ratings[item] == pytest.approx(rat[item] + cal[p.role])
    means = _first_team_means(league)
    assert abs(means[BATTER] - 50) < 0.5 and abs(means[PITCHER] - 50) < 0.5


def test_new_league_with_prerun_matches_manual_steps_and_rookies_are_calibrated():
    league = new_league(2, None, CONFIG, PARTS, prerun=True)
    manual = generate_league(2, CONFIG, PARTS)
    prerun(manual, 2, CONFIG, PARTS, SETTINGS)
    apply_calibration(manual, SETTINGS.calibration("medium"), CONFIG)
    assert [(p.id, dict(p.ratings)) for p in league.all_players()] == [(p.id, dict(p.ratings)) for p in manual.all_players()]
    plain = new_league(2, None, CONFIG, PARTS, prerun=False)
    assert [p.id for p in plain.all_players()] == [p.id for p in generate_league(2, CONFIG, PARTS).all_players()]
    # その後に入る新人にも同じ定数が足される(同じ乱数で作った新人の強弱の項目が、定数の分だけ高い)
    from pennant.generate import make_rookie
    from pennant.names import NameGenerator

    r0 = make_rookie(CONFIG, NameGenerator(PARTS, random.Random(5), set()), random.Random(5), BATTER, "SS", "X1", 0.0)
    r1 = make_rookie(CONFIG, NameGenerator(PARTS, random.Random(5), set()), random.Random(5), BATTER, "SS", "X1", 4.0)
    for item in r0.ratings:
        assert r1.ratings[item] == pytest.approx(r0.ratings[item] + (0.0 if item in STYLE_ITEMS else 4.0))
    # 引退の判定は校正前の目盛り(総合値 − 定数)で行うので、定数の有無で引退する人数が大きく変わらない
    r_zero = run_offseason(copy.deepcopy(league), 99, CONFIG, PARTS, SETTINGS, 1, {"batter": 0.0, "pitcher": 0.0})
    r_cal = run_offseason(copy.deepcopy(league), 99, CONFIG, PARTS, SETTINGS, 1, SETTINGS.calibration("medium"))
    assert len(r_cal.retired) > 0.7 * len(r_zero.retired)


def test_game_new_uses_prerun_and_saves_calibration():
    g = api.Game.new(4, [None] * 12, 0, season_seed=4, baselines="default")
    assert g.state.calibration == SETTINGS.calibration("medium")
    means = _first_team_means(g.state.league)
    assert abs(means[BATTER] - 50) < 0.5 and abs(means[PITCHER] - 50) < 0.5
    g.advance(1)
    again = load_game(save_game(g.state))
    assert again.calibration == SETTINGS.calibration("medium")


def test_v6_save_loads_with_zero_calibration():
    g = api.Game.new(2, [None] * 12, None, season_seed=5, baselines="default")
    g.advance(1)
    data = save_game(g.state)
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 6
    state = json.loads(files["state.json"])
    del state["calibration"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    loaded = load_game(buf.getvalue())
    assert loaded.calibration == {"batter": 0.0, "pitcher": 0.0}
    old = api.Game(loaded, dirty=False)
    old.advance(124)
    old.year_end()  # 旧版の選手には校正を足さず、新人にも足さない(定数 0)
    assert old.status()["year"] == 2


def test_career_metrics_are_weighted_averages_of_seasons(two_seasons):
    g, _, _ = two_seasons
    config = api.metrics_config()
    dep_b, dep_p = baseline_dependent(config, "batter"), baseline_dependent(config, "pitcher")
    assert dep_b == {"woba", "wrc_plus", "ops_plus"} and dep_p == {"fip"}
    a = g.state.history[0]
    career = {r["player_id"]: r["values"] for r in g.stats("batter", "saber", season="career", qualified=False)["rows"]}
    now_base = g.baselines()[0]
    checked = 0
    for pid, values in career.items():
        c1 = a.records.batters.get(pid)
        c2 = g.records.total.batters.get(pid)
        if not c1 or not c2 or not c1["PA"] or not c2["PA"]:
            continue
        v1 = compute(config, "batter", c1, {**a.baselines.values, "pf": a.park_factors.get(pid, Fraction(1))})
        v2 = compute(config, "batter", c2, {**now_base.values, "pf": g.player_park_factor(pid)})
        for key in ("woba", "wrc_plus", "ops_plus"):
            expected = (v1[key] * c1["PA"] + v2[key] * c2["PA"]) / (c1["PA"] + c2["PA"])
            assert values[key] == api.format_value(config, key, expected), (pid, key)
        # 元の数から計算できる指標は、元の数の合計から
        total = Counter(c1) + Counter(c2)
        assert values["iso"] == api.format_value(config, "iso", compute(config, "batter", total)["iso"])
        checked += 1
    assert checked > 50
    pc = {r["player_id"]: r["values"] for r in g.stats("pitcher", "saber", season="career", qualified=False)["rows"]}
    for pid, values in list(pc.items())[:30]:
        c1, c2 = a.records.pitchers.get(pid), g.records.total.pitchers.get(pid)
        if not c1 or not c2 or not c1["OUTS"] or not c2["OUTS"]:
            continue
        f1 = compute(config, "pitcher", c1, dict(a.baselines.values))["fip"]
        f2 = compute(config, "pitcher", c2, dict(now_base.values))["fip"]
        assert values["fip"] == api.format_value(config, "fip", (f1 * c1["OUTS"] + f2 * c2["OUTS"]) / (c1["OUTS"] + c2["OUTS"]))
    assert "加重平均" in g.stats("batter", "saber", season="career")["baseline_note"]


def test_career_with_one_season_equals_current():
    g = api.Game.new(1, [None] * 12, None, season_seed=13, baselines="default")
    g.advance(125)
    with pytest.raises(ValueError):
        g.stats("batter", "saber", season="career")  # 1 シーズン目は通算を選べない
    g.year_end()
    # 2 シーズン目の 0 日目:通算 = 1 シーズン目の確定した値と同じ(選手ごとの値)
    past = {r["player_id"]: r["values"] for r in g.stats("batter", "saber", season=1, qualified=False)["rows"]}
    career = {r["player_id"]: r["values"] for r in g.stats("batter", "saber", season="career", qualified=False)["rows"]}
    assert set(past) == set(career)
    for pid in past:
        assert past[pid] == career[pid]
    pp = {r["player_id"]: r["values"] for r in g.stats("pitcher", "saber", season=1, qualified=False)["rows"]}
    cp = {r["player_id"]: r["values"] for r in g.stats("pitcher", "saber", season="career", qualified=False)["rows"]}
    assert pp == cp
