"""F3-1(ドラフトと自由契約、スカウト評価。D-198〜D-211)と、評価の 2 層化・ドラフトの振り返り(D-212〜D-216)、
作り直し③の A〜E の評価とスカウトの一言・1 巡目の入札と抽選(D-368、D-369)の確認。"""

import copy
import io
import json
import statistics
import zipfile

import pytest

from pennant import api
from pennant.abilities import BATTER, PITCHER, abilities_for, strength_items_for
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
from pennant.scouting import GRADES, SCOUT_METHOD, grade_cuts, grade_of, item_sd, item_stats, overall_sd, quick_value, report, strengths
from pennant.stats import overall, projected_overall

CONFIG = load_generation_config()
PARTS = load_name_parts()
OFF = load_offseason_settings()
DS = load_draft_settings()
MINS = minimum_positions()
MIN_BATTERS = minimum_batters()


def finish_renewal(g):
    """契約の段階(D-272)の更改を済ませる。作り直し②で更改は段階の始めに自動で済むので、資金を超えていれば年俸の高い選手から自由契約にするだけ(段階は契約のまま)。"""
    proc = g.state.procedure
    if proc is not None and g.offseason_view()["stage"] == "contract":
        while g.offseason_view()["renewal"]["over"] > 0:
            rows = [r for r in g.contract_table("all", sort="contract", order="desc")["rows"] if r["can_release"] and r["in_team"]]
            g.offseason_contract_release(rows[0]["player_id"])


def roster_rows(g):
    """契約の画面の表のうち、今の自球団の選手の行(D-272)。"""
    return [r for r in g.contract_table("all")["rows"] if r["in_team"]]


def skip_fa(g):
    """FA の段階(F3-2c)と外国人市場(D-376)に入っていたら、自球団の分は何もせずに済ませてドラフトへ。戻り値は画面の情報。"""
    v = g.offseason_view()
    while v["phase"] in ("fa", "foreign"):
        v = g.offseason_next()
    return v


# ---- 設定 ----


def test_settings_and_validation():
    assert DS.rounds == 6 and DS.market_rounds == 3 and DS.default_level == "medium"
    assert item_sd(DS.level_sd("small")) < item_sd(DS.level_sd("medium")) < item_sd(DS.level_sd("large"))
    assert [round(item_sd(DS.level_sd(lv)), 1) for lv in DS.levels] == [3.0, 5.0, 8.0]  # 項目の誤差の合計は F3-1 と同じ(D-212)
    assert DS.level_sd("medium") == {"common": 4.0, "item": 3.0}
    data = copy.deepcopy(DS.data)
    data["scouting"]["levels"]["medium"] = 5
    with pytest.raises(ConfigError, match="scouting.levels.medium"):
        validate_draft_settings(data)
    assert abs(sum(DS.grade_shares.values()) - 1) < 1e-9 and list(DS.grade_shares) == list(GRADES) == ["A", "B", "C", "D", "E"]
    data = copy.deepcopy(DS.data)
    data["scouting"]["grade_shares"]["A"] = 0.5
    with pytest.raises(ConfigError, match="grade_shares"):
        validate_draft_settings(data)
    data = copy.deepcopy(DS.data)
    del data["scouting"]["comment"]["phrases"]["velocity"]
    with pytest.raises(ConfigError, match="phrases.velocity"):
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
    cuts = grade_cuts(players, DS.grade_shares)
    return players, cuts, item_stats(players)


SD5 = {"common": 4.0, "item": 3.0}


