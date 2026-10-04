"""実装⑥:セーブ・ロードの受け入れ条件の確認。"""

import io
import json
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pennant import savegame
from pennant.fingerprint import _digest, season_record
from pennant.newgame import MAX_TEAM_NAME_LENGTH, TeamNameError, check_team_name, new_league, public_player
from pennant.savegame import GameState, SaveDataError, default_file_name, load_game, read_manifest, save_game, start_game
from pennant.season import Season
from pennant.season_config import default_season_data, validate_season_config
from pennant.storage import LocalFolderStorage, MemoryStorage, StorageError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
AT = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def short_state(seed=4, team_names=None, days=0):
    """短いシーズン(同じ相手と2試合。10日)の状態。テストを速くするため。"""
    data = default_season_data()
    data["schedule"]["games_per_opponent"] = 2
    state = start_game(seed, team_names)
    state.season = Season(state.season.league, seed, season_config=validate_season_config(data))
    state.season.play_days(days)
    return state


def _zip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _rezip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _edit_state(data: bytes, change) -> bytes:
    files = _zip(data)
    state = json.loads(files["state.json"])
    change(state)
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    return _rezip(files)


@pytest.fixture(scope="module")
def saved():
    state = short_state(days=4)
    return state, save_game(state, AT)


# ---- 受け入れ条件1:保存して読み込むと、状態が完全に一致する ----

def test_round_trip_is_identical(saved):
    state, data = saved
    loaded = load_game(data)
    assert save_game(loaded, AT) == data  # 読み込んだ状態を保存し直すと、1バイトも違わない
    assert loaded.season.day == 4 and len(loaded.season.played) == len(state.season.played)
    assert [p.result for p in loaded.season.played] == [p.result for p in state.season.played]
    for i in loaded.season.league_indexes():
        assert loaded.season.standings(i) == state.season.standings(i)
    assert loaded.season.rotation == state.season.rotation
    assert [p.state for p in loaded.league.all_players()] == [p.state for p in state.league.all_players()]
    assert [p.hidden for p in loaded.league.all_players()] == [p.hidden for p in state.league.all_players()]


def test_file_layout(saved):
    _, data = saved
    files = _zip(data)
    assert set(files) == {"manifest.json", "state.json", "logs/season-1.jsonl"}
    manifest = json.loads(files["manifest.json"])
    assert manifest["format_version"] == savegame.SAVE_FORMAT_VERSION and manifest["saved_at"].startswith("2026-10-02")
    state = json.loads(files["state.json"])
    assert {"seed", "rng", "configs", "league", "season"} <= set(state)
    assert set(state["configs"]) == {"generation", "names", "plate_appearance", "game", "season", "baselines", "offseason", "draft", "contracts", "negotiation"}
    assert state["baselines"]["source"] == "default" and len(state["baselines"]["re24"]) == 24  # 第2弾①の基準値(版 3)
    assert "potential" in state["league"]["teams"][0]["players"][0]["hidden"]


def test_saved_at_does_not_change_the_content(saved):
    state, data = saved
    other = save_game(state, datetime(2030, 1, 1, tzinfo=timezone.utc))
    a, b = _zip(data), _zip(other)
    assert a["state.json"] == b["state.json"] and a["logs/season-1.jsonl"] == b["logs/season-1.jsonl"]


def test_replay_a_game_from_a_save(saved):
    state, data = saved
    loaded = load_game(data)
    original = state.season.played[7].result
    assert loaded.season.replay_game(7) == original


# ---- 受け入れ条件2:途中で保存しても、続きが同じ ----

@pytest.mark.parametrize("day", [0, 1, 5, 10])
def test_continue_after_save_is_the_same(day):
    straight = short_state(days=10)
    resumed = load_game(save_game(short_state(days=day), AT))
    resumed.season.play_to_end()
    assert season_record(resumed.season.result()) == season_record(straight.season.result())


# ---- 受け入れ条件3:別の版で保存した見本を読んで、続きが一致する ----

def test_sample_file_from_another_version():
    info = json.loads((DATA / "sample-save.json").read_text(encoding="utf-8"))
    data = (DATA / "sample-save.sav").read_bytes()
    state = load_game(data)
    assert state.season.day == info["save_day"]
    assert read_manifest(data)["format_version"] == 1  # 見本は版1。版2へ変換して読む
    resaved = load_game(save_game(state, AT))
    assert save_game(resaved, AT) == save_game(state, AT) and resaved.my_team_id is None
    state.season.play_days(info["continue_to_day"] - state.season.day)
    assert _digest(season_record(state.season.result())) == info["continuation_digest"], info["made_with"]


# ---- 受け入れ条件4:壊れた・編集されたデータ ----

