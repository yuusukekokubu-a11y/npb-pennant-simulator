"""複数年(F2)を長く回して、リーグが安定しているかを確かめる開発者向けスクリプト(操作画面ではない。D-188)。

使い方:
    python scripts/inspect_multiyear.py                       # リーグ 1〜5 を 30 年回す
    python scripts/inspect_multiyear.py --worlds 2 --years 5  # 速い確認用
    python scripts/inspect_multiyear.py --json out.json       # 年ごとの数を JSON にも書く

出力は Markdown の表(世界ごとの値を年ごとに平均したもの):
  - 一軍相当の選手の総合能力の平均(真の能力。このスクリプトの中だけで使う。D-108)
  - リーグの打率・本塁打/試合・得点/試合・防御率
  - WAR の合計と目標(勝ち数 − .290 × 試合数)の比
  - 年齢の分布(平均・標準偏差・年齢帯の割合)
  - 選手の数・引退と新人の数・年度の確定にかかった時間
  - 最初の5年と最後の5年の平均の差(傾向があるかどうか)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from fractions import Fraction

from pennant import api
from pennant.config import load_generation_config
from pennant.stats import first_team_players, overall
from pennant.war import load_war_settings, target_total_war, war_totals

AGE_BINS = ((18, 22), (23, 27), (28, 32), (33, 37), (38, 99))
COLUMNS = [
    ("first_team", "一軍の能力", "{:.2f}"),
    ("avg", "打率", "{:.3f}"),
    ("hr_per_game", "本塁打/試合", "{:.2f}"),
    ("runs_per_game", "得点/試合(両チーム)", "{:.2f}"),
    ("era", "防御率", "{:.2f}"),
    ("war_ratio", "WAR 合計 ÷ 目標", "{:.3f}"),
    ("age_mean", "年齢の平均", "{:.1f}"),
    ("age_sd", "年齢の SD", "{:.1f}"),
    ("age_18_22", "18〜22", "{:.1%}"),
    ("age_23_27", "23〜27", "{:.1%}"),
    ("age_28_32", "28〜32", "{:.1%}"),
    ("age_33_37", "33〜37", "{:.1%}"),
    ("age_38_99", "38+", "{:.1%}"),
    ("players", "選手", "{:.0f}"),
    ("retired", "引退", "{:.1f}"),
    ("year_end_seconds", "確定(秒)", "{:.2f}"),
]


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def measure(g: api.Game, config, war_settings) -> dict:
    """1シーズン(終了時)の数。"""
    rec = g.records.total
    bat = {k: sum(c[k] for c in rec.batters.values()) for k in ("AB", "H", "HR", "R", "PA")}
    pit = {k: sum(c[k] for c in rec.pitchers.values()) for k in ("ER", "OUTS")}
    games = sum(c["G"] for c in rec.teams.values()) / 2
    lines = g.war_lines()
    t = war_totals(lines)
    target = target_total_war(rec, war_settings)
    players = g.state.league.all_players()
    ages = [p.age for p in players]
    out = {
        "first_team": statistics.fmean(overall(p) for p in first_team_players([g.state.league], config)),
        "avg": bat["H"] / bat["AB"] if bat["AB"] else 0.0,
        "hr_per_game": bat["HR"] / games if games else 0.0,
        "runs_per_game": bat["R"] / games if games else 0.0,
        "era": 27 * pit["ER"] / pit["OUTS"] if pit["OUTS"] else 0.0,
        "war_ratio": float((t["batters"] + t["pitchers_ra"]) / target) if target else 0.0,
        "age_mean": statistics.fmean(ages),
        "age_sd": statistics.pstdev(ages),
        "players": len(players),
    }
    for lo, hi in AGE_BINS:
        out[f"age_{lo}_{hi}"] = sum(1 for a in ages if lo <= a <= hi) / len(ages)
    return out


def run_world(seed: int, years: int, config, war_settings, log=sys.stderr) -> list[dict]:
    g = api.Game.new(seed, [None] * 12, 0, season_seed=seed, baselines="default")
    sizes = {t.id: len(t.players) for t in g.state.league.teams}
    positions = {t.id: sorted(p.position for p in t.players) for t in g.state.league.teams}
    rows = []
    for year in range(1, years + 1):
        g.advance(g.state.season.total_days)
        row = measure(g, config, war_settings)
        row["year"] = year
        row["seed"] = seed
        if year < years:
            t0 = time.perf_counter()
            summary = g.year_end()
            row["year_end_seconds"] = time.perf_counter() - t0
            row["retired"] = summary["counts"]["retired"]
            assert {t.id: len(t.players) for t in g.state.league.teams} == sizes, "選手の数が変わった"
            assert {t.id: sorted(p.position for p in t.players) for t in g.state.league.teams} == positions, "ポジションの数が変わった"
        else:
            row["year_end_seconds"] = 0.0
            row["retired"] = 0
        rows.append(row)
        print(f"  ... リーグ {seed}: {year} / {years} 年(一軍の能力 {row['first_team']:.2f}、打率 {row['avg']:.3f}、年齢 {row['age_mean']:.1f})", file=log)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="複数年を回して、リーグの安定を確かめる")
    parser.add_argument("--worlds", type=int, default=5, help="回す世界(リーグのシード)の数。シードは --seed-start から順")
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--years", type=int, default=30)
    parser.add_argument("--json", help="年ごとの数を書き出す JSON ファイル")
    args = parser.parse_args(argv)
    config = load_generation_config()
    war_settings = load_war_settings()
    worlds = {}
    for seed in range(args.seed_start, args.seed_start + args.worlds):
        worlds[seed] = run_world(seed, args.years, config, war_settings)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(worlds, f, ensure_ascii=False, indent=1)

    print(f"# 複数年の安定の確認({args.worlds} 世界 × {args.years} 年。リーグ {args.seed_start}〜{args.seed_start + args.worlds - 1})\n")
    print("## 年ごとの平均(世界をまたいだ平均)\n")
    keys = [k for k, _, _ in COLUMNS]
    rows = []
    yearly = {}
    for year in range(1, args.years + 1):
        vals = {k: statistics.fmean(w[year - 1][k] for w in worlds.values()) for k in keys}
        yearly[year] = vals
        rows.append([str(year)] + [fmt.format(vals[k]) for k, _, fmt in COLUMNS])
    print(_table(["年"] + [label for _, label, _ in COLUMNS], rows))

    n = min(5, args.years)
    print(f"\n## 傾向:最初の {n} 年と最後の {n} 年の平均の差\n")
    rows = []
    for k, label, fmt in COLUMNS:
        if k in ("year_end_seconds", "retired"):
            continue
        head = statistics.fmean(yearly[y][k] for y in range(1, n + 1))
        tail = statistics.fmean(yearly[y][k] for y in range(args.years - n + 1, args.years + 1))
        rows.append([label, fmt.format(head), fmt.format(tail), fmt.format(tail - head) if "%" not in fmt else f"{tail - head:+.1%}"])
    print(_table(["項目", f"最初の {n} 年", f"最後の {n} 年", "差"], rows))

    print("\n## 世界ごとの、全年の平均と最小・最大(ばらつきの確認)\n")
    rows = []
    for seed, w in worlds.items():
        rows.append([str(seed)] + [f"{statistics.fmean(r[k] for r in w):.3g}({min(r[k] for r in w):.3g}〜{max(r[k] for r in w):.3g})" for k in ("first_team", "avg", "runs_per_game", "era", "war_ratio", "age_mean")])
    print(_table(["リーグ", "一軍の能力", "打率", "得点/試合", "防御率", "WAR ÷ 目標", "年齢の平均"], rows))
    total_time = sum(r["year_end_seconds"] for w in worlds.values() for r in w)
    count = sum(1 for w in worlds.values() for r in w if r["year_end_seconds"] > 0)
    print(f"\n- 年度の確定にかかった時間(PC):平均 {total_time / count:.2f} 秒({count} 回)" if count else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
