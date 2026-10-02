"""実在の名前が含まれていないかの確認(受け入れ条件10)。

実在名の禁止リストはリポジトリに置かない(D-002、D-003)。
そのため、手元に自分用のリストを置いた人だけが確認できる形にしている:
  環境変数 PENNANT_BANNED_NAMES_FILE に、1 行 1 語の UTF-8 テキストファイルの場所を入れる。
  設定がなければ、このテストは「スキップ(実行しない)」になる。
"""

import os
from pathlib import Path

import pytest

from pennant import generate_league

ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("src", "tests", "scripts")
SCANNED_SUFFIXES = (".py", ".json", ".toml", ".md")


def _banned_words() -> list[str]:
    path = os.environ.get("PENNANT_BANNED_NAMES_FILE")
    if not path:
        pytest.skip("PENNANT_BANNED_NAMES_FILE が設定されていないため実行しません")
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [w.strip() for w in lines if w.strip() and not w.startswith("#")]


def test_repository_files_have_no_banned_words():
    words = _banned_words()
    hits = []
    for d in SCANNED_DIRS:
        for f in (ROOT / d).rglob("*"):
            if f.is_file() and f.suffix in SCANNED_SUFFIXES:
                text = f.read_text(encoding="utf-8")
                hits += [f"{f.relative_to(ROOT)}: {w}" for w in words if w in text]
    assert not hits


def test_generated_names_have_no_banned_words(config, names):
    words = set(_banned_words())
    for seed in range(1, 21):
        league = generate_league(seed, config, names)
        for team in league.teams:
            # 球団名・球場名は、禁止語が一部に含まれていても検出する
            assert not [w for w in words if w in team.name or w in team.stadium]
        for p in league.all_players():
            assert p.name.replace(" ", "") not in words and p.name not in words
