"""ポジション別の「打撃 + 走塁」を、シードを変えた多数の世界で測り直す開発者向けスクリプト(D-174)。

使い方:
    python scripts/inspect_position_runs.py                      # 世界 12 × 8 シーズン
    python scripts/inspect_position_runs.py --worlds 4 --seasons 3

出力は Markdown の表:ポジション別(守備アウト数が最多の位置。指名打者を含む)に、
「打撃 + 走塁」の平均(点/125試合)、標準誤差、延べ選手数、実際の選手数、
出場量(打席数)の重みづけで合計が 0 になる「打ち消す補正」(D-175)。報告だけで、値は変えない。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from fractions import Fraction

from pennant.abilities import POSITION_LABELS
from pennant.baselines import load_baseline_settings, season_baselines
from pennant.parkfactors import load_park_settings, run_seasons
from pennant.records import season_records
from pennant.runvalues import player_park_factors, season_player_runs
from pennant.war import POSITIONS_WITH_DH, dh_plate_appearances, load_war_settings


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def primary_position(r, dh_pa_count, pa_per_slot):
    best = max(r.outs_by_position.items(), key=lambda kv: kv[1], default=(None, 0))
    full_outs = 125 * 27
    dh_share = dh_pa_count / pa_per_slot if pa_per_slot else 0
    if dh_share > (best[1] / full_outs if full_outs else 0):
        return "DH"
    return best[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description="ポジション別の打撃 + 走塁を多数の世界で測る")
    parser.add_argument("--worlds", type=int, default=12, help="世界(リーグのシード)の数。シード 1 から順に")
    parser.add_argument("--seasons", type=int, default=8, help="各世界のシーズン数")
    parser.add_argument("--min-pa", type=int, default=300, help="対象にする1シーズンの打席数の下限")
    args = parser.parse_args(argv)
    settings = load_baseline_settings()
    war_settings = load_war_settings()
    samples = {p: [] for p in POSITIONS_WITH_DH}  # (値, 打席数, 選手の鍵)
    games_hint = {"games": 125}

    for seed in range(1, args.worlds + 1):
        def on_results(k, results, league, estimates, seed=seed):
            base = season_baselines(results, settings, settings.default_baselines())
            rec = season_records(results)
            pfs = player_park_factors(results, estimates)
            runs = season_player_runs(results, base, lambda pid: pfs.get(pid, Fraction(1)), rec)
            dh = dh_plate_appearances(results)
            games = max(c["G"] for c in rec.teams.values())
            games_hint["games"] = games
            total_pa = sum(c["PA"] for c in rec.batters.values())
            pa_per_slot = Fraction(total_pa, 9 * len(rec.teams))
            for pid, r in runs.items():
                if pid not in rec.batters or r.plate_appearances < args.min_pa:
                    continue
                pos = primary_position(r, dh.get(pid, 0), pa_per_slot)
                if pos not in samples:
                    continue
                c = rec.batters[pid]
                per_125 = float(r.batting + r.baserunning) / r.plate_appearances * c["PA"] / c["G"] * games
                samples[pos].append((per_125, r.plate_appearances, (seed, pid)))
            print(f"  ... 世界 {seed} / {args.worlds}、シーズン {k} / {args.seasons}", file=sys.stderr)

        run_seasons(seed, args.seasons, load_park_settings(), on_results=on_results)

    print(f"# ポジション別の「打撃 + 走塁」の測り直し(世界 {args.worlds} × {args.seasons} シーズン、1シーズン打席 {args.min_pa} 以上)\n")
    means, weights = {}, {}
    rows = []
    for pos in POSITIONS_WITH_DH:
        vals = [v for v, _, _ in samples[pos]]
        if not vals:
            continue
        m = statistics.fmean(vals)
        se = statistics.pstdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else 0.0
        means[pos] = m
        weights[pos] = sum(pa for _, pa, _ in samples[pos])
        rows.append([POSITION_LABELS.get(pos, pos), f"{m:+.1f}", f"{se:.1f}", str(len(vals)), str(len({k for _, _, k in samples[pos]}))])
    total_w = sum(weights.values())
    weighted_mean = sum(means[p] * weights[p] for p in means) / total_w
    print(_table(["ポジション", "打撃 + 走塁の平均(点/125試合)", "標準誤差", "延べ選手数", "実際の選手数"], rows))
    print(f"\n出場量(打席数)で重みづけした全体の平均: {weighted_mean:+.2f} 点/125試合\n")
    print("## 打ち消す補正(= −(平均 − 重みづけした全体の平均)。出場量の重みづけで合計 0。D-175)と仮置きの値\n")
    rows = []
    for pos in POSITIONS_WITH_DH:
        if pos in means:
            rows.append([POSITION_LABELS.get(pos, pos), f"{-(means[pos] - weighted_mean):+.1f}", f"{float(war_settings.position_runs(pos)):+.0f}", f"{100 * weights[pos] / total_w:.1f}%"])
    print(_table(["ポジション", "打ち消す補正(点/125試合)", "仮置きのポジション補正", "出場量の割合"], rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