def _assert_error(data, *patterns):
    with pytest.raises(SaveDataError) as e:
        load_game(data)
    text = str(e.value)
    for pat in patterns:
        assert re.search(pat, text), text
    return text


def test_not_a_save_file():
    _assert_error(b"hello", "ZIP")


def _first_pitcher(s):
    return next(p for p in s["league"]["teams"][0]["players"] if p["role"] == "pitcher")


def test_missing_file(saved):
    files = _zip(saved[1])
    del files["state.json"]
    _assert_error(_rezip(files), r"state\.json: ファイルが入っていません")


def test_broken_json(saved):
    files = _zip(saved[1])
    files["state.json"] = files["state.json"][:-10]
    _assert_error(_rezip(files), r"state\.json: JSON として読めません\(\d+ 行目 \d+ 文字目")


@pytest.mark.parametrize(
    "change, pattern",
    [
        (lambda s: s.pop("seed"), r"「seed」がありません"),
        (lambda s: _first_pitcher(s)["ratings"].update(stamina=9999), r"teams\[0\]\.players\[\d+\]\.ratings\.stamina: 能力値が範囲外です"),
        (lambda s: s["league"]["teams"][1]["players"].__delitem__(slice(0, 50)), r"teams\[1\]\.players: 選手の数が範囲外です"),
        (lambda s: s["league"]["teams"][0]["players"][1].update(id=s["league"]["teams"][0]["players"][0]["id"]), r"ID .* が重複しています"),
        (lambda s: s["season"].update(day=6), r"日付と試合が合いません"),
        (lambda s: s["league"]["teams"][0]["players"][0]["hidden"].update(growth_type="fast"), r"growth_type: early / normal / late"),
        (lambda s: s["league"]["teams"][0].update(display_name="球団\u0007"), r"display_name: 制御文字"),
        (lambda s: s["configs"]["game"]["rules"].update(innings="9"), r"configs\.game"),
        (lambda s: s["season"]["rotation"].update(T01=-1), r"ローテーションの位置"),
    ],
)
def test_edited_state_gives_japanese_reason(saved, change, pattern):
    _assert_error(_edit_state(saved[1], change), pattern)


def test_broken_log_line(saved):
    files = _zip(saved[1])
    lines = files["logs/season-1.jsonl"].split(b"\n")
    lines[2] = b'{"number": 2, "result": {"home_team_id": "T01"}}'
    files["logs/season-1.jsonl"] = b"\n".join(lines)
    _assert_error(_rezip(files), r"logs/season-1\.jsonl の 3 行目: 試合の記録の形が違います")


def test_too_new_version(saved):
    files = _zip(saved[1])
    m = json.loads(files["manifest.json"])
    m["format_version"] = savegame.SAVE_FORMAT_VERSION + 1
    files["manifest.json"] = json.dumps(m).encode()
    _assert_error(_rezip(files), "新しい版", "プログラムを新しく")


def test_failed_load_keeps_current_state(saved):
    state, data = saved
    current = load_game(data)
    before = save_game(current, AT)
    with pytest.raises(SaveDataError):
        load_game(_edit_state(data, lambda s: s["season"].update(day=6)))
    assert save_game(current, AT) == before  # 今の状態は変わらない


# ---- 受け入れ条件5:旧版の変換 ----

def test_real_v1_to_v2_conversion(saved):
    """版1(自球団の情報がない)のファイルは、版2へ変換して読む。"""
    files = _zip(saved[1])
    state = json.loads(files["state.json"])
    state.pop("user")
    m = json.loads(files["manifest.json"])
    m["format_version"] = 1
    files.update({"state.json": json.dumps(state).encode(), "manifest.json": json.dumps(m).encode()})
    loaded = load_game(_rezip(files))
    assert loaded.my_team_id is None and loaded.season.day == 4


def test_my_team_is_saved():
    state = short_state(days=1)
    state.my_team_id = state.league.teams[3].id
    assert load_game(save_game(state, AT)).my_team_id == state.league.teams[3].id
    bad = _edit_state(save_game(state, AT), lambda s: s["user"].update(my_team_id="T99"))
    _assert_error(bad, "自球団 'T99' が、球団の一覧にありません")


def test_old_version_is_converted(monkeypatch, saved):
    # ダミーの「版4」:season.day の名前が today だった、という古い形を作る(今の版を5とみなす)
    def old_v2(state):
        state["season"]["today"] = state["season"].pop("day")

    old = _edit_state(saved[1], old_v2)
    calls = []

    def v1_to_v2(bundle):
        calls.append(1)
        bundle["state"]["season"]["day"] = bundle["state"]["season"].pop("today")
        return bundle

    monkeypatch.setattr(savegame, "SAVE_FORMAT_VERSION", 12)
    monkeypatch.setattr(savegame, "MIGRATIONS", {**savegame.MIGRATIONS, 11: v1_to_v2})
    state = load_game(old)
    assert calls == [1] and state.season.day == 4


