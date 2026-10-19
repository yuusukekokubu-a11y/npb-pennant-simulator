"""新規開始のテンプレート(用意したリーグ。src/pennant/data/templates/)を作り直す開発者向けスクリプト(D-467)。

使い方:
    python scripts/make_template.py

選手の作り方・名前の部品・事前運転(加齢・引退・オフの手続き)・契約・試合の計算(試運転の基準値)・チーム方針の AI・
保存形式のどれかを変えたら、作り直して同じ PR に入れる。重いテスト(tests/test_template.py)が、今のコードで作り直した
ものと同じかを確かめる。中身は架空の名前だけ(球団名は初期名のまま)。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pennant.template import TEMPLATE_ID, build_template, meta_bytes

OUT = Path(__file__).resolve().parents[1] / "src" / "pennant" / "data" / "templates"


def main() -> int:
    t0 = time.perf_counter()
    data, meta = build_template(prerun_progress=lambda k, n: print(f"\r事前運転 {k}/{n} 年", end="", flush=True))
    print()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{TEMPLATE_ID}.sav").write_bytes(data)
    (OUT / f"{TEMPLATE_ID}.json").write_bytes(meta_bytes(meta))
    print(f"{OUT / (TEMPLATE_ID + '.sav')}({len(data):,} バイト)と {TEMPLATE_ID}.json を作りました({time.perf_counter() - t0:.1f} 秒)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
