"""成績の画面(②)の操作の関数と、答え合わせ用の関数の確認(D-114〜D-119)。"""

import hashlib
import json
import re
from fractions import Fraction

import pytest

from pennant import answers, api
from pennant.abilities import ITEM_LABELS, items_for
from pennant.config import ConfigError
from pennant.game_stats import game_story, narrate, story_text
from pennant.metrics import compute, default_metrics_data, validate_metrics_config
from pennant.records import qualifying_outs, qualifying_plate_appearances, season_records

HIDDEN_KEYS = {"ratings", "potential", "growth_type", "archetype", "ability_drift", "hidden"}


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


@pytest.fixture(scope="module")
def game():
    g = api.Game.new(1, [None] * 12, 2, season_seed=13, baselines="default")
    g.advance(20)
    return g


# ---- 個人成績 ----

def test_stats_default_tables(game):
    """表の既定(D-116):列と並び順。"""
    b = game.stats("batter", "basic")
    assert [c["label"] for c in b["columns"]] == ["試合", "打席", "打数", "安打", "本塁打", "打点", "得点", "打率", "出塁率", "長打率", "OPS"]
    assert (b["sort"]["key"], b["order"], b["qualified"]) == ("avg", "desc", True)
    p = game.stats("pitcher", "basic")
    assert [c["label"] for c in p["columns"]] == ["登板", "先発", "勝利", "敗戦", "セーブ", "ホールド", "投球回", "奪三振", "防御率"]
    assert (p["sort"]["key"], p["order"]) == ("era", "asc")
    bs = game.stats("batter", "saber")
    assert [c["key"] for c in bs["columns"]] == ["PA", "woba", "wrc_plus", "ops_plus", "iso", "babip", "k_pct", "bb_pct"]  # OPS は基本へ(D-130)
    assert bs["sort"]["key"] == "wrc_plus" and bs["order"] == "desc"  # 第2弾①で wRC+ の高い順に変えた
    ps = game.stats("pitcher", "saber")
    assert [c["key"] for c in ps["columns"]] == ["OUTS", "fip", "k_pct", "bb_pct"] and ps["sort"]["key"] == "era" and ps["order"] == "asc"


def test_stats_sort_matches_records(game):
    """並べ替えが、計算本体の集計(元の数と指標)の順と同じ。値なしは最後。"""
    config = api.metrics_config()
    rec = season_records(p.result for p in game.state.season.played)
    for role, kind, sort in (("batter", "basic", "avg"), ("batter", "basic", "HR"), ("batter", "saber", "babip"), ("pitcher", "basic", "era"), ("pitcher", "saber", "k_pct")):
        for order in ("asc", "desc"):
            t = game.stats(role, kind, sort, order, qualified=False)
            group = rec.batters if role == "batter" else rec.pitchers
            def key(pid):
                counts = group[pid]
                return counts[sort] if sort in config["counts"][role] else compute(config, role, counts)[sort]
            ids = [r["player_id"] for r in t["rows"]]
            assert set(ids) == set(group)
            values = [key(pid) for pid in ids]
            present = [v for v in values if v is not None]
            assert values[: len(present)] == present  # 値なしは最後
            assert present == sorted(present, reverse=order == "desc")
            assert [r["rank"] for r in t["rows"]] == list(range(1, len(ids) + 1))


