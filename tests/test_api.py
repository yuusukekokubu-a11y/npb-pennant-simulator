"""画面から呼ぶ操作の関数(src/pennant/api.py。D-107、D-108)の確認。"""

import json
import re

import pytest

from pennant import api
from pennant.fingerprint import LEAGUE_SEED, SEASON_SEED, _digest, season_record
from pennant.savegame import SaveDataError

# 公開用の戻り値に出てはいけない、能力値・隠し情報の項目名(D-108)
HIDDEN_KEYS = {"ratings", "potential", "growth_type", "archetype", "contact", "power", "eye", "speed", "stamina", "control", "stuff", "strikeout"}


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


@pytest.fixture(scope="module")
def game():
    g = api.Game.new(5, ["テスト球団A"] + [None] * 11, 3, baselines="default")
    g.advance(7)
    return g


def test_preview_teams_lists_default_names():
    pv = api.preview_teams(5)
    teams = [t for lg in pv["leagues"] for t in lg["teams"]]
    assert len(pv["leagues"]) == 2 and len(teams) == 12 and pv["order"] == [t["id"] for t in teams]
    assert all(t["default_name"] for t in teams)
    json.dumps(pv)


def test_check_team_names_gives_reason_per_field():
    pv = api.preview_teams(5)
    default_1 = pv["leagues"][0]["teams"][1]["default_name"]
    names = ["", "  ", "あ" * 30, "同じ名前", "同じ名前", default_1] + [""] * 6
    problems = api.check_team_names(5, names)
    assert problems[0] is None  # 空欄は架空の初期名になる
    assert "空白だけ" in problems[1] and "長すぎ" in problems[2]
    assert problems[3:] == [None] * 9  # ほかの欄に問題があるときは、重複はまだ見ない
    problems = api.check_team_names(5, ["", "", "", "同じ名前", "同じ名前"] + [""] * 7)
    assert problems[3] is None and problems[4] and "同じ" in problems[4]
    assert api.check_team_names(5, ["新しい名前"] + [""] * 11) == [None] * 12


def test_new_game_rejects_bad_input():
    with pytest.raises(api.TeamNameError):
        api.Game.new(5, ["  "] + [None] * 11, 0, baselines="default")
    for bad in (12, -1, True, "0"):
        with pytest.raises(api.TeamNameError, match="自球団"):
            api.Game.new(5, [None] * 12, bad, baselines="default")


def test_status_and_my_team(game):
    st = game.status()
    assert st["day"] == 7 and st["total_days"] == 125 and st["games_played"] == 42 and st["dirty"] is True
    assert st["my_team"]["id"] == game.state.league.teams[3].id
    json.dumps(st, ensure_ascii=False)


def test_standings_match_season(game):
    """順位表は、実装④の順位(season.standings)そのまま。自球団の行だけ is_mine。"""
    table = game.standings()
    assert len(table["leagues"]) == 2
    mine = [r for lg in table["leagues"] for r in lg["rows"] if r["is_mine"]]
    assert [r["team_id"] for r in mine] == [game.state.my_team_id]
    for lg in table["leagues"]:
        rows = game.state.season.standings(lg["index"])
        assert [(r["rank"], r["team_id"], r["wins"], r["losses"], r["ties"], r["games"]) for r in lg["rows"]] == [
            (s.rank, s.team_id, s.wins, s.losses, s.ties, s.games) for s in rows
        ]
        for r, s in zip(lg["rows"], rows):
            assert re.fullmatch(r"-|1\.000|\.\d{3}", r["pct"])
            assert r["games_behind"] == ("-" if s.rank == 1 or s.games_behind == 0 else f"{s.games_behind:.1f}")
    json.dumps(table, ensure_ascii=False)


def test_recent_games(game):
    games = game.recent_games()
    assert len(games) == 5 and [g["day"] for g in games] == sorted((g["day"] for g in games), reverse=True)
    assert all(g["outcome"] in ("勝", "負", "分") for g in games)


def test_public_functions_have_no_hidden_info(game):
    """公開用の関数の戻り値に、能力値・隠し情報が入らない(D-108)。"""
    team_id = game.state.league.teams[0].id
    values = [api.preview_teams(5), game.status(), game.standings(), game.recent_games(), game.teams(), game.players(team_id)]
    assert not (_keys(values) & HIDDEN_KEYS)
    players = game.players(team_id)
    assert len(players) == len(game.state.league.teams[0].players)
    json.dumps(players, ensure_ascii=False)


def test_save_and_load_keep_my_team(game):
    out = game.save(today="2026-10-02")
    assert out["file_name"] == "save-20261002.sav" and game.dirty is False
    assert "テスト" not in out["file_name"]  # ファイル名に球団名を入れない
    loaded = api.Game.load(out["data"])
    assert loaded.dirty is False and loaded.state.my_team_id == game.state.my_team_id
    assert loaded.standings() == game.standings()
    loaded.advance(1)
    assert loaded.dirty is True and loaded.status()["day"] == 8


def test_broken_save_gives_japanese_error():
    with pytest.raises(SaveDataError, match="セーブデータ"):
        api.Game.load(b"not a zip file")


def test_advance_stops_at_end_and_matches_fingerprint_d():
    """画面と同じ流れ(新規開始 → 進める → 保存 → 読み込み → 最後まで)で、指紋 (d) と同じ結果になる。
    自球団は指紋の元に入らない。"""
    expected = json.loads((__import__("pathlib").Path(__file__).parent / "data" / "fingerprints.json").read_text(encoding="utf-8"))
    g = api.Game.new(LEAGUE_SEED, [None] * 12, 7, season_seed=SEASON_SEED, baselines="default")
    g.advance(30)
    g = api.Game.load(g.save(today="2026-10-02")["data"])
    st = g.advance(1000)
    assert st["is_over"] and st["day"] == 125 and st["games_played"] == 750
    assert g.advance(1)["day"] == 125
    assert _digest(season_record(g.state.season.result())) == expected["season"]
