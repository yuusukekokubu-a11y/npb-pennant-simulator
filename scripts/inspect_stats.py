"""1シーズンを回して、成績のランキングと整合チェックを出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_stats.py            # シード1のシーズン
    python scripts/inspect_stats.py --seed 3 --top 5

出力は Markdown の表を含む文章。真の能力(隠し情報を含む)と成績の関係も出すが、確認用で、画面には出さない。
"""

from __future__ import annotations

import argparse
import sys
import time

from pennant import ConfigError, generate_league, load_generation_config, load_name_parts
from pennant.metrics import load_metrics_config
from pennant.records import season_records
from pennant.season import Season
from pennant.stats_report import build_stats_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1シーズンの成績のランキングと整合チェックを表示する")
    parser.add_argument("--seed", type=int, default=1, help="シード(乱数の出方を決める初期値)")
    parser.add_argument("--top", type=int, default=10, help="ランキングの人数")
    parser.add_argument("--metrics", help="指標の定義 JSON(省略時は既定)")
    args = parser.parse_args(argv)
    try:
        config = load_metrics_config(args.metrics)
        league = generate_league(args.seed, load_generation_config(), load_name_parts())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    result = Season(league, args.seed).play_to_end()
    results = [p.result for p in result.games]
    t0 = time.perf_counter()
    rec = season_records(results)
    seconds = time.perf_counter() - t0
    print(build_stats_report(rec, results, league, config, result, args.top))
    print(f"- 集計にかかった時間: {seconds:.2f} 秒({len(results)} 試合)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
