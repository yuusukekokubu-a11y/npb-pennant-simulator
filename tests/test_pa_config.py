"""打席の計算の設定ファイルの検証(受け入れ条件11)と、確認用スクリプト。"""

import importlib.util
from pathlib import Path

import pytest

from pennant import ConfigError
from pennant.pa_config import default_pa_data, load_pa_config, validate_pa_config

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_plate_appearance.py"


def _errors(data) -> str:
    with pytest.raises(ConfigError) as info:
        validate_pa_config(data, "テスト用")
    return str(info.value)


def test_default_is_valid():
    assert load_pa_config()["format_version"] == 1


def test_missing_league_rate():
    data = default_pa_data()
    del data["stage1"]["league_rates"]["walk"]
    assert "stage1.league_rates.walk" in _errors(data)


def test_ratio_out_of_range():
    data = default_pa_data()
    data["stage1"]["batter_effects"]["home_run"]["power"] = 0
    assert "stage1.batter_effects.home_run.power" in _errors(data)


def test_ability_of_the_wrong_role():
    data = default_pa_data()
    data["stage1"]["batter_effects"]["strikeout"]["stuff"] = 1.1  # 球威は投手の能力
    assert "この役割では使えない能力" in _errors(data)


def test_batted_ball_shares_must_sum_to_one():
    data = default_pa_data()
    data["batted_ball"]["league_shares"]["ground"] = 0.6
    assert "合計が 1" in _errors(data)


def test_unknown_position_in_fielder_shares():
    data = default_pa_data()
    data["fielder_shares"]["fly"]["DH"] = 0.0
    assert "DH" in _errors(data)


def test_unfieldable_share_must_be_below_hit_rate():
    data = default_pa_data()
    data["in_play"]["line"]["unfieldable_share"] = 0.9
    assert "unfieldable_share" in _errors(data)


def test_too_large_stage1_rates():
    data = default_pa_data()
    data["stage1"]["league_rates"]["strikeout"] = 0.55
    data["stage1"]["league_rates"]["walk"] = 0.3
    assert "インプレーの率" in _errors(data)


def _load_script():
    spec = importlib.util.spec_from_file_location("inspect_plate_appearance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_runs(capsys):
    assert _load_script().main(["--pa", "2000", "--leagues", "1"]) == 0
    out = capsys.readouterr().out
    for heading in ("結果の割合", "感度表", "左右の相性", "守備の効果", "担当ポジション"):
        assert heading in out


def test_script_reports_config_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert _load_script().main(["--config", str(bad), "--leagues", "1"]) == 1
    assert "問題があります" in capsys.readouterr().err
