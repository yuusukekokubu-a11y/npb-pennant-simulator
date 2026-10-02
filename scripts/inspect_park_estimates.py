"""球場補正の推定が、シーズン数とともに真の倍率にどれだけ近づくかを表で出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_park_estimates.py                 # リーグ 1 を20シーズン回し、1・2・3・5・10・20 シーズン時点の推定を比べる
    python scripts/inspect_park_estimates.py --seasons 5     # シーズン数を変える(速い確認用)
    python scripts/inspect_park_estimates.py --no-home-check # ホームの有利を切った比較を省く

出力は Markdown の表。主な見方は、推定の誤差(二乗平均平方根:推定と真の値の差を二乗して平均し、平方根を
取ったもの。小さいほど近い)を、「全部 1.0 と推定した場合」の誤差(真の値のばらつきそのもの)と比べること(D-150)。
推定の誤差が 1.0 の誤差より小さければ、推定を使う意味がある。相関(1 に近いほど並び順を言い当てている)は参考値。
得点の「真の値」は、真の倍率から求めた「1打席あたりの得点の出やすさ」(parks.expected_run_factors)。
得点の推定は、本塁打と BABIP の推定から組み立てた値(D-147)。「得点(直接)」は、本拠地 ÷ アウェイの生の比を
リーグの平均でそろえただけの検証用の値(縮めていない。補正には使わない)。
"""

from __future__ import annotations

import argparse
import copy
import sys
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
    runs = expected_run_factors(league, model)
    return {t.id: {"home_run": t.park.home_run / 1000, "babip": t.park.babip / 1000, "runs": runs[t.id]} for t in league.teams}


def collect(league_seed, n, settings, pa_config=None):
    snaps = {}

    def on_season(k, est, league):
        if k in CHECKPOINTS:
            snaps[k] = {tid: {key: float(e.estimate[key]) for key in FACTOR_KEYS} for tid, e in est.items()}
            groups = {}
            for t in league.teams:
                groups.setdefault(t.league_index, []).append(t.id)
            for ids in groups.values():  # 得点(直接):生の比をリーグの平均でそろえる(検証用)
                raws = {tid: float(est[tid].raw["runs"] or 1) for tid in ids}
                mean = sum(raws.values()) / len(raws)
                for tid in ids:
                    snaps[k][tid]["runs_direct"] = raws[tid] / mean
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
    keys = list(FACTOR_KEYS) + ["runs_direct"]
    labels = {**FACTOR_LABELS, "runs_direct": "得点(直接)"}
    truth_of = lambda key, i: truth[i]["runs" if key == "runs_direct" else key]
    print("## シーズン数ごとの、推定の誤差と「全部 1.0 と推定した場合」の誤差(D-150)\n")
    rows = []
    ones = {key: rmse([1.0] * len(ids), [truth_of(key, i) for i in ids]) for key in keys}
    for k in sorted(snaps):
        row = [str(k)]
        for key in keys:
            row += [f"{rmse([snaps[k][i][key] for i in ids], [truth_of(key, i) for i in ids]):.3f}", f"{ones[key]:.3f}"]
        rows.append(row)
    print(_table(["シーズン数"] + [f"{labels[k]}:{'推定の誤差' if j == 0 else '全部 1.0 の誤差'}" for k in keys for j in range(2)], rows))
    print("\n## シーズン数ごとの、推定と真の値の相関(参考値)\n")
    rows = []
    for k in sorted(snaps):
        rows.append([str(k)] + [f"{corr([snaps[k][i][key] for i in ids], [truth_of(key, i) for i in ids]):.2f}" for key in keys])
    print(_table(["シーズン数"] + [f"{labels[k]}:相関" for k in keys], rows))
    last = max(snaps)
    print(f"\n## {last} シーズン時点の、球場ごとの推定と真の値\n")
    rows = []
    for t in league.teams:
        s = snaps[last][t.id]
        tr = truth[t.id]
        rows.append([t.stadium, f"{s['runs']:.3f}", f"{s['runs_direct']:.3f}", f"{tr['runs']:.3f}", f"{s['home_run']:.3f}", f"{tr['home_run']:.3f}", f"{s['babip']:.3f}", f"{tr['babip']:.3f}"])
    print(_table(["球場", "得点:推定", "得点:直接", "得点:真", "本塁打:推定", "本塁打:真", "BABIP:推定", "BABIP:真"], rows))

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
