"""校正の定数(D-197)を求める開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/calibrate.py                 # シード 1〜10 で、事前運転の後の一軍の平均を測り、定数の案を出す
    python scripts/calibrate.py --write         # 求めた定数を data/offseason.json に書き込む
    python scripts/calibrate.py --check         # 今の定数で、新規開始時の一軍の平均(投手・野手別)がどうなるかを確かめる

一軍相当 = 各球団の上位(generation.json の first_team。投手 14・野手 15)の真の総合能力。目標は 50。
定数 = 目標 − (校正なしで事前運転した後の平均)。役割(打者・投手)ごとに求める。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from pennant.abilities import BATTER, PITCHER
from pennant.config import load_generation_config, load_name_parts
from pennant.newgame import new_league
from pennant.offseason import OffseasonSettings, load_offseason_settings
from pennant.stats import first_team, overall

TARGET = 50.0
SETTINGS_PATH = Path(__file__).resolve().parents[1] / "src" / "pennant" / "data" / "offseason.json"


def first_team_means(league, config) -> dict[str, float]:
    players = [p for t in league.teams for p in first_team(t, config)]
    return {role: statistics.fmean(overall(p) for p in players if p.role == role) for role in (BATTER, PITCHER)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="校正の定数を求める")
    parser.add_argument("--seeds", type=int, default=10, help="使うシードの数(1 から順)")
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--write", action="store_true", help="求めた定数を data/offseason.json に書く")
    parser.add_argument("--check", action="store_true", help="今の定数で新規開始したときの平均を確かめる")
    args = parser.parse_args(argv)
    config = load_generation_config()
    parts = load_name_parts()
    settings = load_offseason_settings()
    if args.check:
        rows = []
        for seed in range(args.seed_start, args.seed_start + args.seeds):
            t0 = time.perf_counter()
            league = new_league(seed, None, config, parts, prerun=True, offseason_settings=settings)
            sec = time.perf_counter() - t0
            m = first_team_means(league, config)
            out = sum(1 for p in league.all_players() for item, v in p.ratings.items() if item != "gb_fb" and (v < 20 or v > 80))
            total = sum(len([i for i in p.ratings if i != "gb_fb"]) for p in league.all_players())
            rows.append((seed, m[BATTER], m[PITCHER], sec, out / total))
            print(f"| {seed} | {m[BATTER]:.2f} | {m[PITCHER]:.2f} | {sec:.2f} | {100 * out / total:.2f}% |", flush=True)
        print(f"- 平均:野手 {statistics.fmean(r[1] for r in rows):.2f}、投手 {statistics.fmean(r[2] for r in rows):.2f}。範囲:野手 {min(r[1] for r in rows):.2f}〜{max(r[1] for r in rows):.2f}、投手 {min(r[2] for r in rows):.2f}〜{max(r[2] for r in rows):.2f}。事前運転を含む生成の時間 平均 {statistics.fmean(r[3] for r in rows):.2f} 秒。20〜80 の外の項目 {100 * statistics.fmean(r[4] for r in rows):.2f}%")
        return 0
    # 校正なし(定数 0)で事前運転した後の平均
    data = json.loads(json.dumps(settings.data))
    data["calibration"] = {"batter": 0.0, "pitcher": 0.0}
    zero = OffseasonSettings(data, "(校正なし)")
    means = {BATTER: [], PITCHER: []}
    for seed in range(args.seed_start, args.seed_start + args.seeds):
        league = new_league(seed, None, config, parts, prerun=True, offseason_settings=zero)
        m = first_team_means(league, config)
        for role in means:
            means[role].append(m[role])
        print(f"| {seed} | {m[BATTER]:.2f} | {m[PITCHER]:.2f} |", flush=True)
    shift = {role: round(TARGET - statistics.fmean(v), 2) for role, v in means.items()}
    print(f"- 校正なしの平均:野手 {statistics.fmean(means[BATTER]):.2f}(標準偏差 {statistics.pstdev(means[BATTER]):.2f})、投手 {statistics.fmean(means[PITCHER]):.2f}(標準偏差 {statistics.pstdev(means[PITCHER]):.2f})")
    print(f"- 定数の案:batter {shift[BATTER]:+.2f}、pitcher {shift[PITCHER]:+.2f}(事前運転 {settings.prerun_years} 年)")
    if args.write:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        raw["calibration"] = {"batter": shift[BATTER], "pitcher": shift[PITCHER]}
        SETTINGS_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"- {SETTINGS_PATH} に書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
