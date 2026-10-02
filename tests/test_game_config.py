"""1試合の進行の設定ファイルの検証(受け入れ条件11)と、確認用スクリプト。"""

import importlib.util
from pathlib import Path

import pytest

from pennant import ConfigError
from pennant.game_config import default_game_data, load_game_config, validate_game_config

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_game.py"


def _errors(data) -> str:
    with pytest.raises(ConfigError) as info:
        validate_game_config(data, "テスト用")
    return str(info.value)


def test_default_is_valid():
    assert load_game_config()["rules"]["innings"] == 9


def test_missing_value():
    data = default_game_data()
    del data["pitching"]["starter_run_limit"]
    assert "pitching.starter_run_limit" in _errors(data)


def test_probability_out_of_range():
    data = default_game_data()
    data["advancement"]["double_play"]["base"] = 1.5
    assert "advancement.double_play.base" in _errors(data)


def test_unknown_ability_in_effects():
    data = default_game_data()
    data["advancement"]["double_play"]["fielder"]["power"] = 1.1
    assert "使えない能力" in _errors(data)


def test_max_innings_must_not_be_below_innings():
    data = default_game_data()
    data["rules"]["max_innings"] = 8
    assert "rules.max_innings" in _errors(data)


def test_allow_tie_must_be_boolean():
    data = default_game_data()
    data["rules"]["allow_tie"] = "yes"
    assert "true か false" in _errors(data)


def test_relief_roles_must_fit():
    data = default_game_data()
    data["pitching"]["closer_count"] = 5
    data["pitching"]["setup_count"] = 5
    assert "救援の人数" in _errors(data)


def test_near_positions_must_be_fielders():
    data = default_game_data()
    data["rest"]["near_positions"]["SS"] = ["DH"]
    assert "rest.near_positions.SS" in _errors(data)


def _load_script():
    spec = importlib.util.spec_from_file_location("inspect_game", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_runs(capsys):
    assert _load_script().main(["--games", "20"]) == 0
    out = capsys.readouterr().out
    for heading in ("1回表", "スコア", "投手", "試合単位の集計", "打席の結果の割合", "得点の分布"):
        assert heading in out


def test_script_reports_config_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert _load_script().main(["--config", str(bad)]) == 1
    assert "問題があります" in capsys.readouterr().err
