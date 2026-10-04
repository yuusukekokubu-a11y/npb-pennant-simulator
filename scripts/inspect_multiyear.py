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
    ("rate", "単価(万円/WAR)", "{:,.0f}"),
    ("total_mean", "総年俸の平均", "{:,.0f}"),
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


def negotiation_stats(g: api.Game) -> dict:
    """契約更改の段階の集計(F3-2b。自球団は自動案でまとめて提示した直後)。理由・軸は設定のキー。"""
    proc = g.state.procedure
    my = g.state.my_team_id
    players = {p.id: p for p in g.state.league.all_players()} | {p.id: p for p in proc.market}
    first_ref: dict[str, int] = {t.id: 0 for t in g.state.league.teams}
    reasons: dict[str, int] = {}
    axis_offers: dict[str, list[int]] = {}
    for e in proc.negotiations.values():
        if not e["offers"]:
            continue
        o = e["offers"][0]
        p = players.get(e["player_id"])
        top = max(p.preference, key=p.preference.get) if p is not None and p.preference else "?"
        axis_offers.setdefault(top, [0, 0])
        axis_offers[top][0] += 1
        if o["accepted"]:
            axis_offers[top][1] += 1
        else:
            first_ref[e["team_id"]] += 1
            reasons[o["reason"] or "?"] = reasons.get(o["reason"] or "?", 0) + 1
    return {"my_first_refusals": first_ref.get(my, 0), "first_refusals": [first_ref[t] for t in sorted(first_ref)], "reasons": reasons, "axis_offers": axis_offers}