def test_report_is_deterministic_and_team_specific(pool):
    players, cuts, stats = pool
    p = players[0]
    a = report(p, "T01", 99, SD5, cuts, stats, DS.comment)
    b = report(p, "T01", 99, SD5, cuts, stats, DS.comment)
    c = report(p, "T02", 99, SD5, cuts, stats, DS.comment)
    assert a.to_dict() == b.to_dict() and a.to_dict() != c.to_dict()
    q = quick_value(p, "T01", 99, SD5, cuts)
    assert abs(q[0] - a.overall) < 1e-9 and q[1] == a.grade  # AI が使う速い値と、画面の評価が一致する
    assert abs(sum(a.items.values()) / len(a.items) - a.overall) < 1e-9
    assert a.method == SCOUT_METHOD == 3 and a.grade in GRADES
    # 画面に出すのは評価と一言だけ(能力の数値・ふれ幅は出さない。D-368)
    pub = a.to_public()
    assert set(pub) == {"team_id", "grade", "comment"} and pub["comment"]
    assert "potential" not in json.dumps(pub) and "growth" not in json.dumps(pub)
    assert report(p, "T01", 100, SD5, cuts, stats, DS.comment).to_dict() != a.to_dict()  # シードが違えば違う


def test_comment_picks_one_or_two_strengths(pool):
    """スカウトの一言(D-368):同じ役割の候補の中で目立つ項目を 1〜2 個。目立つ項目がなければ「目立つ長所はまだない」。"""
    players, cuts, stats = pool
    st = stats["pitcher"]
    mean = {k: v[0] for k, v in st.items()}
    sd = {k: v[1] for k, v in st.items()}
    flat = dict(mean)
    assert strengths(flat, st, DS.comment) == []
    one = {**mean, "velocity": mean["velocity"] + 2 * sd["velocity"]}
    assert strengths(one, st, DS.comment) == ["velocity"]
    two = {**one, "control": mean["control"] + 1.8 * sd["control"]}
    assert strengths(two, st, DS.comment) == ["velocity", "control"]
    far = {**one, "control": mean["control"] + 1.0 * sd["control"]}  # 2 つ目が 1 つ目より 0.5 以上離れていれば 1 つだけ
    assert strengths(far, st, DS.comment) == ["velocity"]
    texts = [report(p, "T01", 5, SD5, cuts, stats, DS.comment).comment for p in players[:120]]
    assert "速球が武器" in "".join(texts) and any("、" in x for x in texts) and any(x == DS.comment["none"] for x in texts)


@pytest.mark.parametrize("level", ["small", "medium", "large"])
def test_two_layer_error_sd_coverage_and_correlation(pool, level):
    """2 層の見誤り(D-212):項目の誤差 3・5・8、総合の誤差 約 2・4・6、項目間の誤差の相関が正(内部の推定値。画面には出さない)。"""
    players, cuts, stats = pool
    sd = DS.level_sd(level)
    errs = []
    overall_errs = []
    pairs = []
    for p in players:
        r = report(p, "T03", 7, sd, cuts, stats, DS.comment)
        items = abilities_for(p.role)
        es = {}
        for item in items:
            e = r.items[item] - p.abilities[item]
            errs.append(e)
            es[item] = e
        oe = r.overall - sum(p.abilities[i] for i in items) / len(items)
        overall_errs.append(oe)
        pairs.append((es[items[0]], es[items[1]]))
    assert abs(statistics.pstdev(errs) - item_sd(sd)) < 0.12 * item_sd(sd)  # 項目:3・5・8
    target = {"small": 2.0, "medium": 4.0, "large": 6.0}[level]
    assert abs(statistics.pstdev(overall_errs) - target) < 0.2 * target + 0.3  # 総合:約 2・4・6
    corr = statistics.correlation([a for a, _ in pairs], [b for _, b in pairs])
    expect = sd["common"] ** 2 / (sd["common"] ** 2 + sd["item"] ** 2)
    assert corr > 0.25 and abs(corr - expect) < 0.15  # 共通の見誤りで、項目間の誤差が正の相関になる


