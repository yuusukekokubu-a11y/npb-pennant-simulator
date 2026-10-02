"""設定ファイルの検証(範囲外・欠損・合計の不一致などで、分かりやすいエラーが出るか)。"""

import json

import pytest

from pennant import ConfigError, generate_league, load_generation_config, load_name_parts
from pennant.config import (
    default_generation_data,
    validate_generation_config,
    validate_name_parts,
)


def _errors(data) -> str:
    with pytest.raises(ConfigError) as info:
        validate_generation_config(data, "テスト用")
    return str(info.value)


def test_default_config_is_valid():
    cfg = load_generation_config()
    assert cfg["format_version"] == 1
    assert load_name_parts().surnames


def test_missing_value_is_reported_with_its_place():
    data = default_generation_data()
    del data["potential"]["noise_sd"]
    message = _errors(data)
    assert "potential.noise_sd" in message
    assert "値がありません" in message


def test_out_of_range_value():
    data = default_generation_data()
    data["potential"]["noise_sd"] = -1
    assert "potential.noise_sd" in _errors(data)


def test_not_a_number():
    data = default_generation_data()
    data["initial_ages"]["mean"] = "26"
    assert "数値が必要です" in _errors(data)


def test_shares_must_sum_to_one():
    data = default_generation_data()
    data["batter_archetypes"]["power_hitter"]["share"] = 0.5
    message = _errors(data)
    assert "batter_archetypes" in message and "合計が 1" in message


def test_unbalanced_corrections_are_rejected():
    data = default_generation_data()
    data["batter_archetypes"]["power_hitter"]["corrections"]["power"] = 25
    message = _errors(data)
    assert "power" in message and "リーグ平均" in message


def test_role_balance_depends_on_roster():
    """先発・救援の人数を変えると、役割の補正の釣り合いが崩れることを知らせる。"""
    data = default_generation_data()
    data["roster"]["pitchers"] = {"SP": 24, "RP": 8}
    message = _errors(data)
    assert "pitcher_roles" in message and "stamina" in message


def test_unknown_item_in_corrections():
    data = default_generation_data()
    data["pitcher_qualities"]["power"]["corrections"]["velocity"] = 3
    assert "velocity" in _errors(data)


def test_item_in_two_aging_groups():
    data = default_generation_data()
    data["aging"]["groups"]["power"]["items"].append("speed")
    assert "1項目は1グループだけ" in _errors(data)


def test_style_item_must_be_fixed():
    data = default_generation_data()
    data["aging"]["groups"]["fixed"]["items"] = []
    data["aging"]["groups"]["technical"]["items"].append("gb_fb")
    message = _errors(data)
    assert "gb_fb" in message


def test_age_range_must_be_ordered():
    data = default_generation_data()
    data["initial_ages"]["min"] = 40
    data["initial_ages"]["max"] = 30
    assert "min(40)は max(30)より小さく" in _errors(data)


def test_wrong_format_version():
    data = default_generation_data()
    data["format_version"] = 99
    assert "バージョン" in _errors(data)


def test_several_problems_are_reported_at_once():
    data = default_generation_data()
    data["potential"]["noise_sd"] = -1
    data["initial_ages"]["sd"] = -1
    with pytest.raises(ConfigError) as info:
        validate_generation_config(data, "テスト用")
    assert len(info.value.problems) >= 2


def test_broken_json_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{ 壊れた JSON", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_generation_config(path)
    assert "JSON として読めません" in str(info.value)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_generation_config(tmp_path / "nothing.json")
    assert "ファイルを読めません" in str(info.value)


def test_config_file_round_trip(tmp_path):
    path = tmp_path / "my.json"
    path.write_text(json.dumps(default_generation_data(), ensure_ascii=False), encoding="utf-8")
    assert load_generation_config(path)["league"]["num_leagues"] == 2


def _name_data():
    return {
        "format_version": 1,
        "surnames": ["山", "川"],
        "given_names": ["一", "二"],
        "places": [f"地名{i}" for i in range(12)],
        "team_nicknames": [f"愛称{i}" for i in range(12)],
        "stadium_suffixes": ["球場"],
        "league_names": ["A", "B"],
    }


def test_name_parts_duplicates_are_rejected():
    data = _name_data()
    data["surnames"] = ["山", "山"]
    with pytest.raises(ConfigError) as info:
        validate_name_parts(data, "テスト用")
    assert "重複" in str(info.value)


def test_name_parts_empty_list_is_rejected():
    data = _name_data()
    data["given_names"] = []
    with pytest.raises(ConfigError):
        validate_name_parts(data, "テスト用")


def test_too_few_name_parts_give_a_clear_error(config):
    """部品が少なすぎて重複しない名前を作れないときは、分かりやすいエラーを出す。"""
    parts = validate_name_parts(_name_data(), "テスト用")
    with pytest.raises(ConfigError) as info:
        generate_league(1, config, parts)
    assert "部品を増やしてください" in str(info.value)


def test_too_few_places_give_a_clear_error(config):
    data = _name_data()
    data["places"] = ["地名"]
    parts = validate_name_parts(data, "テスト用")
    with pytest.raises(ConfigError) as info:
        generate_league(1, config, parts)
    assert "places" in str(info.value)
