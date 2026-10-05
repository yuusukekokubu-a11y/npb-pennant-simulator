"""GitHub のテストを、同時に走らせるジョブに分ける(D-285)。

使い方(GitHub のテストから使う。手元でも同じ分け方で回せる):
    python scripts/ci_groups.py fast fingerprint   # → pytest に渡す引数を 1 行に出す
    pytest $(python scripts/ci_groups.py fast b)

分け方:
  fast(速いテスト。版ごとに 3 つのジョブ)
    fingerprint  版をまたいだ指紋の一致の確認(tests/test_fingerprint.py。D-281)
    a            時間のかかるファイルをまとめたもの
    b            残り全部(新しく足したテストのファイルは、ここに入る)
  slow(重いテスト。3.12 で 3 つのジョブ)
    1・2         指紋を作り直すテストと、時間のかかるファイル
    3            残り全部(新しく足した重いテストは、ここに入る)
「残り全部」のジョブがあるので、どのジョブにも入らないテストは出ない。
時間のかたよりが大きくなったら、下の一覧を直して、ジョブの時間をそろえる(報告に時間を書く)。
"""

from __future__ import annotations

import sys

REST = None  # 同じ種類のほかのジョブに入らない、残り全部

GROUPS: dict[str, dict[str, list[str] | None]] = {
    "fast": {
        "fingerprint": ["tests/test_fingerprint.py"],
        "a": ["tests/test_offseason.py", "tests/test_savegame.py", "tests/test_parks.py", "tests/test_draft.py"],
        "b": REST,
    },
    "slow": {
        "1": [
            "tests/test_fingerprint.py::test_same_in_a_fresh_process_with_other_hash_seed",
            "tests/test_fingerprint.py::test_without_parks_matches_the_values_before_parks",
            "tests/test_negotiation.py",
        ],
        # 指紋を 1 回だけ作って、スクリプトの表示とページの表示の確認で使い回す(tests/conftest.py の current_fingerprints)
        "2": ["tests/test_fingerprint.py::test_script_reports_match", "tests/test_web.py", "tests/test_fa.py", "tests/test_baselines.py"],
        "3": REST,
    },
}


def pytest_args(kind: str, name: str) -> list[str]:
    """そのジョブで pytest に渡す引数。"""
    groups = GROUPS[kind]
    targets = groups[name]
    args = ["-m", "slow"] if kind == "slow" else []
    if targets is not REST:
        return args + list(targets)
    for other, items in groups.items():
        if other == name or items is REST:
            continue
        for item in items:
            args.append(f"--deselect={item}" if "::" in item else f"--ignore={item}")
    return args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in GROUPS or argv[1] not in GROUPS[argv[0]]:
        names = "、".join(f"{k}({' / '.join(v)})" for k, v in GROUPS.items())
        print(f"使い方: python scripts/ci_groups.py 種類 ジョブ  (種類とジョブ: {names})", file=sys.stderr)
        return 2
    print(" ".join(pytest_args(argv[0], argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
