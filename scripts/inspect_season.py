"""1シーズンを回して、順位表と集計表を出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_season.py                 # シード1のシーズンを1つ回して、順位表と集計を出す
    python scripts/inspect_season.py --seed 3
    python scripts/inspect_season.py --seeds 20      # シード1〜20のシーズンを回して、シードごとの要約を出す(校正用)

出力は Markdown の表を含む文章。ファイルに保存したいときは「> report.md」を付ける。
"""

from __future__ import annotations

import argparse
import statistics
import sys

from pennant import ConfigError, load_generation_config, load_name_parts
from pennant.newgame import new_league
from pennant.game_config import load_game_config
from pennant.pa_config import load_pa_config
from pennant.plate_appearance import OddsRatioModel
from pennant.season import Season
from pennant.season_config import load_season_config
from pennant.season_stats import build_season_report, season_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1シーズンの順位表と集計を表示する")
    parser.add_argument("--seed", type=int, default=1, help="シード(乱数の出方を決める初期値)")
    parser.add_argument("--seeds", type=int, help="シード1〜N のシーズンを回して、要約だけを出す")
    parser.add_argument("--season-config", help="シーズンの設定 JSON(省略時は既定)")
    parser.add_argument("--game-config", help="1試合の進行の設定 JSON(省略時は既定)")
    parser.add_argument("--pa-config", help="打席の計算の設定 JSON(省略時は既定)")
    args = parser.parse_args(argv)
    try:
        gen, names = load_generation_config(), load_name_parts()
        season_config = load_season_config(args.season_config)
        game_config = load_game_config(args.game_config)
        model = OddsRatioModel(load_pa_config(args.pa_config))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    def run(seed: int):
        season = Season(new_league(seed, None, gen, names, prerun=True), seed, season_config, game_config, model)  # 新規開始と同じ(事前運転と校正を含む。D-190)
        return season, season.play_to_end()

    if not args.seeds:
        season, result = run(args.seed)
        print(build_season_report(season, result))
        return 0

    rows, all_sd, home, runs, skips, ties = [], [], [], [], [], []
    for seed in range(1, args.seeds + 1):
        _, result = run(seed)
        s = season_summary(result)
        sds = s["チーム勝率の標準偏差(各リーグ)"]
        all_sd += sds
        home.append(s["ホームの勝率(引き分け除く)"])
        runs.append(s["得点(1チーム1試合)"])
        skips.append(s["先発を飛ばした割合"])
        ties.append(s["引き分け率"])
        rows.append(
            f"| {seed} | {100 * home[-1]:.1f}% | {runs[-1]:.2f} | {100 * ties[-1]:.1f}% | "
            + " / ".join(f"{x:.3f}" for x in sds)
            + f" | {100 * skips[-1]:.1f}% |"
        )
    print(f"# シーズンの要約(シード1〜{args.seeds})\n")
    print("| シード | ホームの勝率 | 得点(1チーム1試合) | 引き分け率 | チーム勝率の標準偏差(第一 / 第二) | 先発を飛ばした割合 |")
    print("|---|---|---|---|---|---|")
    print("\n".join(rows))
    print(
        f"\n- 全体:ホームの勝率 {100 * statistics.mean(home):.1f}%、得点 {statistics.mean(runs):.2f}、"
        f"引き分け率 {100 * statistics.mean(ties):.1f}%、先発を飛ばした割合 {100 * statistics.mean(skips):.1f}%"
    )
    print(
        f"- チーム勝率の標準偏差:平均 {statistics.mean(all_sd):.3f}、最小 {min(all_sd):.3f}、最大 {max(all_sd):.3f}"
        f"(目標 0.04〜0.07 に入ったリーグ {sum(0.04 <= x <= 0.07 for x in all_sd)} / {len(all_sd)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