def test_grades_follow_shares_and_potential(pool):
    players, cuts, _ = pool
    assert cuts == sorted(cuts, reverse=True)
    grades = [grade_of(projected_overall(p), cuts) for p in players]
    assert abs(grades.count("A") / len(players) - 0.10) < 0.03 and abs(grades.count("E") / len(players) - 0.15) < 0.05
    by = {g: [projected_overall(p) for p, gr in zip(players, grades) if gr == g] for g in GRADES}
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
        sd = {t.id: SD5 for t in league.teams}
        proc, joined = run_ai_offseason(league, 55, CONFIG, PARTS, OFF, DS, 1, None, sd, [t.id for t in league.teams])
        results.append(([(x["phase"], x["round"], x["team_id"], x["player_id"]) for x in proc.picks], [p.id for p in league.all_players()], proc.lottery))
        assert proc.phase == "done" and not proc.candidates and not proc.market
        assert all(len(t.players) == MAX_ROSTER for t in league.teams)
        assert all(not shortages(t.players, MINS, MIN_BATTERS) for t in league.teams)
        # 1 巡目は入札(全球団が 1 人ずつ。重なったら抽選)、2 巡目は前年の順位が低い順、3 巡目は逆(D-369)
        r1 = [x for x in proc.picks if x["phase"] == "draft" and x["round"] == 1]
        assert sorted(x["team_id"] for x in r1) == sorted(proc.order) and proc.bid_done
        assert [x["winner"] for x in proc.lottery] and all(x["winner"] in x["teams"] for x in proc.lottery)
        assert len({x["player_id"] for x in r1 if x["player_id"]}) == sum(1 for x in r1 if x["player_id"])
        r2 = [x["team_id"] for x in proc.picks if x["phase"] == "draft" and x["round"] == 2]
        r3 = [x["team_id"] for x in proc.picks if x["phase"] == "draft" and x["round"] == 3]
        assert r2 == proc.order and r3 == list(reversed(proc.order))
        assert max(x["round"] for x in proc.picks if x["phase"] == "draft") <= DS.rounds
        # 空き枠がない球団はパス(note=full)、指名は空き枠の分まで
        for t in league.teams:
            drafted = [x for x in proc.picks if x["team_id"] == t.id and x["player_id"]]
            assert len(drafted) <= MAX_ROSTER
        assert all(p.scouting is not None for n in joined for p in [next(p for t in league.teams for p in t.players + t.development if p.id == n.player_id)])  # 育成ドラフトの選手は育成の一覧(D-375)
        assert all(p.scouting["team_id"] == p.team_id for t in league.teams for p in t.players if p.scouting)
    assert results[0] == results[1]  # 同じシードなら同じ結果


def test_ai_release_respects_minimums_and_limit():
    league = generate_league(3, CONFIG, PARTS)
    age_update_retire(league, 9, CONFIG, OFF, 1, None)
    proc, _ = run_ai_offseason(copy.deepcopy(league), 9, CONFIG, PARTS, OFF, DS, 1, None, {t.id: SD5 for t in league.teams}, [t.id for t in league.teams])
    per_team = {}
    for x in proc.released:
        per_team[x["team_id"]] = per_team.get(x["team_id"], 0) + 1
    assert all(n <= DS.ai("release_max") for n in per_team.values())
    assert all(x["age"] >= DS.ai("release_min_age") for x in proc.released)
    team = league.teams[0]
    chosen = ai_release(team, proc, SD5, DS, MINS, MIN_BATTERS)
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
    proc, _ = run_ai_offseason(league, 21, CONFIG, PARTS, OFF, DS, 1, None, {t.id: SD5 for t in league.teams}, [t.id for t in league.teams])
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
    assert all("grade" in x and "comment" in x for x in g.state.transactions if x["player_id"] and x["phase"] in ("draft", "market", "fill"))


@pytest.fixture(scope="module")
def operated():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default", scout_level="large")
    g.advance(125)
    g.year_end()
    finish_renewal(g)
    return g


