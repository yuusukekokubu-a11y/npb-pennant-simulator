"""生成したリーグの分布を確認する、開発者向けのスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_league.py                 # シード 1 でリーグを 1 つ作って表示
    python scripts/inspect_league.py --leagues 20    # シード 1〜20 で 20 回作り、まとめて集計
    python scripts/inspect_league.py --config my.json --seed 7

出力は Markdown の表を含む文章。ファイルに保存したいときは「> report.md」を付ける。
"""

from __future__ import annotations

import argparse
import sys

from pennant import ConfigError, generate_draft_class, generate_league, load_generation_config, load_name_parts
from pennant.stats import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成したリーグの分布を表で表示する")
    parser.add_argument("--seed", type=int, default=1, help="最初のシード(乱数の出方を決める初期値)")
    parser.add_argument("--leagues", type=int, default=1, help="何回生成してまとめるか(多いほど数字が安定する)")
    parser.add_argument("--draft", type=int, default=1000, help="確認用に作る新人(ドラフト候補)の人数")
    parser.add_argument("--config", help="生成設定の JSON ファイル(省略時は既定)")
    parser.add_argument("--names", help="名前の部品の JSON ファイル(省略時は既定)")
    args = parser.parse_args(argv)

    try:
        config = load_generation_config(args.config)
        names = load_name_parts(args.names)
        leagues = [generate_league(args.seed + i, config, names) for i in range(args.leagues)]
        draft = generate_draft_class(args.seed, args.draft, config, names) if args.draft > 0 else None
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(build_report(leagues, config, draft))
    return 0


if __name__ == "__main__":
    sys.exit(main())
