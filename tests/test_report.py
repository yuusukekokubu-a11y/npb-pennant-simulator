"""確認用スクリプト(scripts/inspect_league.py)と集計表の確認。"""

import importlib.util
from pathlib import Path

from pennant.stats import build_report

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_league.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("inspect_league", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_has_all_sections(league, config, draft_class):
    text = build_report([league], config, draft_class[:200])
    for heading in (
        "年齢の分布",
        "球団別の平均年齢",
        "型ごとの人数の割合",
        "全選手",
        "一軍相当",
        "年齢層別の平均",
        "調整案",
        "潜在能力と現在の能力の差",
        "新人(ドラフト候補)",
    ):
        assert heading in text, heading
    assert "未確認" in text  # 一軍登録人数が未確認であることを明記する


def test_script_runs(capsys):
    assert _load_script().main(["--seed", "3", "--leagues", "1", "--draft", "50"]) == 0
    assert "生成したリーグの分布" in capsys.readouterr().out


def test_script_reports_config_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert _load_script().main(["--config", str(bad), "--draft", "0"]) == 1
    assert "問題があります" in capsys.readouterr().err