def test_missing_conversion_is_reported(monkeypatch, saved):
    monkeypatch.setattr(savegame, "SAVE_FORMAT_VERSION", 13)
    monkeypatch.setattr(savegame, "MIGRATIONS", {**savegame.MIGRATIONS, 11: lambda b: b})
    _assert_error(saved[1], "バージョン 12 から 13 への変換がありません")


# ---- 受け入れ条件6:球団名の入力 ----

def test_blank_names_become_default():
    league = new_league(1, [None, ""] + ["自作の球団"] + [None] * 9)
    assert league.teams[0].display_name is None and league.teams[0].name == league.teams[0].default_name
    assert league.teams[2].name == "自作の球団"


@pytest.mark.parametrize(
    "name, reason",
    [
        ("   ", "空白だけ"),
        ("あ" * (MAX_TEAM_NAME_LENGTH + 1), "長すぎます"),
        ("球団​名", "制御文字"),
        ("球団\n名", "制御文字"),
    ],
)
def test_bad_team_names(name, reason):
    assert reason in check_team_name(name)
    with pytest.raises(TeamNameError, match=reason):
        new_league(1, [name] + [None] * 11)


def test_duplicate_team_names():
    with pytest.raises(TeamNameError, match="2番目の球団: 「ＡＢＣ球団」は 1番目の球団と同じ名前"):
        new_league(1, ["ABC球団", "ＡＢＣ球団"] + [None] * 10)
    default = new_league(1).teams[3].name
    with pytest.raises(TeamNameError, match="同じ名前"):
        new_league(1, [default] + [None] * 11)  # 空欄の球団の初期名とも重ならない


def test_team_name_only_in_state(tmp_path):
    name = "テスト用ZQ球団"
    state = short_state(seed=6, team_names=[name] + [None] * 11, days=2)
    state.name = "見本"
    data = save_game(state, AT)
    files = _zip(data)
    where = sorted(n for n, d in files.items() if name.encode("utf-8") in d)
    assert where == ["state.json"]  # 状態の中にだけ入る(ログ・manifest には入らない)
    assert name not in default_file_name(date(2026, 10, 2)) and default_file_name(date(2026, 10, 2)) == "save-20261002.sav"
    storage = LocalFolderStorage(tmp_path / "saves")
    storage.write(default_file_name(date(2026, 10, 2)), data)
    assert [p.name for p in (tmp_path / "saves").iterdir()] == ["save-20261002.sav"]  # 一時ファイルは残らない
    assert load_game(storage.read("save-20261002.sav")).league.teams[0].name == name


# ---- 受け入れ条件7:画面用の選手情報 ----

def test_public_player_has_no_hidden_info():
    league = new_league(1)
    for p in league.all_players()[:50]:
        info = public_player(p)
        text = json.dumps(info, ensure_ascii=False)
        assert not {"hidden", "potential", "growth_type", "archetype", "ability_drift", "ratings"} & set(info)
        assert p.hidden.archetype not in text and p.hidden.growth_type not in text.replace(p.name, "")


# ---- 保存の差し替え口と、ファイルの読み書きの集約(受け入れ条件10) ----

def test_memory_storage():
    s = MemoryStorage()
    s.write("a.sav", b"123")
    assert s.read("a.sav") == b"123" and s.list() == ["a.sav"]
    with pytest.raises(StorageError, match="見つかりません"):
        s.read("b.sav")
    with pytest.raises(StorageError, match="使えない文字"):
        s.write("../x.sav", b"")


def test_local_folder_storage(tmp_path):
    s = LocalFolderStorage(tmp_path / "f")
    assert s.list() == []
    s.write("x.sav", b"abc")
    assert s.read("x.sav") == b"abc" and s.list() == ["x.sav"]
    with pytest.raises(StorageError, match="読めません"):
        s.read("none.sav")
    assert LocalFolderStorage().folder == Path("~/.npb-pennant-simulator").expanduser()


def test_only_storage_reads_and_writes_files():
    """計算本体でファイルを読み書きするのは storage.py だけ(D-083、D-101)。"""
    pattern = re.compile(r"\bopen\(|\.read_text\(|\.write_text\(|\.read_bytes\(|\.write_bytes\(|resources\.files|os\.remove|shutil\.")
    offenders = []
    for path in (ROOT / "src" / "pennant").glob("*.py"):
        if path.name == "storage.py":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert offenders == []


def test_script_runs(capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("inspect_save", ROOT / "scripts" / "inspect_save.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--days", "3"]) == 0
    out = capsys.readouterr().out
    for heading in ("保存と読み込み", "中身の構成", "壊れたデータ"):
        assert heading in out
