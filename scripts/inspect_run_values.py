"""打撃・走塁・守備の得点(第3弾②)を複数シーズン回して確かめる開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_run_values.py                 # リーグ 1 を10シーズン回す
    python scripts/inspect_run_values.py --seasons 3     # シーズン数を変える(速い確認用)
    python scripts/inspect_run_values.py --seed 2

出力は Markdown の表:
  - シーズンごとのリーグ全体の合計(打撃・走塁・守備。0 付近になるはず)
  - 守備の得点と真の能力(守備範囲・肩・捕球の平均)の相関:1・3・10 シーズンの累計。1シーズンの得点の標準偏差(運のぶれ)
  - 走塁の得点と真の能力(走力・走塁判断の平均)の相関
  - 打撃の得点と wRC+ の相関
  - ポジション別の守備の得点の平均・標準偏差
真の能力は、このスクリプトの中だけで使う(画面には出さない。D-108)。
同じリーグを繰り返し回すので、選手(能力)はシーズンをまたいで同じ。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from fractions import Fraction

from pennant import api
from pennant.abilities import BATTER, FIELDER_POSITIONS, POSITION_LABELS
from pennant.baselines import load_baseline_settings, season_baselines
from pennant.metrics import compute
from pennant.parkfactors import load_park_settings, run_seasons
from pennant.records import season_records
from pennant.runvalues import PlayerRuns, league_totals, player_park_factors, season_player_runs

CHECKPOINTS = (1, 3, 10)
FIELDING_ITEMS = ("range", "arm", "fielding")
RUNNING_ITEMS = ("speed", "baserunning")


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else float("nan")


def main(argv=None):
    parser = argparse.ArgumentParser(description="打撃・走塁・守備の得点を複数シーズンで確かめる")
    parser.add_argument("--seed", type=int, default=1, help="リーグのシード")
    parser.add_argument("--seasons", type=int, default=10, help="回すシーズンの数")
    parser.add_argument("--min-chances", type=int, default=200, help="守備の相関に入れる、1シーズンあたりの守備の機会の下限")
    parser.add_argument("--min-pa", type=int, default=300, help="走塁・打撃の相関に入れる、1シーズンあたりの打席数の下限")
    args = parser.parse_args(argv)
    settings = load_baseline_settings()
    metrics_config = api.metrics_config()

    per_season: list[dict[str, PlayerRuns]] = []
    wrc_by_season: list[dict[str, Fraction]] = []
    totals_rows = []
    league_holder = {}

    def on_results(k, results, league, estimates):
        league_holder["league"] = league
        base = season_baselines(results, settings, settings.default_baselines())
        pfs = player_park_factors(results, estimates)
        pf_of = lambda pid: pfs.get(pid, Fraction(1))  # noqa: E731
        runs = season_player_runs(results, base, pf_of)
        per_season.append(runs)
        rec = season_records(results)
        wrc = {}
        for pid, counts in rec.batters.items():
            values = dict(base.values)
            values["pf"] = pf_of(pid)
            v = compute(metrics_config, "batter", counts, values).get("wrc_plus")
            if v is not None:
                wrc[pid] = v
        wrc_by_season.append(wrc)
        t = league_totals(runs)
        totals_rows.append([str(k)] + [f"{float(t[key]):+.4f}" for key in ("batting", "baserunning", "fielding")])
        print(f"  ... シーズン {k} / {args.seasons}", file=sys.stderr)

    run_seasons(args.seed, args.seasons, load_park_settings(), on_results=on_results)
    league = league_holder["league"]
    players = {p.id: p for p in league.all_players()}
    batters = {pid for pid, p in players.items() if p.role == BATTER}

    print(f"# 打撃・走塁・守備の得点(リーグ {args.seed}、{args.seasons} シーズン)\n")
    print("## リーグ全体の合計(シーズンごと)\n")
    print(_table(["シーズン", "打撃", "走塁", "守備"], totals_rows))
    print("\n- 走塁・守備はちょうど 0。打撃は球場補正(2シーズン目から)の分だけ 0 からずれる。\n")

    # 守備:1シーズンの標準偏差と、累計の相関
    def fielding_ability(pid):
        return statistics.fmean(players[pid].ratings[i] for i in FIELDING_ITEMS)

    def running_ability(pid):
        return statistics.fmean(players[pid].ratings[i] for i in RUNNING_ITEMS)

    regular = [pid for pid in batters if all(sum(r[pid].chances_by_position.values()) >= args.min_chances for r in per_season if pid in r) and pid in per_season[0]]
    one = [float(per_season[0][pid].fielding) for pid in regular]
    print("## 守備の得点と真の能力(守備範囲・肩・捕球の平均)\n")
    print(f"- 対象:毎シーズン守備の機会 {args.min_chances} 以上の野手 {len(regular)} 人")
    print(f"- 1シーズンの守備の得点の標準偏差(運のぶれを含む): {statistics.pstdev(one):.2f} 点(平均 {statistics.fmean(one):+.2f})\n")
    rows = []
    for n in CHECKPOINTS:
        if n > len(per_season):
            continue
        cum = [sum(float(per_season[s][pid].fielding) for s in range(n)) / n for pid in regular]
        rows.append([f"{n}", f"{corr(cum, [fielding_ability(pid) for pid in regular]):.2f}", f"{statistics.pstdev(cum):.2f}"])
    print(_table(["シーズン数(累計の平均)", "真の能力との相関", "1シーズンあたりの得点の標準偏差"], rows))
    # 項目ごと
    rows = []
    n = min(len(per_season), CHECKPOINTS[-1])
    cum = [sum(float(per_season[s][pid].fielding) for s in range(n)) / n for pid in regular]
    for item in FIELDING_ITEMS:
        rows.append([item, f"{corr(cum, [players[pid].ratings[item] for pid in regular]):.2f}"])
    print(f"\n項目ごとの相関({n} シーズンの累計)\n\n" + _table(["能力", "相関"], rows))

    # ポジション別
    print("\n## ポジション別の守備の得点(1シーズンあたり。守備の機会のあった選手)\n")
    rows = []
    for pos in ("P",) + FIELDER_POSITIONS:
        vals = [float(r[pid].fielding_by_position[pos]) for r in per_season for pid in r if pos in r[pid].fielding_by_position and r[pid].chances_by_position.get(pos, 0) >= 50]
        if vals:
            rows.append([POSITION_LABELS.get(pos, pos), str(len(vals)), f"{statistics.fmean(vals):+.2f}", f"{statistics.pstdev(vals):.2f}", f"{min(vals):+.1f}", f"{max(vals):+.1f}"])
    print(_table(["ポジション", "選手・シーズン数", "平均", "標準偏差", "最小", "最大"], rows))

    # 走塁
    regular_pa = [pid for pid in batters if all(r[pid].plate_appearances >= args.min_pa for r in per_season if pid in r) and pid in per_season[0]]
    print(f"\n## 走塁の得点と真の能力(走力・走塁判断の平均)\n\n- 対象:毎シーズン打席 {args.min_pa} 以上の打者 {len(regular_pa)} 人")
    one = [float(per_season[0][pid].baserunning) for pid in regular_pa]
    print(f"- 1シーズンの走塁の得点の標準偏差: {statistics.pstdev(one):.2f} 点\n")
    rows = []
    for n in CHECKPOINTS:
        if n > len(per_season):
            continue
        cum = [sum(float(per_season[s][pid].baserunning) for s in range(n)) / n for pid in regular_pa]
        rows.append([f"{n}", f"{corr(cum, [running_ability(pid) for pid in regular_pa]):.2f}", f"{corr(cum, [players[pid].ratings['speed'] for pid in regular_pa]):.2f}", f"{corr(cum, [players[pid].ratings['baserunning'] for pid in regular_pa]):.2f}"])
    print(_table(["シーズン数(累計の平均)", "走力・走塁判断の平均との相関", "走力との相関", "走塁判断との相関"], rows))

    # 打撃 vs wRC+
    print("\n## 打撃の得点と wRC+ の相関(シーズンごと。打席 {0} 以上)\n".format(args.min_pa))
    rows = []
    for s, runs in enumerate(per_season):
        ids = [pid for pid in runs if runs[pid].plate_appearances >= args.min_pa and pid in wrc_by_season[s]]
        rows.append([str(s + 1), str(len(ids)), f"{corr([float(runs[pid].batting) for pid in ids], [float(wrc_by_season[s][pid]) for pid in ids]):.3f}", f"{corr([float(runs[pid].batting) / runs[pid].plate_appearances for pid in ids], [float(wrc_by_season[s][pid]) for pid in ids]):.3f}"])
    print(_table(["シーズン", "人数", "打撃の得点との相関", "1打席あたりの打撃の得点との相関"], rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