@pytest.mark.slow
def test_operated_team_goes_through_phases_and_can_resume(operated):
    g = operated
    assert g.status()["offseason"]["active"] and g.status()["offseason"]["phase_label"] == "契約" and not g.status()["can_year_end"]
    assert g.state.scout_level == "large" and g.state.calibration == OFF.calibration("large")
    with pytest.raises(ValueError):
        g.year_end()
    v = g.offseason_view()
    roster = roster_rows(g)
    assert v["stage"] == "contract" and len(roster) == len(g._team("T01").players) and v["my_team"]["team_id"] == "T01"
    assert all("potential" not in json.dumps(r) for r in roster)  # 公開用に隠し情報は出ない
    ok = [r["player_id"] for r in roster if r["can_release"]][:2]
    with pytest.raises(ValueError, match="最低人数"):
        g.offseason_release([r["player_id"] for r in roster if r["position"] == "C"])  # 捕手を全員は外せない
    before = v["counts"]["released"]  # 更改の交渉で自由契約になった選手(F3-2b)を含む
    v = g.offseason_release(ok)
    assert v["my_release_done"] and v["counts"]["released"] == before + 2
    g.offseason_next()
    v = skip_fa(g)
    assert v["phase"] == "draft" and v["counts"]["released"] >= before + 2 and v["counts"]["candidates"] == 108
    v = g.offseason_advance()
    assert v["is_my_turn"] and v["round"] == 1 and v["bidding"] and v["current_team"] is None  # 1 巡目は入札(D-369)
    table = g.decision_table("draft")
    assert [c["key"] for c in table["columns"]][:2] == ["grade", "comment"] and table["sort"]["key"] == "grade"
    assert all(set(r["scouting"]) == {"team_id", "grade", "comment"} for r in table["rows"])  # 能力の数値は出さない(D-368)
    # 保存して読み込むと、途中から再開できる
    data = save_game(g.state)
    again = api.Game(load_game(data), dirty=False)
    assert SAVE_FORMAT_VERSION == 19
    v2 = again.offseason_view()
    t2 = again.decision_table("draft")
    assert v2["phase"] == "draft" and v2["bidding"] and v2["is_my_turn"] and [r["player_id"] for r in t2["rows"]] == [r["player_id"] for r in table["rows"]]
    assert t2["rows"][0]["scouting"] == table["rows"][0]["scouting"]  # 評価も同じ(シードから導く)
    pick = t2["rows"][0]["player_id"]
    v2 = again.offseason_pick(pick)  # 入札
    assert v2["last_bid"]["step"] == 1 and any(x["player_id"] == pick for x in v2["last_bid"]["results"])
    while v2["bidding"]:  # 外れたら入札し直す
        assert not v2["last_bid"]["won"] and v2["bid_step"] > 1
        pick = again.decision_table("draft")["rows"][0]["player_id"]
        v2 = again.offseason_pick(pick)
    assert v2["last_bid"]["won"] and pick in {p.id for p in again._team("T01").players} and v2["counts"]["picked"] >= 12
    chosen = next(p for p in again._team("T01").players if p.id == pick)
    assert chosen.scouting["team_id"] == "T01" and chosen.scouting["year"] == 2 and chosen.scouting["grade"] in GRADES
    assert v2["round"] >= 2 and not v2["bidding"]
    with pytest.raises(ValueError):
        again.offseason_pick(pick)  # 一覧にいない
    v2 = again.offseason_pass()
    r = again.offseason_auto()
    assert r["finished"] and again.status()["year"] == 2 and again.state.procedure is None
    assert all(len(t.players) == 70 for t in again.state.league.teams)
    page = again.player(pick)
    assert page["player"]["abilities"] and '"ratings"' not in json.dumps(page) and "scouting" not in page["player"]  # プロになったら能力を見せ、スカウト評価は見せない(D-352)
    # 元のゲーム側は「おまかせ」で全部進めても同じ年に進む
    g.offseason_auto()
    assert g.status()["year"] == 2


# ---- ドラフトの振り返り(D-216) ----

@pytest.fixture(scope="module")
def reviewed():
    """観戦のみで 2 回のオフを終えた状態(3 シーズン目)。"""
    g = api.Game.new(3, [None] * 12, None, season_seed=17, baselines="default")
    for _ in range(2):
        g.advance(125)
        g.year_end()
    g.advance(5)
    return g


