"""PR で重いテストを回すかを決める(GitHub のテストから使う。D-280)。

標準入力に、PR で変わったファイルの一覧(1 行に 1 つ。git diff --name-only の出力)を受け取り、
計算本体(結果に関わるファイル)が 1 つでも変わっていれば slow=true、そうでなければ slow=false を出す
(GitHub Actions の出力 $GITHUB_OUTPUT にそのまま足せる形)。

計算本体の範囲:
  src/pennant/            計算本体のコードと設定のデータ(data/*.json)
  tests/                  テストのコードと、固定した指紋などのデータ
  pyproject.toml          依存とテストの設定
  scripts/fingerprint.py  指紋の確認のスクリプト(テストから使う)
  .github/workflows/test.yml  テストの回し方そのもの
画面(web/)、docs/、ほかの開発者向けスクリプトだけの変更では、重いテストは回さない。
"""

from __future__ import annotations

import re
import sys

PATTERNS = (
    r"^src/pennant/",
    r"^tests/",
    r"^pyproject\.toml$",
    r"^scripts/fingerprint\.py$",
    r"^\.github/workflows/test\.yml$",
)


def needs_slow(paths) -> bool:
    return any(re.match(p, path.strip()) for path in paths for p in PATTERNS)


def main(argv=None) -> int:
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(f"slow={'true' if needs_slow(paths) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
