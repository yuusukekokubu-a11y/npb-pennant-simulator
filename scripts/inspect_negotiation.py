"""契約更改と志望の判定の集計を表で出す開発者向けスクリプト(F3-2b。操作画面ではない)。

使い方:
    python scripts/inspect_negotiation.py --worlds 2 --years 5                 # その場で回す(お金のルールは --money-rule)
    python scripts/inspect_negotiation.py --load none.json loose.json ...      # inspect_multiyear.py --my-team --json の結果をまとめる

球団 T01 を操作する球団にし、毎年「自動案でまとめて更改」→「おまかせ」で進める(ほかの球団は AI)。出力は Markdown の表:
  - 断る人数(自動案):自球団の毎年の人数の分布(平均・10%・中央・90%・最小・最大・3〜8 人に入った割合)と、全球団の分布
  - 断った理由の内訳(軸ごと。最初の提示)
  - 志望の分布(軸ごとの重みの平均・標準偏差、いちばん重い軸の割合)と、軸ごとの受諾率(いちばん重い軸で分けた、最初の提示を受けた割合)
  - 複数年の割合(受けた契約のうち 2 年以上)
  - 交渉が決裂して自由契約になった人数(全球団、1 年あたり)と、その選手の見込みの WAR の分布
  - 標準以上で、上限を超えた額の最大(自動補充の分を除く)
志望(隠し情報)はこのスクリプトの中だけで使う(D-108)。
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
from pennant.negotiation import load_negotiation_settings  # noqa: E402
from pennant.war import load_war_settings  # noqa: E402


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(lines)


def _q(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else 0


def summarize(label: str, rows: list[dict], neg) -> dict:
    """1 つのお金のルール(複数の世界)の年ごとの行から、表の数をまとめる。"""
    rows = [r for r in rows if "my_first_refusals" in r]
    mine = [r["my_first_refusals"] for r in rows]
    everyone = [n for r in rows for n in r["first_refusals"]]
    reasons: dict[str, int] = {}
    axis: dict[str, list[int]] = {}
    for r in rows:
        for k, n in r["reasons"].items():
            reasons[k] = reasons.get(k, 0) + n
        for k, (n, ok) in r["axis_offers"].items():
            a = axis.setdefault(k, [0, 0])
            a[0] += n
            a[1] += ok
    released = [x for r in rows for x in r["negotiation_released"]]
    prefs = [p for r in rows for p in r.get("preferences", []) if p]
    return {
        "label": label, "years": len(rows), "mine": mine, "everyone": everyone, "reasons": reasons, "axis": axis,
        "released_per_year": statistics.fmean(len(r["negotiation_released"]) for r in rows) if rows else 0.0, "released": released,
        "multi_share": sum(r["multi_year"] for r in rows) / max(1, sum(r["accepted"] for r in rows)),
        "prefs": prefs, "over_cap": max((r.get("over_cap_excl_fill", 0) for r in rows), default=0),
        "seconds": statistics.fmean(r["year_end_seconds"] for r in rows) if rows else 0.0,
    }


def print_tables(sums: list[dict], neg) -> None:
    axes = list(neg.axes)
    print("## 断る人数(自動案でまとめて提示したとき、最初の提示を断った人数)\n")
    out = []
    for s in sums:
        m, e = s["mine"], s["everyone"]
        out.append([s["label"], s["years"], f"{statistics.fmean(m):.2f}", _q(m, 0.1), _q(m, 0.5), _q(m, 0.9), min(m), max(m), f"{100 * sum(3 <= x <= 8 for x in m) / len(m):.0f}%", f"{statistics.fmean(e):.2f}", f"{_q(e, 0.1)}〜{_q(e, 0.9)}", f"{100 * sum(3 <= x <= 8 for x in e) / len(e):.0f}%"])
    print(_table(["お金のルール", "年数(世界 × 年)", "自球団の平均", "10%", "中央", "90%", "最小", "最大", "3〜8 人の年", "全球団の平均", "全球団の 10〜90%", "全球団で 3〜8 人"], out))
    print("\n## 断った理由の内訳(最初の提示。全球団)\n")
    out = []
    for s in sums:
        total = sum(s["reasons"].values()) or 1
        out.append([s["label"]] + [f"{100 * s['reasons'].get(a, 0) / total:.0f}%({s['reasons'].get(a, 0)})" for a in axes])
    print(_table(["お金のルール"] + [neg.reason(a) for a in axes], out))
    print("\n## 志望の分布と、軸ごとの受諾率(最初の提示。いちばん重い軸で分けた)\n")
    out = []
    prefs = [p for s in sums for p in s["prefs"]]
    if prefs:
        for a in axes:
            w = [p.get(a, 0.0) for p in prefs]
            top = sum(1 for p in prefs if max(p, key=p.get) == a) / len(prefs)
            out.append([neg.label(a), f"{statistics.fmean(w):.3f}", f"{statistics.pstdev(w):.3f}", f"{100 * top:.0f}%"] + [f"{100 * s['axis'].get(a, [0, 0])[1] / max(1, s['axis'].get(a, [0, 0])[0]):.1f}%" for s in sums])
        print(_table(["軸", "重みの平均", "重みの標準偏差", "いちばん重い軸の割合"] + [f"受諾率({s['label']})" for s in sums], out))
    print("\n## 複数年・交渉決裂・予算\n")
    out = []
    for s in sums:
        r = s["released"]
        out.append([s["label"], f"{100 * s['multi_share']:.1f}%", f"{s['released_per_year']:.1f}", f"{statistics.fmean(r):.2f}" if r else "-", f"{_q(r, 0.5):.2f}" if r else "-", f"{_q(r, 0.9):.2f}" if r else "-", f"{100 * sum(1 for x in r if x >= 1.0) / len(r):.0f}%" if r else "-", f"{s['over_cap']:,}" if s["label"] in ("standard", "strict") else "-", f"{s['seconds']:.2f}"])
    print(_table(["お金のルール", "受けた契約のうち複数年", "交渉決裂で自由契約(全球団・1 年)", "その選手の見込みの WAR の平均", "中央", "90%", "見込み 1.0 以上の割合", "上限を超えた額の最大(補充を除く。万円)", "オフ全体の時間(PC・秒)"], out))
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(description="契約更改と志望の判定の集計")
    parser.add_argument("--money-rule", default="none", choices=("none", "loose", "standard", "strict"))
    parser.add_argument("--worlds", type=int, default=2)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--load", nargs="*", help="inspect_multiyear.py --my-team --json の結果(ファイル名の先頭がお金のルールなら、その名前で表に出す)")
    args = parser.parse_args(argv)
    neg = load_negotiation_settings()
    print("# 契約更改と志望の判定(F3-2b)\n")
    sums = []
    if args.load:
        for path in args.load:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            name = Path(path).stem
            label = next((r for r in ("none", "loose", "standard", "strict") if r in name), name)
            rows = [r for rs in data.values() for r in rs]
            sums.append(summarize(label, rows, neg))
    else:
        config = load_generation_config()
        war_settings = load_war_settings()
        rows = []
        for seed in range(1, args.worlds + 1):
            rows += run_world(seed, args.years, config, war_settings, money_rule=args.money_rule, my_team=True)
        sums.append(summarize(args.money_rule, rows, neg))
    print_tables(sums, neg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