def test_sort_by_any_metric_with_extra_column(game):
    """並び順には、基本・セイバーの全指標を使える。表にない指標なら extra_column(D-132)。"""
    keys = [c["key"] for c in api.sortable_keys("batter")]
    assert keys[:11] == ["G", "PA", "AB", "H", "HR", "RBI", "R", "avg", "obp", "slg", "ops"] and "babip" in keys and "woba" in keys
    assert len(keys) == len(set(keys)) and all(api.is_sortable(api.metrics_config(), "batter", k) for k in keys)
    assert [c["key"] for c in api.sortable_keys("pitcher")][:9] == ["G", "GS", "W", "L", "SV", "HLD", "OUTS", "SO", "era"]
    t = game.stats("batter", "saber", sort="avg")
    assert t["extra_column"]["key"] == "avg" and t["order"] == "desc"
    by_basic = game.stats("batter", "basic", sort="avg")
    assert by_basic["extra_column"] is None
    assert [r["player_id"] for r in t["rows"]] == [r["player_id"] for r in by_basic["rows"]]
    assert all(r["values"]["avg"] == b["values"]["avg"] for r, b in zip(t["rows"], by_basic["rows"]))
    assert set(t["rows"][0]["values"]) == {c["key"] for c in t["columns"]} | {"avg"}
    p = game.stats("pitcher", "saber")  # 投手のセイバーの既定(防御率)は表にないので、固定列になる
    assert p["extra_column"]["key"] == "era" and p["order"] == "asc"
    with pytest.raises(ValueError):
        game.stats("batter", "basic", sort="stamina")  # 能力の項目は、公開用の関数では使えない
    with pytest.raises(ValueError):
        api.sortable_keys("coach")


def test_ability_table_can_keep_stats_sort(game):
    """能力の表も、成績の指標で並べられる(並び順を保ったまま切り替えたとき。固定列)。"""
    t = answers.ability_table(game, "pitcher", 1, sort="era", order="asc", qualified=False)
    assert t["extra_column"]["key"] == "era" and t["sort"]["key"] == "era"
    ids = [r["player_id"] for r in t["rows"]]
    assert ids == [r["player_id"] for r in game.stats("pitcher", "basic", sort="era", order="asc", qualified=False)["rows"]]
    assert all("era" in r["values"] for r in t["rows"])
    by_default = answers.ability_table(game, "pitcher", 1, sort="era", order=None, qualified=False)
    assert by_default["order"] == "asc" and [r["player_id"] for r in by_default["rows"]] == ids  # 向きの省略時は「よい」向き
    plain = answers.ability_table(game, "pitcher", 1, sort="stamina")
    assert plain["extra_column"] is None and "era" not in plain["rows"][0]["values"]
    assert [c["key"] for c in answers.columns("batter", 2)][-2:] == ["growth_type", "archetype"]


def test_qualified_follows_definition(game):
    """規定打席 = 試合数 × 3.1(四捨五入)、規定投球回 = 試合数 × 1.0(D-096)。"""
    rec = game.records.total
    for role, group, owner, need, unit in (
        ("batter", rec.batters, rec.batter_team, qualifying_plate_appearances, "PA"),
        ("pitcher", rec.pitchers, rec.pitcher_team, qualifying_outs, "OUTS"),
    ):
        expected = {pid for pid, c in group.items() if c[unit] >= need(rec.teams[owner[pid]]["G"])}
        assert set(game.select_players(role)) == expected
        assert {r["player_id"] for r in game.stats(role)["rows"]} == expected
    assert qualifying_plate_appearances(20) == 62 and qualifying_outs(20) == 60


def test_stats_filters(game):
    league = 1
    team = next(t for t in game.state.league.teams if t.league_index == league)
    rows = game.stats("batter", qualified=False, league=league)["rows"]
    assert rows and all(game._team(r["team_id"]).league_index == league for r in rows)
    rows = game.stats("batter", qualified=False, team_id=team.id)["rows"]
    assert rows and {r["team_id"] for r in rows} == {team.id}
    with pytest.raises(ValueError):
        game.stats("coach")
    with pytest.raises(ValueError):
        game.stats("batter", "ability")  # 能力は答え合わせ用の関数で


def test_column_descriptions_come_from_metrics_data(game):
    """見出しの解説は、指標の定義データの解説(D-119)。"""
    data = json.loads(open("src/pennant/data/metrics.json", encoding="utf-8").read())
    for role in ("batter", "pitcher"):
        for kind in ("basic", "saber"):
            for c in game.stats(role, kind)["columns"]:
                if c["type"] == "metric":
                    assert c["description"].startswith(data["metrics"][c["key"]]["description"])
                    assert c["category"] == data["metrics"][c["key"]]["category"]
                else:
                    assert c["description"] == data["count_descriptions"][role][c["key"]]


