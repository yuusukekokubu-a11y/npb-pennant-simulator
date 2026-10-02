"""ブラウザの画面(web/。D-107)と、開発者向けの測定ページ(web/dev/。D-082、D-111)の確認。

ブラウザそのものは CI では動かさない。ここでは、ページが使う Python 側(web/bridge.py・web/dev/bench.py)が
標準ライブラリだけで動くこと、公開用のまとめ(scripts/build_web.py)が正しいこと、
ページが外部に通信したり、ブラウザの保存領域に書いたりしないことを、ファイルの中身から確かめる。
ブラウザでの通しの確認(新規開始 → 進行 → 順位表 → 保存 → 読み込み → 最後まで)は、開発者が手元で行う(web/README.md)。
"""

import importlib.util
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bench_module():
    return _load("bench", WEB / "dev" / "bench.py")


@pytest.fixture(scope="module")
def bench(bench_module):
    b = bench_module.Bench(1)
    b.play_games(12)
    return b


# ---- Python 側(ページが呼ぶ関数) ----

def test_bench_plays_whole_days(bench):
    assert bench.games_played() == 12 and bench.days == 2  # 1日=各リーグ3試合
    assert bench.plate_appearances() > 12 * 60


def test_same_seed_same_log(bench_module, bench):
    other = bench_module.Bench(1)
    other.play_games(12)
    assert other.export_log() == bench.export_log()


def test_file_round_trip(bench_module, bench):
    data = bench.export_first_game()
    info = bench_module.decode_log(data)
    assert info["games"] == 1
    assert info["plate_appearances"] == len(bench.results[0].log)
    assert info["runs"] == bench.results[0].home_runs + bench.results[0].away_runs
    assert info["sha256"] == bench_module.log_digest(data)


@pytest.mark.parametrize("data", [b"not gzip", __import__("gzip").compress(b'{"format": "other"}\n')])
def test_broken_file_gives_japanese_error(bench_module, data):
    with pytest.raises(ValueError, match="読めません|ではありません"):
        bench_module.decode_log(data)


def test_test_team_name_is_not_kept(bench_module, bench):
    name = "テスト用の球団名"
    before = [t.name for t in bench.league.teams]
    text = bench.sample_header(name)
    assert name in text
    assert [t.name for t in bench.league.teams] == before  # リーグの本体には残らない
    import gzip

    assert name.encode() not in gzip.decompress(bench.export_log())  # ログのファイルにも入らない


def test_log_memory_is_measured(bench):
    assert bench.log_memory_bytes() > 100_000


# ---- 公開用のまとめ ----

def test_build_contains_code_and_fictional_data_only(tmp_path):
    build = _load("build_web", ROOT / "scripts" / "build_web.py")
    info = build.build(tmp_path / "site")
    site = tmp_path / "site"
    for name in ("index.html", "app.js", "worker.js", "bridge.py", "pennant.zip", "sample-save.sav", "sample-save.json", "expected-fingerprints.json",
                 "dev/index.html", "dev/app.js", "dev/worker.js", "dev/bench.py"):
        assert (site / name).exists()
    with zipfile.ZipFile(site / "pennant.zip") as zf:
        names = set(zf.namelist())
    expected = {"pennant/" + p.relative_to(ROOT / "src" / "pennant").as_posix() for p in build.package_files()}
    assert names == expected == set(info["package_files"])
    assert "pennant/__init__.py" in names and "pennant/data/names.json" in names
    assert all(n.endswith((".py", ".json")) for n in names)
    assert not (site / "pyodide").exists()  # 公開用には Pyodide を入れない(配布元から読む)


# ---- 通信と保存(ファイルの中身から確かめる) ----

PAGES = (WEB, WEB / "dev")  # 遊ぶための画面と、測定ページ


def _web_text() -> str:
    return "\n".join((d / n).read_text(encoding="utf-8") for d in PAGES for n in ("index.html", "app.js", "worker.js"))


def test_no_browser_storage_writes():
    text = _web_text()
    for pattern in (r"localStorage\.setItem", r"sessionStorage\.setItem", r"indexedDB\.open", r"document\.cookie\s*=", r"caches\.open", r"IDBFS"):
        assert not re.search(pattern, text), pattern


def test_only_pyodide_cdn_is_contacted():
    hosts = set(re.findall(r"https://([a-zA-Z0-9.-]+)", _web_text()))
    assert hosts == {"cdn.jsdelivr.net"}
    for word in ("XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource"):
        assert word not in _web_text()