@pytest.mark.slow
def test_draft_review_is_public_and_excludes_prerun(reviewed):
    g = reviewed
    assert g.review_years() == [2, 3]
    v = g.draft_review()
    assert v["team_id"] == "T01" and v["year"] == 3 and v["years"] == [2, 3] and len(v["teams"]) == 12  # 観戦のみ:最初の球団・最新の年度
    assert v["rows"] and all(r["route"] in ("foreign", "draft", "dev_draft", "market", "fill") and r["entry_grade"] in GRADES and r["entry_comment"] for r in v["rows"])
    assert all(r["status"] in ("same", "moved", "left") and isinstance(r["games"], int) for r in v["rows"])
    text = json.dumps(v, ensure_ascii=False)
    for key in ("ratings", "potential", "growth_type", "archetype", "hidden", "cuts", "items", "overall"):
        assert f'"{key}"' not in text
    assert "diff" not in text and "actual_ceiling" not in text and "±" not in text  # 入団時の評価は A〜E と一言だけ(D-368)
    ids = {r["player_id"] for r in v["rows"]}
    players = {p.id: p for p in g.state.league.all_players()}
    assert all(players[pid].scouting["year"] == 3 for pid in ids if pid in players)
    # 事前運転の入団(年を持たない)は、どの年度にも出ない
    prerun_ids = {p.id for p in players.values() if p.scouting and p.scouting.get("year") is None}
    assert prerun_ids and not any(pid in prerun_ids for y in (2, 3) for t in g.state.league.teams for pid in {r["player_id"] for r in g.draft_review(t.id, y)["rows"]})
    v2 = g.draft_review("T05", 2)
    assert v2["team_id"] == "T05" and v2["year"] == 2 and v2["rows"] and all(r["route_label"].startswith(("ドラフト", "育成ドラフト", "外国人市場", "市場", "自動補充")) for r in v2["rows"])
    assert sum(len(g.draft_review(t.id, 2)["rows"]) for t in g.state.league.teams) == sum(1 for x in g.state.transactions if x["player_id"] and x["year"] == 1 and x["phase"] not in ("release", "fa_declare", "fa", "promote"))  # FA の宣言・契約と、育成から支配下に上げたのは入団ではない(F3-2c、D-375)
    with pytest.raises(ValueError):
        g.draft_review("T01", 9)
    with pytest.raises(ValueError):
        g.draft_review("T99")


def test_finalize_makes_room_when_full_team_lacks_minimum():
    """70 人のまま最低人数が足りない球団は、評価の低い選手を外して補充し、70 人と最低人数の両方を守る(D-203)。"""
    from pennant.draft import finalize, start_procedure

    league = generate_league(6, CONFIG, PARTS)
    team = league.teams[10]
    catchers = [p for p in team.players if p.position == "C"]
    for p in catchers:  # 捕手を全員、一塁手に変えて「70 人なのに捕手 0 人」にする
        p.position = "1B"
    assert len(team.players) == MAX_ROSTER and shortages(team.players, MINS, MIN_BATTERS) == {"C": 2}
    proc = start_procedure(league, 31, CONFIG, PARTS, DS, 1, None, None)
    proc.phase = "market"
    sd = {t.id: SD5 for t in league.teams}
    filled = finalize(league, proc, CONFIG, PARTS, DS, sd, None, MINS, MIN_BATTERS)
    assert len(team.players) == MAX_ROSTER and not shortages(team.players, MINS, MIN_BATTERS)
    room = [x for x in proc.released if x.get("note") == "room"]
    assert len(room) == 2 and all(x["team_id"] == team.id for x in room) and sum(1 for n in filled if n.team_id == team.id and n.position == "C") == 2
    assert all(len(t.players) == MAX_ROSTER for t in league.teams)


# ---- 自由契約・市場の、成績つきの選手の一覧(D-222) ----

@pytest.fixture(scope="module")
def release_game():
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    g.advance(125)
    g.year_end()
    finish_renewal(g)
    return g