def run_world(seed: int, years: int, config, war_settings, log=sys.stderr, scout_level: str = "medium", money_rule: str = "none", my_team: bool = False) -> list[dict]:
    from pennant.contracts import team_salary

    g = api.Game.new(seed, [None] * 12, 0 if my_team else None, season_seed=seed, baselines="default", scout_level=scout_level, money_rule=money_rule)  # 観戦のみ(全球団 AI)。my_team なら T01 を自動案 + おまかせで進める(F3-2b)
    if my_team:
        prefs = [p.preference for p in g.state.league.all_players()]
    sizes = {t.id: len(t.players) for t in g.state.league.teams}
    positions = {t.id: sorted(p.position for p in t.players) for t in g.state.league.teams}
    rows = []
    for year in range(1, years + 1):
        g.advance(g.state.season.total_days)
        row = measure(g, config, war_settings)
        row["year"] = year
        row["seed"] = seed
        totals = [team_salary(t) for t in g.state.league.teams]  # 契約(F3-2a):単価・総年俸・使用率
        caps = [g.budget_info(t.id)["cap"] for t in g.state.league.teams]
        row["rate"] = g.state.contract_rates.get(str(year))
        row["total_mean"] = statistics.fmean(totals)
        row["total_max"] = max(totals)
        row["usage_max"] = max(t / c for t, c in zip(totals, caps)) if caps[0] else None
        row["win_pct"] = {r["team_id"]: r["wins"] / max(1, r["wins"] + r["losses"]) for lg in g.standings()["leagues"] for r in lg["rows"]}  # きびしいの格差の確認用(F3-2a)
        row["rank"] = {r["team_id"]: r["rank"] for lg in g.standings()["leagues"] for r in lg["rows"]}
        row["tiers"] = dict(g.state.budget_tiers)
        row["released"] = 0
        if year < years:
            actives = {p.id for a in g.state.season.actives.values() for p in a.players}  # このシーズンの一軍(FA の宣言に主力が含まれるか。F3-2c)
            t0 = time.perf_counter()
            summary = g.year_end()
            if my_team:  # 自球団:自動案でまとめて提示 → 集計 → おまかせ(AI と同じ方針)
                if g.state.procedure.phase == "renewal":
                    g.offseason_renew_auto()
                row.update(negotiation_stats(g))
                g.offseason_auto()
                negs = g.last_negotiations or {}
                row["negotiation_released"] = [e["expected"] for e in negs.values() if e["status"] == "released"]
                row["multi_year"] = sum(1 for e in negs.values() if e["status"] == "accepted" and e["offers"] and e["offers"][-1]["years"] > 1)
                row["accepted"] = sum(1 for e in negs.values() if e["status"] == "accepted")
                row["entries"] = len(negs)
                if year == 1:
                    row["preferences"] = prefs
                caps_now = {t.id: g.budget_info(t.id)["cap"] for t in g.state.league.teams}
                fills = {}
                for x in g.state.transactions:
                    if x["year"] == year and x["phase"] == "fill":
                        fills[x["team_id"]] = fills.get(x["team_id"], 0) + int(x.get("salary") or 0)
                row["over_cap_excl_fill"] = max([team_salary(t) - fills.get(t.id, 0) - caps_now[t.id] for t in g.state.league.teams if caps_now[t.id]] or [0])
            row["year_end_seconds"] = time.perf_counter() - t0
            fa = g.last_fa or {"info": {}, "results": [], "ranks": {}, "budget_releases": []}  # FA(F3-2c)
            row["fa_declared"] = len(fa["info"])
            row["fa_signed"] = len(fa["results"])
            row["fa_moved"] = sum(1 for x in fa["results"] if x["team_id"] != x["former_team"])
            row["fa_declared_exp"] = [round(float(x["expected"]), 2) for x in fa["info"].values()]
            row["fa_declared_regular"] = sum(1 for pid in fa["info"] if pid in actives)
            row["fa_signed_rank"] = [fa["ranks"].get(x["team_id"]) for x in fa["results"]]
            row["fa_signed_team"] = [x["team_id"] for x in fa["results"]]
            row["fa_signed_exp"] = [round(float(fa["info"][x["player_id"]]["expected"]), 2) for x in fa["results"]]
            row["budget_release_exp"] = [x.get("expected") for x in fa["budget_releases"]]
            row["retired"] = summary["counts"]["retired"]
            row["released"] = sum(1 for x in g.state.transactions if x["year"] == year and x["phase"] == "release")  # 確定の後に数える(そのオフの自由契約)
            row["budget_releases"] = sum(1 for x in g.state.transactions if x["year"] == year and x["phase"] == "release" and x.get("note") == "budget")
            row["budget_passes"] = sum(1 for x in g.state.transactions if x["year"] == year and x.get("note") == "budget")
            now = {t.id: len(t.players) for t in g.state.league.teams}
            assert now == sizes, f"選手の数が変わった(リーグ {seed}、{year} 年目の確定の後:{ {k: v for k, v in now.items() if v != sizes[k]} })"
            from pennant.draft import minimum_batters, minimum_positions, shortages

            assert all(not shortages(t.players, minimum_positions(), minimum_batters()) for t in g.state.league.teams), "最低人数を割った"  # F3-1:ポジションの構成は最低人数だけ守る(D-203)
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
    parser.add_argument("--scout-level", choices=("small", "medium", "large"), default="medium", help="スカウト評価のずれの段階(F3-1)")
    parser.add_argument("--money-rule", choices=("none", "loose", "standard", "strict"), default="none", help="お金のルール(F3-2a)")
    parser.add_argument("--my-team", action="store_true", help="球団 T01 を操作する球団にし、毎年 自動案でまとめて更改 → おまかせ で進める(F3-2b。更改の集計を JSON に残す)")
    parser.add_argument("--load", nargs="*", help="回す代わりに、--json で書き出したファイルを読んで表を出す(別々に回した世界をまとめる)")
    args = parser.parse_args(argv)
    config = load_generation_config()
    war_settings = load_war_settings()
    worlds = {}
    if args.load:
        for path in args.load:
            with open(path, encoding="utf-8") as f:
                worlds.update({int(k): v for k, v in json.load(f).items()})
        args.worlds = len(worlds)
        args.seed_start = min(worlds)
        args.years = min(len(v) for v in worlds.values())
    else:
        for seed in range(args.seed_start, args.seed_start + args.worlds):
            worlds[seed] = run_world(seed, args.years, config, war_settings, scout_level=args.scout_level, money_rule=args.money_rule, my_team=args.my_team)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(worlds, f, ensure_ascii=False, indent=1)

    print(f"# 複数年の安定の確認({args.worlds} 世界 × {args.years} 年。リーグ {'・'.join(str(s) for s in sorted(worlds))})\n")
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
    # きびしい:最強・最弱チームの勝率(全年の平均)と、予算の格差ごとの平均順位(F3-2a)
    if any(w[0].get("tiers") for w in worlds.values()):
        print("\n## 予算の格差と成績(きびしい)\n")
        best, worst, by_tier = [], [], {"large": [], "medium": [], "small": []}
        for seed, w in worlds.items():
            teams = list(w[0]["win_pct"])
            avg = {tid: statistics.fmean(r["win_pct"][tid] for r in w) for tid in teams}
            best.append(max(avg.values()))
            worst.append(min(avg.values()))
            for tid in teams:
                tier = w[0]["tiers"].get(tid, "medium")
                by_tier[tier].append(statistics.fmean(r["rank"][tid] for r in w))
        print(_table(["最強チームの勝率(全年の平均。世界の平均)", "最弱チームの勝率", "予算 大 の平均順位", "中", "小"], [[f"{statistics.fmean(best):.3f}", f"{statistics.fmean(worst):.3f}"] + [f"{statistics.fmean(by_tier[t]):.2f}" if by_tier[t] else "-" for t in ("large", "medium", "small")]]))
    total_time = sum(r["year_end_seconds"] for w in worlds.values() for r in w)
    count = sum(1 for w in worlds.values() for r in w if r["year_end_seconds"] > 0)
    print(f"\n- 年度の確定にかかった時間(PC):平均 {total_time / count:.2f} 秒({count} 回)" if count else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
