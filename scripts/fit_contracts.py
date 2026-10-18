"""見せる能力の平均と、そのシーズンの WAR の関係を求める開発者向けスクリプト(作り直し②。D-356。操作画面ではない)。

使い方:
    python scripts/fit_contracts.py                 # リーグ 1〜3 を 2 シーズンずつ回して、役割ごとの直線を出す
    python scripts/fit_contracts.py --write         # 求めた値を data/contracts.json の ability_war に書く

シーズンの始めに全選手の見せる能力の平均(総合値)を求め、そのシーズンの WAR(出場しなければ 0)に
直線(切片 + 傾き × (総合値 − 50))を当てはめる。見せる能力だけを使い、隠し情報は使わない。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from pennant import api
from pennant.stats import overall

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "src" / "pennant" / "data" / "contracts.json"


def fit(xs, ys):
    """最小二乗の直線(切片, 傾き)。"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def main(argv=None):
    parser = argparse.ArgumentParser(description="見せる能力と WAR の関係を求める")
    parser.add_argument("--leagues", type=int, default=3)
    parser.add_argument("--seasons", type=int, default=2)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    samples = {"batter": [], "pitcher": []}  # (総合値 − 50, WAR)
    for seed in range(1, args.leagues + 1):
        g = api.Game.new(seed, [None] * 12, None, season_seed=seed, baselines="default")
        for k in range(args.seasons):
            state = g.state
            est = {p.id: (p.role, overall(p)) for p in state.league.all_players()}
            g.advance_to_end()
            lines = g.war_lines()
            for pid, (role, value) in est.items():
                line = lines.get(pid)
                war = 0.0 if line is None else float(line.war if role == "batter" else line.war_ra)
                samples[role].append((value - 50.0, war))
            print(f"  ... リーグ {seed} シーズン {k + 1}:{len(lines)} 人に WAR", file=sys.stderr)
            if k + 1 < args.seasons:
                g.year_end()
    print("# 見せる能力 → 見込みの WAR(作り直し②)\n")
    print("| 役割 | 人数 | 切片(総合値 50 のとき) | 傾き(1 点あたり) | 相関 |")
    print("|---|---|---|---|---|")
    result = {}
    for role, rows in samples.items():
        xs = [r[0] for r in rows]
        ys = [r[1] for r in rows]
        a, b = fit(xs, ys)
        corr = statistics.correlation(xs, ys)
        result[role] = {"intercept": round(a, 3), "slope": round(b, 4)}
        print(f"| {role} | {len(rows)} | {a:.3f} | {b:.4f} | {corr:.2f} |")
        total_pred = sum(max(0.0, a + b * x) for x, _ in rows) / args.leagues / args.seasons
        total_act = sum(ys) / args.leagues / args.seasons
        print(f"- {role}:1 シーズンあたりの見込みの合計(0 未満は 0){total_pred:.1f} WAR、実際の合計 {total_act:.1f} WAR")
    if args.write:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        raw.pop("scouting_war", None)
        raw["ability_war"] = result
        SETTINGS_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"- {SETTINGS_PATH} の ability_war に書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
