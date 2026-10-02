"""試合を多数回まわして集計表を出し、1試合の流れを文章で出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_game.py                    # 既定:2,000 試合の集計 + 1試合の流れ
    python scripts/inspect_game.py --games 5000 --seed 3
    python scripts/inspect_game.py --games 0          # 1試合の流れだけ
    python scripts/inspect_game.py --games 0 --sample-seed 5  # 別の試合の流れ

出力は Markdown の表を含む文章。ファイルに保存したいときは「> report.md」を付ける。
"""

from __future__ import annotations

import argparse
import random
import sys

from pennant import ConfigError, generate_league, load_generation_config, load_name_parts
from pennant.game import simulate_game
from pennant.game_config import load_game_config
from pennant.game_stats import build_game_report, narrate, play_games
from pennant.manager import SimpleManager
from pennant.pa_config import load_pa_config
from pennant.plate_appearance import OddsRatioModel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="試合の集計と、1試合の流れを表示する")
    parser.add_argument("--seed", type=int, default=1, help="シード(乱数の出方を決める初期値)")
    parser.add_argument("--games", type=int, default=2000, help="集計する試合数")
    parser.add_argument("--no-sample", action="store_true", help="1試合の流れを出さない")
    parser.add_argument("--sample-seed", type=int, help="1試合の流れに使うシード(省略時は --seed と同じ)")
    parser.add_argument("--config", help="1試合の進行の設定 JSON(省略時は既定)")
    parser.add_argument("--pa-config", help="打席の計算の設定 JSON(省略時は既定)")
    args = parser.parse_args(argv)

    try:
        gen_config = load_generation_config()
        names = load_name_parts()
        config = load_game_config(args.config)
        model = OddsRatioModel(load_pa_config(args.pa_config))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    manager = SimpleManager(config)
    parts = []

    if not args.no_sample:
        league = generate_league(args.seed, gen_config, names)
        players = {p.id: p for p in league.all_players()}
        team_names = {t.id: t.name for t in league.teams}
        rng = random.Random(args.seed if args.sample_seed is None else args.sample_seed)
        home, away = league.teams[0], league.teams[1]
        hs, _ = manager.prepare(home, rng, 0)
        aw, _ = manager.prepare(away, rng, 0)
        result = simulate_game(hs, aw, rng, model=model, config=config, manager=manager)
        parts.append(narrate(result, players, team_names))

    if args.games > 0:
        league = generate_league(args.seed, gen_config, names)
        results = play_games(league, args.games, args.seed, config, model, manager)
        parts.append(build_game_report(results, league))
    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
