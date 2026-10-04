"""年俸の分布、年俸と WAR の関係、総年俸と予算、30 年の推移を表で出す開発者向けスクリプト(F3-2a。操作画面ではない)。

使い方:
    python scripts/inspect_contracts.py                       # お金のルール「なし」でリーグ 1〜3 を 2 シーズン回し、分布と関係を出す
    python scripts/inspect_contracts.py --rule strict --years 30 --worlds 1   # 30 年の推移(総年俸・予算・単価・自由契約の人数)
    python scripts/inspect_contracts.py --load out.json       # inspect_multiyear.py --money-rule で書き出した JSON から推移の表だけ出す

出力は Markdown の表:
  - 年俸の分布(最低・中央・平均・最大・上位 10%)
  - 年俸と翌シーズンの WAR の相関、年俸と真の総合の相関(真の能力はこのスクリプトの中だけで使う。D-108)
  - 球団別の総年俸の最大・中央値・最小と、予算(上限)に対する使用率
  - 年俸 ÷ 見込みの WAR の分布(割安・割高の選手)
  - 年ごとの推移(単価、総年俸の平均、予算超過の自由契約、契約満了の更改の人数)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys

from pennant import api
from pennant.contracts import team_salary
from pennant.stats import overall


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def distribution(rule: str, worlds: int, log) -> None:
    salaries = []
    pairs_war = []  # (年俸, 翌シーズンの WAR)
    pairs_true = []  # (年俸, 真の総合)
    ratios = []  # 年俸 ÷ 見込み(万円 / WAR)
    team_rows = []
    for seed in range(1, worlds + 1):
        g = api.Game.new(seed, [None] * 12, None, season_seed=seed, baselines="default", money_rule=rule)
        g.advance(g.state.season.total_days)
        g.year_end()  # 1 シーズン目の成績で更改された契約を見る
        state = g.state
        from pennant.draft import state_war_history

        war_history = state_war_history(state)  # 割安・割高の目安は、前のシーズンの WAR(履歴)だけで見る
        sal = {p.id: int(p.contract["salary"]) for t in state.league.teams for p in t.players}
        salaries += list(sal.values())
        for t in state.league.teams:
            total = team_salary(t)
            b = g.budget_info(t.id)
            team_rows.append((seed, t.id, total, b["cap"]))
            for p in t.players:
                pairs_true.append((sal[p.id], overall(p)))
                hist = war_history(p.id, p.role)
                if hist:
                    exp = sum(w for w, _ in hist[:1])
                    if exp > 0.2:
                        ratios.append(sal[p.id] / exp)
        g.advance(state.season.total_days)  # 翌シーズン
        lines = g.war_lines()
        for t in state.league.teams:
            for p in t.players:
                if p.id in sal and p.id in lines:
                    line = lines[p.id]
                    pairs_war.append((sal[p.id], float(line.war if p.role == "batter" else line.war_ra)))
        print(f"  ... リーグ {seed}:分布を集計", file=log)
    print(f"## 年俸の分布(お金のルール {rule}、{worlds} リーグ、2 シーズン目の開始時。単位は万円)\n")
    print(_table(["人数", "最低", "中央", "平均", "上位 10%", "最大"], [[str(len(salaries)), f"{min(salaries):,}", f"{statistics.median(salaries):,.0f}", f"{statistics.fmean(salaries):,.0f}", f"{_pct(salaries, 0.9):,}", f"{max(salaries):,}"]]))
    print("\n## 年俸と成績・能力の関係\n")
    corr_war = statistics.correlation([a for a, _ in pairs_war], [b for _, b in pairs_war])
    corr_true = statistics.correlation([a for a, _ in pairs_true], [b for _, b in pairs_true])
    print(_table(["年俸と翌シーズンの WAR の相関(出場した選手)", "年俸と真の総合の相関(全選手。開発者向け)"], [[f"{corr_war:.2f}({len(pairs_war)} 人)", f"{corr_true:.2f}({len(pairs_true)} 人)"]]))
    print("\n## 球団別の総年俸(万円)と予算\n")
    totals = [r[2] for r in team_rows]
    cap = team_rows[0][3]
    print(_table(["最大", "中央値", "最小", "平均", "予算の上限(目安)", "使用率の平均"], [[f"{max(totals):,}", f"{statistics.median(totals):,.0f}", f"{min(totals):,}", f"{statistics.fmean(totals):,.0f}", f"{cap:,}" if cap else "-", f"{100 * statistics.fmean(totals) / cap:.1f}%" if cap else "-"]]))
    print("\n## 年俸 ÷ 前のシーズンの WAR(万円 / WAR。前のシーズンに 0.2 WAR 以上の選手)\n")
    if ratios:
        print(_table(["人数", "下位 10%(割安)", "中央", "上位 10%(割高)"], [[str(len(ratios)), f"{_pct(ratios, 0.1):,.0f}", f"{statistics.median(ratios):,.0f}", f"{_pct(ratios, 0.9):,.0f}"]]))
    print()


def trend(rule: str, worlds: int, years: int, log) -> list[dict]:
    rows = []
    for seed in range(1, worlds + 1):
        g = api.Game.new(seed, [None] * 12, None, season_seed=seed, baselines="default", money_rule=rule)
        for year in range(1, years + 1):
            state = g.state
            totals = [team_salary(t) for t in state.league.teams]
            caps = [g.budget_info(t.id)["cap"] for t in state.league.teams]
            row = {"seed": seed, "year": year, "rate": state.contract_rates.get(str(year)), "total_mean": statistics.fmean(totals), "total_max": max(totals), "usage_max": max(t / c for t, c in zip(totals, caps)) if caps[0] else None}
            g.advance(state.season.total_days)
            if year < years:
                g.year_end()
                tx = [x for x in state.transactions if x["year"] == year]
                row["budget_releases"] = sum(1 for x in tx if x["phase"] == "release" and x.get("note") == "budget")
                row["releases"] = sum(1 for x in tx if x["phase"] == "release")
                row["budget_passes"] = sum(1 for x in tx if x.get("note") == "budget")
            rows.append(row)
            print(f"  ... リーグ {seed}:{year} / {years} 年(総年俸の平均 {row['total_mean']:,.0f})", file=log)
    return rows


def print_trend(rows: list[dict], rule: str) -> None:
    years = sorted({r["year"] for r in rows})
    print(f"## 年ごとの推移(お金のルール {rule}。世界をまたいだ平均)\n")
    out = []
    for y in years:
        rs = [r for r in rows if r["year"] == y]
        rate = statistics.fmean(r["rate"] for r in rs if r["rate"])
        out.append([str(y), f"{rate:,.0f}", f"{statistics.fmean(r['total_mean'] for r in rs):,.0f}", f"{statistics.fmean(r['total_max'] for r in rs):,.0f}", f"{100 * statistics.fmean(r['usage_max'] for r in rs):.1f}%" if rs[0]["usage_max"] else "-", f"{statistics.fmean(r.get('releases', 0) for r in rs):.1f}", f"{statistics.fmean(r.get('budget_releases', 0) for r in rs):.1f}", f"{statistics.fmean(r.get('budget_passes', 0) for r in rs):.1f}"])
    print(_table(["年", "単価(万円/WAR)", "総年俸の平均", "総年俸の最大", "使用率の最大", "自由契約(全体)", "うち予算超過", "予算不足のパス"], out))
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(description="年俸の分布と推移")
    parser.add_argument("--rule", default="none", choices=("none", "loose", "standard", "strict"))
    parser.add_argument("--worlds", type=int, default=3)
    parser.add_argument("--years", type=int, default=0, help="0 なら分布だけ。1 以上なら推移を回す")
    parser.add_argument("--json", help="推移を書き出す JSON")
    parser.add_argument("--load", nargs="*", help="inspect_multiyear.py の JSON から推移の表を出す")
    args = parser.parse_args(argv)
    print(f"# 契約と年俸(F3-2a)\n")
    if args.load:
        rows = []
        for path in args.load:
            with open(path, encoding="utf-8") as f:
                for seed, rs in json.load(f).items():
                    rows += [{"seed": int(seed), **r} for r in rs if "rate" in r]
        print_trend(rows, args.rule)
        return 0
    if args.years > 0:
        rows = trend(args.rule, args.worlds, args.years, sys.stderr)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=1)
        print_trend(rows, args.rule)
    else:
        distribution(args.rule, args.worlds, sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
