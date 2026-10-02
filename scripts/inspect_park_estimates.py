"""球場補正の推定が、シーズン数とともに真の倍率にどれだけ近づくかを表で出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_park_estimates.py                 # リーグ 1 を20シーズン回し、1・2・3・5・10・20 シーズン時点の推定を比べる
    python scripts/inspect_park_estimates.py --seasons 5     # シーズン数を変える(速い確認用)
    python scripts/inspect_park_estimates.py --no-home-check # ホームの有利を切った比較を省く

出力は Markdown の表。相関(1 に近いほど、推定が真の値の並び順を言い当てている)と、
誤差(二乗平均平方根:推定と真の値の差を二乗して平均し、平方根を取ったもの。小さいほど近い)。
得点ベースの「真の値」は、真の倍率から求めた「1打席あたりの得点の出やすさ」の目安(parks.expected_run_factors)。
"""

from __future__ import annotations

import argparse
import copy
import sys
from pennant.baselines import load_baseline_settings
from pennant.pa_config import load_pa_config, validate_pa_config
from pennant.parkfactors import FACTOR_KEYS, FACTOR_LABELS, load_park_settings, run_seasons
from pennant.parks import expected_run_factors
from pennant.plate_appearance import OddsRatioModel

CHECKPOINTS = (1, 2, 3, 5, 10, 20)


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0


def rmse(xs, ys):
    return (sum((x - y) ** 2 for x, y in zip(xs, ys)) / len(xs)) ** 0.5


def true_factors(league, model):
    """真の倍率(本塁打・BABIP)と、得点ベースの真の値の目安(各リーグの平均で割ったもの)。"""
    runs = expected_run_factors(league, model, load_baseline_settings().default_baselines().values)
    return {t.id: {"home_run": t.park.home_run / 1000, "babip": t.park.babip / 1000, "runs": runs[t.id]} for t in league.teams}


def collect(league_seed, n, settings, pa_config=None):
    snaps = {}

    def on_season(k, est, league):
        if k in CHECKPOINTS:
            snaps[k] = {tid: {key: float(e.estimate[key]) for key in FACTOR_KEYS} for tid, e in est.items()}
        print(f"  ... シーズン {k} / {n}", file=sys.stderr)

    history, est, league = run_seasons(league_seed, n, settings, on_season, pa_config)
    return snaps, league


def main(argv=None):
    parser = argparse.ArgumentParser(description="球場補正の推定と真の倍率を比べる")
    parser.add_argument("--seed", type=int, default=1, help="リーグのシード")
    parser.add_argument("--seasons", type=int, default=20, help="回すシーズンの数")
    parser.add_argument("--no-home-check", action="store_true", help="ホームの有利を切った比較を省く")
    args = parser.parse_args(argv)
    settings = load_park_settings()
    model = OddsRatioModel()

    print(f"# 球場補正の推定と真の倍率(リーグ {args.seed}、{args.seasons} シーズン)\n")
    snaps, league = collect(args.seed, args.seasons, settings)
    truth = true_factors(league, model)
    ids = [t.id for t in league.teams]
    rows = []
    for k in sorted(snaps):
        row = [str(k)]
        for key in FACTOR_KEYS:
            xs = [snaps[k][i][key] for i in ids]
            ys = [truth[i][key] for i in ids]
            row += [f"{corr(xs, ys):.2f}", f"{rmse(xs, ys):.3f}"]
        rows.append(row)
    print("## シーズン数ごとの、推定と真の値の関係\n")
    print(_table(["シーズン数"] + [f"{FACTOR_LABELS[k]}:相関" if j == 0 else f"{FACTOR_LABELS[k]}:誤差" for k in FACTOR_KEYS for j in range(2)], rows))
    last = max(snaps)
    print(f"\n## {last} シーズン時点の、球場ごとの推定と真の値\n")
    rows = []
    for t in league.teams:
        s = snaps[last][t.id]
        tr = truth[t.id]
        rows.append([t.stadium, f"{s['runs']:.3f}", f"{tr['runs']:.3f}", f"{s['home_run']:.3f}", f"{tr['home_run']:.3f}", f"{s['babip']:.3f}", f"{tr['babip']:.3f}"])
    print(_table(["球場", "得点:推定", "得点:真(目安)", "本塁打:推定", "本塁打:真", "BABIP:推定", "BABIP:真"], rows))

    if args.no_home_check:
        return 0
    print(f"\n## ホームの有利を切ったときとの比較({last} シーズン時点。D-093)\n", flush=True)
    data = copy.deepcopy(load_pa_config().data)
    data["home_advantage"] = {k: 1.0 for k in data.get("home_advantage", {})}
    off_snaps, league_off = collect(args.seed, args.seasons, settings, validate_pa_config(data, "ホームの有利なし"))
    groups = {}
    for t in league.teams:
        groups.setdefault(t.league_index, []).append(t.id)
    rows = []
    for key in FACTOR_KEYS:
        on = [snaps[last][i][key] for i in ids]
        off = [off_snaps[last][i][key] for i in ids]
        means_on = "・".join(f"{sum(snaps[last][i][key] for i in g) / len(g):.3f}" for g in groups.values())
        means_off = "・".join(f"{sum(off_snaps[last][i][key] for i in g) / len(g):.3f}" for g in groups.values())
        rows.append([FACTOR_LABELS[key], means_on, means_off, f"{max(abs(a - b) for a, b in zip(on, off)):.3f}", f"{corr(on, off):.2f}", f"{corr(off, [truth[i][key] for i in ids]):.2f}"])
    print(_table(["項目", "リーグの平均(有利あり)", "リーグの平均(有利なし)", "球場ごとの差の最大", "あり・なしの相関", "なし:真との相関"], rows))
    print("\n- ホームの有利は両チームの本拠地の試合に同じだけ効き、本拠地とアウェイの比では打ち消されるはず。差が小さければ、推定が偏っていない。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
