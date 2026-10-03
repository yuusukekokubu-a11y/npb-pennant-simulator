"""指標の基準値の既定値(data/baselines.json の defaults)を、試運転で求め直す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/make_baselines.py                 # リーグ 1 の試運転で求めた値を表示し、今の既定値と並べる
    python scripts/make_baselines.py --leagues 3     # リーグ 1〜3 の平均
    python scripts/make_baselines.py --write         # data/baselines.json の defaults を書き換える

既定値は「既定値を使う(速い)」の新規開始、基準値のない古いセーブデータ、得点の球場補正の換算(D-154)で使う。
選手の作り方が変わったとき(第3弾①、D-190 の事前運転と校正)に求め直す。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from fractions import Fraction
from pathlib import Path

from pennant.baselines import load_baseline_settings, trial_baselines
from pennant.config import load_generation_config, load_name_parts
from pennant.newgame import new_league

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "src" / "pennant" / "data" / "baselines.json"
DIGITS = {"lg_era": 2, "fip_constant": 2}


def main(argv=None):
    parser = argparse.ArgumentParser(description="基準値の既定値を試運転で求め直す")
    parser.add_argument("--leagues", type=int, default=1, help="使うリーグの数(シード 1 から順)。平均を取る")
    parser.add_argument("--write", action="store_true", help="data/baselines.json の defaults を書き換える")
    args = parser.parse_args(argv)
    settings = load_baseline_settings()
    config, parts = load_generation_config(), load_name_parts()
    keys = list(settings.data["defaults"])
    found: dict[str, list[Fraction]] = {k: [] for k in keys}
    for seed in range(1, args.leagues + 1):
        league = new_league(seed, None, config, parts, prerun=True)
        base = trial_baselines(league, settings)
        for k in keys:
            found[k].append(base.values[k])
        print(f"  ... リーグ {seed}", file=sys.stderr)
    new = {k: f"{statistics.fmean(float(v) for v in found[k]):.{DIGITS.get(k, 3)}f}" for k in keys}
    print("| 基準値 | 今の既定値 | 求め直した値 |")
    print("|---|---|---|")
    for k in keys:
        print(f"| {k} | {settings.data['defaults'][k]} | {new[k]} |")
    if args.write:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        raw["defaults"] = new
        SETTINGS_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"- {SETTINGS_PATH} に書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
