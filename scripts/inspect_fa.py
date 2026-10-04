"""FA の集計を表で出す開発者向けスクリプト(F3-2c。操作画面ではない)。

使い方:
    python scripts/inspect_fa.py --worlds 2 --years 5 --money-rule standard   # その場で回す
    python scripts/inspect_fa.py --load none.json standard.json ...           # inspect_multiyear.py --my-team --json の結果をまとめる

出力は Markdown の表:
  - 宣言・成立・移籍の人数(リーグ全体・1 年。平均と 10〜90%。目標の範囲に入った年の割合)
  - 宣言した選手の質(見込みの WAR の分布)と、そのシーズンの一軍(主力)が含まれる割合
  - 獲得数と前年順位の関係(上位 1〜2 位・中位 3〜4 位・下位 5〜6 位)
  - きびしいの大・中・小別の平均順位・勝率・FA の獲得数
  - 最強・最弱チームの勝率(30 年の平均)
  - 予算超過の解消で外れる人数と、その選手の見込みの WAR
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inspect_multiyear import run_world  # noqa: E402
from pennant.config import load_generation_config  # noqa: E402
from pennant.war import load_war_settings  # noqa: E402

DECLARE_RANGE = (10, 30)
MOVE_RANGE = (5, 15)


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(lines)


def _q(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else 0


def _share(values, lo, hi):
    return f"{100 * sum(lo <= x <= hi for x in values) / len(values):.0f}%" if values else "-"


def summarize(label: str, worlds: dict[str, list[dict]]) -> dict:
    rows = [r for rs in worlds.values() for r in rs if "fa_declared" in r]
    out = {"label": label, "years": len(rows)}
    out["declared"] = [r["fa_declared"] for r in rows]
    out["signed"] = [r["fa_signed"] for r in rows]
    out["moved"] = [r["fa_moved"] for r in rows]
    out["declared_exp"] = [x for r in rows for x in r["fa_declared_exp"]]
    out["regular"] = sum(r["fa_declared_regular"] for r in rows)
    out["signed_exp"] = [x for r in rows for x in r.get("fa_signed_exp", [])]
    ranks = [x for r in rows for x in r["fa_signed_rank"] if x]
    out["by_rank"] = {"上位(1〜2 位)": sum(1 for x in ranks if x <= 2), "中位(3〜4 位)": sum(1 for x in ranks if 3 <= x <= 4), "下位(5〜6 位)": sum(1 for x in ranks if x >= 5)}
    out["budget_release_exp"] = [x for r in rows for x in r.get("budget_release_exp", []) if x is not None]
    out["budget_releases"] = [len(r.get("budget_release_exp", [])) for r in rows]
    # 最強・最弱チームの勝率(全年の平均)、きびしいの格差ごと
    best, worst = [], []
    tiers = {"large": {"rank": [], "pct": [], "fa": 0, "teams": 0}, "medium": {"rank": [], "pct": [], "fa": 0, "teams": 0}, "small": {"rank": [], "pct": [], "fa": 0, "teams": 0}}
    for rs in worlds.values():
        full = [r for r in rs if "win_pct" in r]
        if not full:
            continue
        teams = list(full[0]["win_pct"])
        avg = {t: statistics.fmean(r["win_pct"][t] for r in full) for t in teams}
        best.append(max(avg.values()))
        worst.append(min(avg.values()))
        tier_of = full[0].get("tiers") or {}
        if tier_of:
            for t in teams:
                tr = tiers[tier_of.get(t, "medium")]
                tr["rank"].append(statistics.fmean(r["rank"][t] for r in full))
                tr["pct"].append(avg[t])
                tr["teams"] += 1
                tr["fa"] += sum(r.get("fa_signed_team", []).count(t) for r in full)
    out["best"], out["worst"], out["tiers"] = best, worst, tiers
    return out


def print_tables(sums: list[dict]) -> None:
    print("## 宣言・成立・移籍の人数(リーグ全体・1 年)\n")
    rows = []
    for s in sums:
        d, g, m = s["declared"], s["signed"], s["moved"]
        rows.append([s["label"], s["years"], f"{statistics.fmean(d):.1f}", f"{_q(d, 0.1)}〜{_q(d, 0.9)}", _share(d, *DECLARE_RANGE), f"{statistics.fmean(g):.1f}", f"{statistics.fmean(m):.1f}", f"{_q(m, 0.1)}〜{_q(m, 0.9)}", _share(m, *MOVE_RANGE)])
    print(_table(["お金のルール", "年数", "宣言の平均", "宣言の 10〜90%", f"宣言 {DECLARE_RANGE[0]}〜{DECLARE_RANGE[1]} 人の年", "成立の平均", "移籍の平均", "移籍の 10〜90%", f"移籍 {MOVE_RANGE[0]}〜{MOVE_RANGE[1]} 人の年"], rows))
    print("\n## 宣言した選手の質(見込みの WAR)\n")
    rows = []
    for s in sums:
        e = s["declared_exp"]
        g = s["signed_exp"]
        rows.append([s["label"], len(e), f"{statistics.fmean(e):.2f}" if e else "-", f"{_q(e, 0.5):.2f}" if e else "-", f"{_q(e, 0.9):.2f}" if e else "-", f"{100 * sum(1 for x in e if x >= 2.0) / len(e):.0f}%" if e else "-", f"{100 * s['regular'] / len(e):.0f}%" if e else "-", f"{statistics.fmean(g):.2f}" if g else "-"])
    print(_table(["お金のルール", "宣言した人数(合計)", "見込みの平均", "中央", "90%", "見込み 2.0 以上", "そのシーズンの一軍(主力)", "成立した選手の見込みの平均"], rows))
    print("\n## 獲得数と前年順位(成立した契約の、獲得した球団の前年順位)\n")
    rows = []
    for s in sums:
        total = sum(s["by_rank"].values()) or 1
        rows.append([s["label"]] + [f"{n}({100 * n / total:.0f}%)" for n in s["by_rank"].values()])
    print(_table(["お金のルール"] + list(sums[0]["by_rank"]), rows))
    print("\n## 強い球団への集中(30 年の平均の勝率)\n")
    print(_table(["お金のルール", "最強チームの勝率(世界の平均)", "最強の最大", "最弱チームの勝率", "最弱の最小"], [[s["label"], f"{statistics.fmean(s['best']):.3f}" if s["best"] else "-", f"{max(s['best']):.3f}" if s["best"] else "-", f"{statistics.fmean(s['worst']):.3f}" if s["worst"] else "-", f"{min(s['worst']):.3f}" if s["worst"] else "-"] for s in sums]))
    strict = [s for s in sums if s["tiers"]["large"]["teams"]]
    if strict:
        print("\n## きびしいの格差(大・中・小)\n")
        rows = []
        for s in strict:
            for tier, label in (("large", "大"), ("medium", "中"), ("small", "小")):
                t = s["tiers"][tier]
                if t["teams"]:
                    rows.append([s["label"], label, t["teams"], f"{statistics.fmean(t['rank']):.2f}", f"{statistics.fmean(t['pct']):.3f}", f"{t['fa'] / t['teams'] / max(1, s['years'] // max(1, len(s['best']))):.2f}"])
        print(_table(["お金のルール", "格差", "球団(世界 × 球団)", "平均順位", "勝率", "FA の獲得(1 球団・1 年)"], rows))
    print("\n## 予算超過の解消で外れる選手(1 年・リーグ全体)\n")
    rows = []
    for s in sums:
        e = s["budget_release_exp"]
        rows.append([s["label"], f"{statistics.fmean(s['budget_releases']):.1f}" if s["budget_releases"] else "-", f"{statistics.fmean(e):.2f}" if e else "-", f"{_q(e, 0.9):.2f}" if e else "-", f"{100 * sum(1 for x in e if x >= 1.0) / len(e):.0f}%" if e else "-"])
    print(_table(["お金のルール", "人数", "見込みの WAR の平均", "90%", "見込み 1.0 以上"], rows))
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(description="FA の集計")
    parser.add_argument("--money-rule", default="standard", choices=("none", "loose", "standard", "strict"))
    parser.add_argument("--worlds", type=int, default=2)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--load", nargs="*", help="inspect_multiyear.py --my-team --json の結果(ファイル名にお金のルールの名前を含める)")
    args = parser.parse_args(argv)
    print("# FA(F3-2c)\n")
    sums = []
    if args.load:
        for path in args.load:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            name = Path(path).stem
            label = next((r for r in ("none", "loose", "standard", "strict") if r in name), name)
            sums.append(summarize(label, data))
    else:
        config = load_generation_config()
        war_settings = load_war_settings()
        worlds = {str(seed): run_world(seed, args.years, config, war_settings, money_rule=args.money_rule, my_team=True) for seed in range(1, args.worlds + 1)}
        sums.append(summarize(args.money_rule, worlds))
    print_tables(sums)
    return 0


if __name__ == "__main__":
    sys.exit(main())
