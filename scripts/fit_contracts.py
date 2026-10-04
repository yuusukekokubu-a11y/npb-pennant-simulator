"""スカウト評価(総合の推定値と天井)と、翌シーズンの WAR の関係を求める開発者向けスクリプト(F3-2a。D-232。操作画面ではない)。

使い方:
    python scripts/fit_contracts.py                 # リーグ 1〜3 を 2 シーズンずつ回して、役割ごとの直線と天井の加点を出す
    python scripts/fit_contracts.py --write         # 求めた値を data/contracts.json の scouting_war に書く

シーズンの始めに、所属球団の評価(契約の乱数系列)で全選手の総合の推定値と天井を求め、そのシーズンの WAR(出場しなければ 0)に
直線(切片 + 傾き × (推定値 − 50))を当てはめる。天井の加点は、段階ごとの残差の平均。真の能力は使わない。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from pennant import api
from pennant.draft import contract_scout
from pennant.scouting import GRADES, ceiling_cuts
from pennant.season import derive_seed

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
    parser = argparse.ArgumentParser(description="評価と WAR の関係を求める")
    parser.add_argument("--leagues", type=int, default=3)
    parser.add_argument("--seasons", type=int, default=2)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    samples = {"batter": [], "pitcher": []}  # (推定値 − 50, 天井, WAR)
    for seed in range(1, args.leagues + 1):
        g = api.Game.new(seed, [None] * 12, None, season_seed=seed, baselines="default")
        for k in range(args.seasons):
            state = g.state
            cuts = ceiling_cuts(state.league.all_players(), {"S": 0.05, "A": 0.15, "B": 0.30, "C": 0.30, "D": 0.20})
            scout = contract_scout(derive_seed(state.season.seed, "fit"), state.scout_sd_of, cuts)
            est = {p.id: (p.role, scout(p, t.id)) for t in state.league.teams for p in t.players}
            g.advance(state.season.total_days)
            lines = g.war_lines()
            for pid, (role, (overall, ceiling)) in est.items():
                line = lines.get(pid)
                war = 0.0 if line is None else float(line.war if role == "batter" else line.war_ra)
                samples[role].append((overall - 50.0, ceiling, war))
            print(f"  ... リーグ {seed} シーズン {k + 1}:{len(lines)} 人に WAR", file=sys.stderr)
            if k + 1 < args.seasons:
                g.year_end()
    print("# 評価 → 見込みの WAR(F3-2a)\n")
    print("| 役割 | 人数 | 切片(推定値 50 のとき) | 傾き(1 点あたり) | 相関 | 天井の加点 S / A / B / C / D |")
    print("|---|---|---|---|---|---|")
    result = {}
    for role, rows in samples.items():
        xs = [r[0] for r in rows]
        ys = [r[2] for r in rows]
        a, b = fit(xs, ys)
        corr = statistics.correlation(xs, ys)
        resid = {g_: [] for g_ in GRADES}
        for x, grade, y in rows:
            resid[grade].append(y - (a + b * x))
        bonus = {g_: round(statistics.fmean(resid[g_]), 2) if resid[g_] else 0.0 for g_ in GRADES}
        result[role] = {"intercept": round(a, 3), "slope": round(b, 4), "ceiling_bonus": bonus}
        print(f"| {role} | {len(rows)} | {a:.3f} | {b:.4f} | {corr:.2f} | {' / '.join(f'{bonus[g_]:+.2f}' for g_ in GRADES)} |")
        total_pred = sum(max(0.0, a + b * x + bonus[grade]) for x, grade, _ in rows) / args.leagues / args.seasons
        total_act = sum(ys) / args.leagues / args.seasons
        print(f"- {role}:1 シーズンあたりの見込みの合計(0 未満は 0){total_pred:.1f} WAR、実際の合計 {total_act:.1f} WAR")
    if args.write:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        raw["scouting_war"] = result
        SETTINGS_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"- {SETTINGS_PATH} の scouting_war に書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