def test_cache_is_reused_until_day_advances(game):
    """集計は、日が進むまで使い回す。進めたら、新しい試合の分だけ足す。"""
    g = api.Game.new(1, [None] * 12, 0, season_seed=13, baselines="default")
    g.advance(2)
    cache = g.records
    first = cache.per_game[0]
    assert g.records is cache and len(cache.per_game) == 12
    g.advance(1)
    assert len(g.records.per_game) == 18 and g.records.per_game[0] is first
    total = season_records(p.result for p in g.state.season.played)
    assert g.records.total.batters == total.batters and g.records.total.pitchers == total.pitchers and g.records.total.teams == total.teams


# ---- 選手・チーム・試合 ----

def test_player_page(game):
    pid = game.stats("pitcher")["rows"][0]["player_id"]
    d = game.player(pid)
    assert d["player"]["id"] == pid and d["player"]["hand"] in ("右投げ", "左投げ")
    assert set(d["season"]["tables"]) == {"basic", "saber"} and d["season"]["qualified"] is True
    days = [g["day"] for g in d["games"]]
    assert days == sorted(days, reverse=True) and len(days) >= 3
    rec = game.records.total.pitchers[pid]
    assert d["season"]["tables"]["basic"]["values"]["G"] == str(rec["G"])
    with pytest.raises(ValueError):
        game.player("nobody")


def test_team_page(game):
    t = game.team("T01")
    rec = game.records.total.teams["T01"]
    assert (t["record"]["games"], t["record"]["runs"], t["record"]["runs_allowed"]) == (rec["G"], rec["R"], rec["RA"])
    assert t["record"]["wins"] == rec["W"] and t["record"]["losses"] == rec["L"]
    assert len(t["players"]) == len(game._team("T01").players)


def test_games_on_and_last_day(game):
    day = game.games_on(20)
    assert len(day["games"]) == 6 and all(g["day"] == 20 for g in day["games"])
    assert game.last_day_games() == day
    assert game.games_on(21)["games"] == []


def test_game_page_matches_narrate(game):
    """試合の文章ログは、確認用の形式(narrate)と同じ構造から作る(D-114)。"""
    s = game.state.season
    names = {t.id: t.name for t in s.league.teams}
    for n in (0, 37, 119):
        d = game.game(n)
        assert story_text(d["story"]) == narrate(s.played[n].result, s.players, names)
        marks = [p["decision"] for p in d["story"]["pitchers"]]
        assert marks.count("勝") + marks.count("敗") in (0, 2)  # 引き分けでなければ勝ちと負けが1人ずつ
        er = sum(p["earned_runs"] for p in d["story"]["pitchers"])
        rec = game.records.per_game[n]
        assert er == sum(c["ER"] for c in rec.pitchers.values())
    with pytest.raises(ValueError):
        game.game(10_000)


def test_narrate_text_is_unchanged():
    """文章ログの文字は固定した値と同じ(構造のデータから作るように直しても、内容は変えない。第3弾①で選手が変わったので値を取り直した)。"""
    from pennant.newgame import new_league
    from pennant.parks import neutralize_parks
    from pennant.season import Season

    league = new_league(1, prerun=False)  # 事前運転(D-190)の前の選手で、文章の形だけを確かめる
    neutralize_parks(league)  # 球場の倍率を入れる前(第2弾②a より前)と同じ試合にする
    s = Season(league, 13)
    s.play_days(3)
    names = {t.id: t.name for t in s.league.teams}
    text = "".join(narrate(p.result, s.players, names) for p in s.played)
    assert hashlib.sha256(text.encode()).hexdigest() == "3cf08a5baa7a08184880ae9dd8234e374758842a80bcf3f97702b30239e370e5"


