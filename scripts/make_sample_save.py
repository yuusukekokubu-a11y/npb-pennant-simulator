"""再現性の確認用の見本のセーブデータ(tests/data/sample-save.sav)を作る開発者向けスクリプト(D-103)。

使い方:
    python scripts/make_sample_save.py

架空のリーグとシーズン(どちらもシード2)を3日目まで進めて保存し、続きを8日目まで進めた結果の指紋を
tests/data/sample-save.json に書く。テスト(tests/test_savegame.py)とブラウザのテスト用ページは、
この見本を読み込んで続きを進め、指紋が同じになることを確かめる(保存した版と違う Python の版でも同じになるはず)。
実在の名前は入らない(球団名は架空の初期名のまま)。計算本体を意図して変えたときだけ、作り直す。
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from pennant.fingerprint import _digest, season_record
from pennant.savegame import load_game, save_game, start_game
from pennant.storage import LocalFolderStorage

SAVE_DAY = 3
CONTINUE_TO_DAY = 8
SAVED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
DATA = Path(__file__).resolve().parents[1] / "tests" / "data"


def continuation_digest(data: bytes) -> str:
    """見本を読み込み、CONTINUE_TO_DAY 日目まで進めた結果の指紋。"""
    state = load_game(data)
    state.season.play_days(CONTINUE_TO_DAY - state.season.day)
    return _digest(season_record(state.season.result()))


def main() -> int:
    state = start_game(2, name="見本のセーブデータ")
    state.season.play_days(SAVE_DAY)
    data = save_game(state, SAVED_AT)
    storage = LocalFolderStorage(DATA)
    storage.write("sample-save.sav", data)
    info = {
        "made_with": f"Python {platform.python_version()}",
        "save_day": SAVE_DAY,
        "continue_to_day": CONTINUE_TO_DAY,
        "continuation_digest": continuation_digest(data),
        "bytes": len(data),
    }
    storage.write("sample-save.json", (json.dumps(info, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
