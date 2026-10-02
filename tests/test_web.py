"""ブラウザでの実行の技術検証(D-082)のテスト用ページ(web/)の確認。

ブラウザそのものは CI では動かさない。ここでは、ページが使う Python 側(web/bench.py)が
標準ライブラリだけで動くこと、公開用のまとめ(scripts/build_web.py)が正しいこと、
ページが外部に通信したり、ブラウザの保存領域に書いたりしないことを、ファイルの中身から確かめる。
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
    return _load("bench", WEB / "bench.py")


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
    for name in ("index.html", "app.js", "worker.js", "bench.py", "pennant.zip"):
        assert (site / name).exists()
    with zipfile.ZipFile(site / "pennant.zip") as zf:
        names = set(zf.namelist())
    expected = {"pennant/" + p.relative_to(ROOT / "src" / "pennant").as_posix() for p in build.package_files()}
    assert names == expected == set(info["package_files"])
    assert "pennant/__init__.py" in names and "pennant/data/names.json" in names
    assert all(n.endswith((".py", ".json")) for n in names)
    assert not (site / "pyodide").exists()  # 公開用には Pyodide を入れない(配布元から読む)


# ---- 通信と保存(ファイルの中身から確かめる) ----

def _web_text() -> str:
    return "\n".join((WEB / n).read_text(encoding="utf-8") for n in ("index.html", "app.js", "worker.js"))


def test_no_browser_storage_writes():
    text = _web_text()
    for pattern in (r"localStorage\.setItem", r"sessionStorage\.setItem", r"indexedDB\.open", r"document\.cookie\s*=", r"caches\.open", r"IDBFS"):
        assert not re.search(pattern, text), pattern


def test_only_pyodide_cdn_is_contacted():
    hosts = set(re.findall(r"https://([a-zA-Z0-9.-]+)", _web_text()))
    assert hosts == {"cdn.jsdelivr.net"}
    for word in ("XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource"):
        assert word not in _web_text()


def test_content_security_policy_limits_connections():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    rules = dict((part.split()[0], part.split()[1:]) for part in csp.split(";") if part.strip())
    assert rules["default-src"] == ["'none'"]
    assert rules["connect-src"] == ["'self'", "https://cdn.jsdelivr.net"]
    assert set(rules["script-src"]) <= {"'self'", "https://cdn.jsdelivr.net", "'wasm-unsafe-eval'"}


def test_test_name_field_is_not_autosaved():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    field = re.search(r'<input id="team-name"[^>]*>', html).group(0)
    assert 'autocomplete="off"' in field
