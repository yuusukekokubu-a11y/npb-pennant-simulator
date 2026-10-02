"""打席の計算を多数回まわして、結果の割合を表で出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_plate_appearance.py               # 既定:リーグ5回分の一軍相当で 100,000 打席
    python scripts/inspect_plate_appearance.py --pa 50000 --seed 3
    python scripts/inspect_plate_appearance.py --config my_pa.json

出力は Markdown の表を含む文章。ファイルに保存したいときは「> report.md」を付ける。
"""

from __future__ import annotations

import argparse
import sys

from pennant import ConfigError, generate_league, load_generation_config, load_name_parts
from pennant.pa_config import load_pa_config
from pennant.pa_stats import FirstTeamPool, build_pa_report
from pennant.plate_appearance import OddsRatioModel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打席の計算の結果を表で表示する")
    parser.add_argument("--seed", type=int, default=1, help="シード(乱数の出方を決める初期値)")
    parser.add_argument("--leagues", type=int, default=5, help="対戦に使うリーグの数(生成回数)")
    parser.add_argument("--pa", type=int, default=100_000, help="回す打席数")
    parser.add_argument("--config", help="打席の計算の設定 JSON(省略時は既定)")
    parser.add_argument("--generation", help="選手生成の設定 JSON(省略時は既定)")
    args = parser.parse_args(argv)

    try:
        gen_config = load_generation_config(args.generation)
        names = load_name_parts()
        model = OddsRatioModel(load_pa_config(args.config))
        leagues = [generate_league(args.seed + i, gen_config, names) for i in range(args.leagues)]
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(build_pa_report(model, FirstTeamPool(leagues, gen_config), args.pa, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
