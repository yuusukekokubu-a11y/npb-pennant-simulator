"""テストの回し方(D-279〜D-281)の確認:重いテストを回す PR の判定と、GitHub のテストの設定。"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _needs_slow():
    spec = importlib.util.spec_from_file_location("ci_needs_slow", ROOT / "scripts" / "ci_needs_slow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.needs_slow


def test_slow_tests_run_only_when_the_calculation_changes():
    needs_slow = _needs_slow()
    # 画面・docs・ほかのスクリプトだけの変更では、重いテストは回さない
    assert not needs_slow(["web/app.js", "web/index.html", "web/bridge.py"])
    assert not needs_slow(["docs/DECISIONS.md", "README.md", "CLAUDE.md", "scripts/check_browser.mjs", "scripts/inspect_fa.py"])
    assert not needs_slow([])
    # 計算本体(コードと設定のデータ)・テスト・設定・指紋のスクリプト・テストの回し方が変わったら回す
    for path in ("src/pennant/api.py", "src/pennant/data/negotiation.json", "tests/test_fa.py", "tests/data/fingerprints.json", "pyproject.toml", "scripts/fingerprint.py", ".github/workflows/test.yml"):
        assert needs_slow(["web/app.js", path]), path


def test_workflow_settings():
    text = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert '"3.10", "3.12", "3.14"' in text  # PR は 3 つの版(指紋の一致の確認を含む。D-281)
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text  # 出し直すと古い実行を止める
    assert "workflow_dispatch:" in text and "pytest -m slow" in text  # 手動で重いテストを回せる
    assert "scripts/ci_needs_slow.py" in text
    for line in text.splitlines():  # 外部の部品は commit の番号で固定する
        if "uses:" in line:
            ref = line.split("@", 1)[1].split()[0]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), line


def test_fingerprint_match_is_a_fast_test():
    """版をまたいだ指紋の一致の確認は、速いテストに残す(D-281)。"""
    text = (ROOT / "tests" / "test_fingerprint.py").read_text(encoding="utf-8")
    head = text.split("def test_fingerprints_match_expected", 1)[0].rstrip().splitlines()
    assert not head[-1].startswith("@pytest.mark.slow") and "pytestmark" not in text
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'addopts = ["-m", "not slow"]' in pyproject and "slow:" in pyproject