def test_roster_table_columns_sort_and_missing_stats(release_game):
    g = release_game
    t = g.offseason_table("release", "batter", "basic")
    keys = [c["key"] for c in t["columns"]]
    assert keys[:3] == ["pos", "age", "usage"] and "PA" not in keys and "avg" in keys  # 基本の列 + 選んだ種類の列(打席は重ねない)
    assert t["sort"]["key"] == "war" and t["order"] == "asc" and t["extra_column"]["key"] == "war"  # 初期は WAR の低い順(表にないので固定列)
    wars = [float(r["values"]["war"]) for r in t["rows"] if r["values"]["war"] != "—"]
    assert wars == sorted(wars) and all(r["values"]["war"] == "—" for r in t["rows"][len(wars):])  # 成績のない選手は最後
    assert all(r["can_release"] in (True, False) and r["role"] == "batter" for r in t["rows"])
    assert len(t["rows"]) == sum(1 for p in g._team("T01").players if p.role == "batter")
    text = json.dumps(t, ensure_ascii=False)
    assert '"ratings"' not in text and '"potential"' not in text
    # 全部の列で並べ替え:ポジション・年齢・打席・成績の列・WAR の列・表にない指標(固定列)
    for key, kind in (("pos", "basic"), ("age", "basic"), ("usage", "saber"), ("avg", "basic"), ("war_ra", "basic"), ("obp", "war")):
        role = "pitcher" if key == "war_ra" else "batter"
        t2 = g.offseason_table("release", role, kind, key, "desc")
        assert t2["sort"]["key"] == key and t2["order"] == "desc"
        if key not in [c["key"] for c in t2["columns"]]:
            assert t2["extra_column"]["key"] == key and all(key in r["values"] for r in t2["rows"])
    ages = [r["age"] for r in g.offseason_table("release", "batter", "basic", "age", "asc")["rows"]]
    assert ages == sorted(ages)
    w = g.offseason_table("release", "pitcher", "war")
    assert [c["key"] for c in w["columns"]][:3] == ["pos", "age", "usage"] and "innings" not in [c["key"] for c in w["columns"]] and w["extra_column"] is None
    assert w["seasons"] == [{"key": "current", "label": "今シーズン(1シーズン目)"}]  # 1 シーズン目は通算を選べない
    with pytest.raises(ValueError):
        g.offseason_table("release", "batter", "basic", "contact")  # 能力の項目は公開用の表では使えない
    with pytest.raises(ValueError):
        g.offseason_table("release", "batter", "basic", season="career")


@pytest.mark.slow
def test_market_table_marks_candidates_without_stats(release_game):
    g = api.Game(copy.deepcopy(release_game.state), dirty=False)
    v = g.offseason_view()
    assert v["minimums"]["positions"]["C"] == 2 and v["minimums"]["batters"] == 15 and v["minimums"]["labels"]["SP"] == "先発"
    g.offseason_release([next(r["player_id"] for r in roster_rows(g) if r["can_release"])])
    g.offseason_next()
    skip_fa(g)
    g.offseason_advance()
    g.offseason_next()
    g.offseason_next()  # 育成ドラフト(D-375)
    m = g.offseason_table("market", "batter", "basic")
    assert m["phase"] == "market" and len(m["rows"]) == sum(1 for p in g.state.procedure.market if p.role == "batter")
    with_stats = [r for r in m["rows"] if r["has_stats"]]
    without = [r for r in m["rows"] if not r["has_stats"]]
    candidates = [r for r in without if r["player_id"].startswith("D")]  # 指名されなかった候補(成績なし)
    assert with_stats and candidates and all(r["former_team"] for r in with_stats) and all(r["values"]["avg"] == "—" and r["former_team"] == "" for r in candidates)
    assert all(r["values"]["avg"] == "—" for r in without)
    assert m["rows"].index(without[0]) > m["rows"].index(with_stats[-1])  # 成績のない選手は最後
    g.offseason_auto()
    assert g.status()["year"] == 2
    g.advance(3)
    t = g.offseason_table if g.state.procedure else None
    assert t is None
    g.advance(122)
    g.year_end()
    finish_renewal(g)
    c = g.offseason_table("release", "batter", "basic", season="career")
    assert c["season"] == "career" and [x["key"] for x in c["seasons"]] == ["current", "career"]