@pytest.mark.parametrize("page", PAGES, ids=["main", "dev"])
def test_content_security_policy_limits_connections(page):
    html = (page / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    rules = dict((part.split()[0], part.split()[1:]) for part in csp.split(";") if part.strip())
    assert rules["default-src"] == ["'none'"]
    assert rules["connect-src"] == ["'self'", "https://cdn.jsdelivr.net"]
    assert set(rules["script-src"]) <= {"'self'", "https://cdn.jsdelivr.net", "'wasm-unsafe-eval'"}


def test_test_name_field_is_not_autosaved():
    html = (WEB / "dev" / "index.html").read_text(encoding="utf-8")
    field = re.search(r'<input id="team-name"[^>]*>', html).group(0)
    assert 'autocomplete="off"' in field


def test_page_shows_the_same_fingerprints_as_the_script(bench_module):
    """ブラウザのページは、PC のスクリプトと同じ関数・同じ文章で指紋を出す(D-089)。"""
    import json

    from pennant.fingerprint import fingerprints, format_fingerprints

    report = bench_module.fingerprint_report()
    assert report["text"] == format_fingerprints(fingerprints())
    expected = json.loads((ROOT / "tests" / "data" / "fingerprints.json").read_text(encoding="utf-8"))
    for key in ("league", "game", "days"):
        assert expected[key] in report["text"]


@pytest.mark.parametrize("page", PAGES, ids=["main", "dev"])
def test_no_favicon_request(page):
    assert '<link rel="icon" href="data:,">' in (page / "index.html").read_text(encoding="utf-8")


def test_compute_stats(bench):
    info = bench.compute_stats()
    assert info["batters"] > 100 and info["pitchers"] > 50 and info["seconds"] >= 0  # 2日分の試合に出た選手


def test_save_check_flow(bench_module):
    """テスト用ページの保存・読み込みの確認:60日目まで進めて保存 → 読み込んで最後まで → 指紋 (d) と一致。"""
    import json

    r = bench_module.save_check_start("テスト用ZQ球団", days=5)
    assert r["name_in"] == ["state.json"] and r["file_name"].startswith("save-") and "ZQ" not in r["file_name"]
    done = bench_module.continue_to_end(r["data"])
    expected = json.loads((ROOT / "tests" / "data" / "fingerprints.json").read_text(encoding="utf-8"))
    assert done["loaded_day"] == 5 and done["digest"] == expected["season"]


def test_sample_check(bench_module):
    import json

    info = json.loads((ROOT / "tests" / "data" / "sample-save.json").read_text(encoding="utf-8"))
    data = (ROOT / "tests" / "data" / "sample-save.sav").read_bytes()
    assert bench_module.continue_sample(data, info["continue_to_day"]) == info["continuation_digest"]


# ---- 遊ぶための画面(web/。D-107、D-108) ----

# 画面に出してはいけない、能力値・隠し情報を表す言葉(D-108)
HIDDEN_WORDS = ("能力", "潜在", "成長", "隠し", "ミート", "パワー", "選球眼", "走力", "スタミナ", "制球", "球威", "奪三振力", "rating", "potential", "archetype", "growth")


@pytest.fixture()
def bridge():
    module = _load("bridge", WEB / "bridge.py")
    module._game = None
    return module


def _ok(text):
    import json

    data = json.loads(text)
    assert data["ok"] is True, data
    return data["value"]


def test_bridge_flow(bridge):
    """新規開始 → 進める → 保存 → 読み込み。読み込みに失敗しても、今のゲームは残る。"""
    import json

    pv = _ok(bridge.preview(3))
    assert len(pv["order"]) == 12
    assert _ok(bridge.check(3, json.dumps(["  "] + [""] * 11)))[0]
    bad = json.loads(bridge.new_game(3, 4, json.dumps(["  "] + [""] * 11), 0))
    assert bad["ok"] is False and "空白" in bad["problems"][0] and bridge._game is None
    view = _ok(bridge.new_game(3, 4, json.dumps(["テスト球団"] + [""] * 11), 2))
    assert view["status"]["day"] == 0 and view["status"]["dirty"] is True
    view = _ok(bridge.advance(3))
    assert view["status"]["day"] == 3 and len(view["recent"]) == 3
    data = bridge.save("2026-10-02")
    info = _ok(bridge.save_info())
    assert info["file_name"] == "save-20261002.sav" and info["bytes"] == len(data) and info["status"]["dirty"] is False
    broken = json.loads(bridge.load(b"broken"))
    assert broken["ok"] is False and broken["problems"] and "今のゲームは、そのまま" in broken["message"]
    assert _ok(bridge.view())["status"]["day"] == 3  # 今のゲームはそのまま
    loaded = _ok(bridge.load(data))
    assert loaded["standings"] == view["standings"] and loaded["status"]["dirty"] is False


def test_main_page_has_no_hidden_words():
    """遊ぶための画面の文章とスクリプトに、能力値・隠し情報を表す言葉がない(D-108)。"""
    for name in ("index.html", "app.js", "worker.js", "bridge.py"):
        text = (WEB / name).read_text(encoding="utf-8")
        for word in HIDDEN_WORDS:
            assert word not in text, f"{name}: {word}"


def test_main_page_basics():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "初回は約12MB" in html and 'id="real-name-notice"' in html
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "beforeunload" in app and "state.dirty" in app
    assert re.search(r'a\.download = r\.value\.file_name', app)  # ファイル名は Python が作る日付だけの名前
