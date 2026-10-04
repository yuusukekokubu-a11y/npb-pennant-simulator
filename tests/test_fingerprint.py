"""再現性の確認(D-088、D-089):決まったシードの結果の指紋が、Python の版を問わず同じになること。

正しい値は tests/data/fingerprints.json に固定してある。CI は Python 3.10・3.12・3.14 で、このテストを実行する。
計算本体を意図して変えたとき(校正の値の変更など)は、`python scripts/fingerprint.py --json` で作り直し、
報告に「指紋が変わった理由」を書く。
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pennant import fingerprint as fpmod

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = json.loads((ROOT / "tests" / "data" / "fingerprints.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fp():
    return fpmod.fingerprints()


def test_fingerprints_match_expected(fp):
    for key in ("fingerprint_version", "league", "game", "days", "season", "records", "save", "baselines", "metrics2", "parks", "park_estimates", "run_values", "war", "multiyear", "procedure", "contracts", "negotiation"):
        assert fp[key] == EXPECTED[key], f"{key} の指紋が、固定した正しい値と違います(Python {sys.version.split()[0]})"
    assert fp["counts"] == EXPECTED["counts"]


def test_same_in_a_fresh_process_with_other_hash_seed():
    """文字列の並び順(Python を起動するたびに変わる)に、結果が左右されないこと。"""
    code = "import json; from pennant.fingerprint import fingerprints; print(json.dumps(fingerprints()))"
    env = dict(os.environ, PYTHONHASHSEED="12345", PYTHONPATH=str(ROOT / "src"))
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
    other = json.loads(out)
    for key in ("league", "game", "days", "season", "records", "save", "baselines", "metrics2", "parks", "park_estimates", "run_values", "war", "multiyear", "procedure", "contracts", "negotiation"):
        assert other[key] == EXPECTED[key]


def test_without_parks_matches_the_values_before_parks():
    """球場の倍率をすべて 1.0 にしたときの指紋 (a)〜(h) が、固定した値と完全に一致する(回帰の確認。D-137)。

    ②a では「倍率を入れる前の指紋」と一致することを確かめた。第3弾①で選手生成が変わったので、
    固定した値は新しい生成で倍率なしのときの値に作り直した(tests/data/fingerprints-parks-off.json)。
    """
    before = json.loads((ROOT / "tests" / "data" / "fingerprints-parks-off.json").read_text(encoding="utf-8"))
    off = fpmod.fingerprints(parks=False)
    for key in ("league", "game", "days", "season", "records", "save", "baselines", "metrics2"):
        assert off[key] == before[key], key
    assert off["league"] == EXPECTED["league"]  # (a) は倍率があっても変わらない


def test_fingerprint_changes_when_seed_changes(monkeypatch, fp):
    monkeypatch.setattr(fpmod, "GAME_SEED", fpmod.GAME_SEED + 1)
    other = fpmod.fingerprints(quick=True)
    assert other["league"] == fp["league"]
    assert other["game"] != fp["game"]


def test_floats_are_not_allowed_in_the_source():
    with pytest.raises(TypeError, match="小数"):
        fpmod._digest({"rating": 50.5})


def test_format_shows_all_three(fp):
    text = fpmod.format_fingerprints(fp)
    for key in ("league", "game", "days", "season", "records", "save", "baselines", "metrics2"):
        assert fp[key] in text


def test_script_reports_match(capsys):
    spec = importlib.util.spec_from_file_location("fingerprint_script", ROOT / "scripts" / "fingerprint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([]) == 0
    assert "○ 一致" in capsys.readouterr().out
