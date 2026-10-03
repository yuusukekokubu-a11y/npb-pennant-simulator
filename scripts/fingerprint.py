"""結果の指紋(再現性の確認用)を表示する開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/fingerprint.py           # 指紋を表示し、テストに固定した正しい値と比べる
    python scripts/fingerprint.py --json    # 指紋を JSON で表示する(正しい値を作り直すとき)

ブラウザのテスト用ページ(web/)の「測定」も、同じ関数(pennant.fingerprint)で同じ文章を表示する。
スマホと PC で、表示された指紋が同じなら、同じシードで同じ結果になっている。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from pennant.fingerprint import fingerprints, format_fingerprints

EXPECTED = Path(__file__).resolve().parents[1] / "tests" / "data" / "fingerprints.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="結果の指紋を表示する")
    parser.add_argument("--json", action="store_true", help="JSON で表示する")
    args = parser.parse_args(argv)
    fp = fingerprints()
    if args.json:
        print(json.dumps(fp, ensure_ascii=False, indent=2))
        return 0
    print(f"Python {platform.python_version()}({platform.system()} {platform.machine()})")
    print(format_fingerprints(fp))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    same = all(fp[k] == expected[k] for k in ("fingerprint_version", "league", "game", "days", "season", "records", "save", "baselines", "metrics2", "parks", "park_estimates", "run_values"))
    print("正しい値(tests/data/fingerprints.json)との比較:" + ("○ 一致" if same else "× 不一致"))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