def test_public_functions_have_no_hidden_info(game):
    """公開用の関数の戻り値に、能力値・隠し情報(その項目名も)が入らない(D-108)。"""
    pid = game.stats("batter")["rows"][0]["player_id"]
    values = [game.stats(r, k, qualified=False) for r in ("batter", "pitcher") for k in ("basic", "saber")]
    page = game.player(pid)
    values += [page, game.team("T03"), game.games_on(5), game.game(3), game.last_day_games(), game.draft_review()]
    assert not (_keys(values) & HIDDEN_KEYS)
    # 能力の項目名は、入団時のスカウト評価(推定値。F3-1)の中にだけ出てよい。それ以外の場所には出ない
    stripped = dict(page)
    stripped["player"] = {k: v for k, v in page["player"].items() if k != "scouting"}
    text = json.dumps([v for v in values if v is not page] + [stripped], ensure_ascii=False)
    for label in ITEM_LABELS.values():
        assert label not in text or label in ("肩", "捕球"), label


# ---- 答え合わせ(D-115) ----

@pytest.mark.parametrize("value, text", [(17.4, "20-"), (19.9, "20-"), (20, "20"), (22.4, "20"), (22.5, "25"), (52.6, "55"), (80, "80"), (80.2, "80+"), (95, "80+")])
def test_scale(value, text):
    assert answers.scale(value)["text"] == text


def test_ability_table(game):
    t = answers.ability_table(game, "pitcher", 1, sort="control", order="desc", qualified=False)
    assert [c["key"] for c in t["columns"]] == list(items_for("pitcher"))
    raw = [game.state.season.players[r["player_id"]].ratings["control"] for r in t["rows"]]
    assert raw == sorted(raw, reverse=True)
    assert {r["player_id"] for r in t["rows"]} == set(game.select_players("pitcher", qualified=False))
    for r in t["rows"]:
        assert all(re.fullmatch(r"(20|25|30|35|40|45|50|55|60|65|70|75|80)[+-]?", v) for v in r["values"].values())
    t2 = answers.ability_table(game, "batter", 2)
    assert t2["columns"][-2]["key"] == "growth_type" and t2["rows"][0]["values"]["growth_type"] in ("早熟", "標準", "晩成")
    with pytest.raises(ValueError):
        answers.ability_table(game, "batter", 3)


def test_player_answers(game):
    pid = game.stats("batter")["rows"][0]["player_id"]
    p = game.state.season.players[pid]
    a1 = answers.player_answers(game, pid, 1)
    assert [i["current"] for i in a1["items"]] == [answers.scale(p.ratings[k])["text"] for k in items_for("batter")]
    assert "growth_type" not in a1 and "potential" not in a1["items"][0]
    a2 = answers.player_answers(game, pid, 2)
    assert a2["items"][0]["potential"] == answers.scale(p.hidden.potential[items_for("batter")[0]])["text"]
    assert a2["growth_type"] and a2["archetype"]


# ---- 指標の定義データ(D-119) ----

@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["tables"]["batter"]["basic"]["columns"].append("XYZ"), "tables.batter.basic.columns"),
        (lambda d: d["tables"]["pitcher"]["saber"].update(sort="avg"), "tables.pitcher.saber.sort"),
        (lambda d: d["tables"]["batter"].pop("game"), "tables.batter.game"),
        (lambda d: d["count_descriptions"]["batter"].pop("PA"), "count_descriptions.batter.PA"),
        (lambda d: d["count_descriptions"]["pitcher"].update(XYZ="?"), "count_descriptions.pitcher.XYZ"),
        (lambda d: d.pop("tables"), "tables"),
    ],
)
def test_tables_validation(change, message):
    data = default_metrics_data()
    change(data)
    with pytest.raises(ConfigError, match=re.escape(message)):
        validate_metrics_config(data)
