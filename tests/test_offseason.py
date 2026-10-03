"""複数年(F2。D-180〜D-189):年度の確定(加齢・能力の更新・引退・補充)、履歴と通算、保存の版6。"""

import copy
import io
import json
import random
import zipfile
from collections import Counter

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
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
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
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
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
    assert summary["counts"]["retired"] == summary["counts"]["rookies"] == len(summary["retired"]) > 0
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
    assert read_manifest(data)["format_version"] == SAVE_FORMAT_VERSION == 6
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
    assert save_game(g.state) == save_game(g2.state)


def test_v5_save_is_converted_to_v6():
    g = api.Game.new(2, [None] * 12, 1, season_seed=5, baselines="default")
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
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
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
