"""スカウト評価の精度と、ドラフトの選抜の効きを表で出す開発者向けスクリプト(F3-1。2 層化:D-212。作り直し③:A〜E の評価と一言、1 巡目の抽選。D-368、D-369。操作画面ではない)。

使い方:
    python scripts/inspect_draft.py                  # リーグ 1〜5 で、ずれの段階(小・中・大)ごとに測る
    python scripts/inspect_draft.py --leagues 2

出力は Markdown の表:
  - 評価の精度(内部の推定値):項目の誤差(推定値 − 真の値)の標準偏差、総合の誤差の標準偏差、
    項目間の誤差の相関(共通の見誤りで正になる)、同じ候補を何度見ても同じ値か、球団ごとに評価が違うか
  - スカウトの一言:一言に入った項目が、本当に目立つ(候補の中で標準化した値が 0.5 以上の)項目である割合、一言の数の分布
  - 選抜の効き:指名された選手の真の総合値の平均と、候補全体の平均(巡ごとも)、1 巡目の抽選の回数
  - 評価(A〜E)と、真のピークの見込みの関係
真の能力はこのスクリプトの中だけで使う(D-108)。
"""

from __future__ import annotations

import argparse
import statistics
import sys

from pennant.abilities import abilities_for
from pennant.config import load_generation_config, load_name_parts
from pennant.draft import load_draft_settings, make_candidates, run_ai_offseason, scout_report
from pennant.newgame import new_league
from pennant.offseason import age_update_retire, load_offseason_settings
from pennant.scouting import GRADES, item_sd, item_stats, overall_sd
from pennant.stats import overall, projected_overall


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="スカウト評価の精度とドラフトの選抜の効き")
    parser.add_argument("--leagues", type=int, default=5, help="使うリーグの数(シード 1 から)")
    args = parser.parse_args(argv)
    config, parts = load_generation_config(), load_name_parts()
    off = load_offseason_settings()
    ds = load_draft_settings()
    print("# スカウト評価とドラフト(F3-1。2 層の見誤り)\n")
    accuracy_rows = []
    overall_rows = []
    select_rows = []
    round_rows = []
    grade_rows = []
    comment_rows = []
    for level in ds.levels:
        sd = ds.level_sd(level)
        errs: list[float] = []
        overall_errs: list[float] = []
        pairs: list[tuple[float, float]] = []
        same = True
        differs = 0
        n_players = 0
        drafted: list[float] = []
        pool_all: list[float] = []
        by_round: dict[int, list[float]] = {}
        by_grade: dict[str, list[float]] = {g: [] for g in GRADES}
        ns = []
        hits = 0
        named = 0
        counts = {0: 0, 1: 0, 2: 0}
        lotteries = 0
        for seed in range(1, args.leagues + 1):
            league = new_league(seed, None, config, parts, prerun=True, offseason_settings=off, draft_settings=ds, scout_sd=sd, calibration=off.calibration(level))
            age_update_retire(league, 777 + seed, config, off, 1, off.calibration(level))
            scout_sd = {t.id: sd for t in league.teams}
            whole = make_candidates(league, 777 + seed, config, parts, ds, 1, off.calibration(level))
            pool_all += [overall(p) for p in whole]
            proc, _ = run_ai_offseason(league, 777 + seed, config, parts, off, ds, 1, off.calibration(level), scout_sd, [t.id for t in league.teams])
            picked = {x["player_id"]: x for x in proc.picks if x["player_id"] and x["phase"] == "draft"}
            lotteries += sum(1 for x in proc.lottery if len(x["teams"]) > 1)
            team_ids = [t.id for t in league.teams]
            stats = item_stats(whole)
            for p in whole:  # 候補全体(指名されなかった候補も含む)を、同じ手続きの評価で見る
                items = abilities_for(p.role)
                ns.append(len(items))
                r1 = scout_report(proc, p, team_ids[0], sd, ds)
                r2 = scout_report(proc, p, team_ids[0], sd, ds)
                same = same and r1.to_dict() == r2.to_dict()
                r3 = scout_report(proc, p, team_ids[1], sd, ds)
                n_players += 1
                if r3.to_dict() != r1.to_dict():
                    differs += 1
                es = {}
                for item in items:
                    e = r1.items[item] - p.abilities[item]
                    es[item] = e
                    errs.append(e)
                oe = r1.overall - sum(p.abilities[i] for i in items) / len(items)
                overall_errs.append(oe)
                pairs.append((es[items[0]], es[items[1]]))
                by_grade[r1.grade].append(projected_overall(p))
                counts[len(r1.strengths)] += 1
                for k in r1.strengths:
                    named += 1
                    m, s = stats[p.role][k]
                    hits += (p.abilities[k] - m) / s >= float(ds.comment["min_z"])
                if p.id in picked:
                    drafted.append(overall(p))
                    by_round.setdefault(picked[p.id]["round"], []).append(overall(p))
        corr = statistics.correlation([a for a, _ in pairs], [b for _, b in pairs])
        expect_corr = sd["common"] ** 2 / (sd["common"] ** 2 + sd["item"] ** 2)
        n_mean = statistics.fmean(ns)
        accuracy_rows.append([level, f"{sd['common']:g} / {sd['item']:g}", f"{item_sd(sd):.2f}", f"{statistics.pstdev(errs):.2f}", f"{corr:.2f}({expect_corr:.2f})", "○" if same else "×", f"{100 * differs / n_players:.0f}%"])
        overall_rows.append([level, f"{overall_sd(sd, round(n_mean)):.2f}", f"{statistics.pstdev(overall_errs):.2f}"])
        comment_rows.append([level, f"{100 * hits / max(1, named):.0f}%", " / ".join(f"{100 * counts[k] / n_players:.0f}%" for k in (0, 1, 2))])
        select_rows.append([level, f"{statistics.fmean(pool_all):.2f}", f"{statistics.fmean(drafted):.2f}", f"{statistics.fmean(drafted) - statistics.fmean(pool_all):+.2f}", str(len(drafted) // args.leagues), f"{lotteries / args.leagues:.1f}"])
        round_rows.append([level] + [f"{statistics.fmean(by_round[r]):.1f}" if by_round.get(r) else "-" for r in range(1, ds.rounds + 1)])
        grade_rows.append([level] + [f"{statistics.fmean(by_grade[g]):.1f}({len(by_grade[g]) // args.leagues})" if by_grade[g] else "-" for g in GRADES])
    print("## 評価の精度(項目。内部の推定値)\n")
    print(_table(["段階", "設定(共通 / 項目ごと)", "項目の誤差の目標(√(共通²+項目²))", "推定値 − 真の値 の標準偏差", "項目間の誤差の相関(かっこは理論値)", "何度見ても同じ", "球団で評価が違う候補"], accuracy_rows))
    print("\n## 評価の精度(総合。内部の推定値)\n")
    print(_table(["段階", "総合の誤差の目標(√(共通²+項目²/n))", "総合の推定値 − 真の総合値 の標準偏差"], overall_rows))
    print("\n## スカウトの一言\n")
    print(_table(["段階", "一言の項目が本当に目立つ割合", "一言の数 0 / 1 / 2 個の割合"], comment_rows))
    print("\n## 選抜の効き(ドラフトで指名された選手の真の総合値)\n")
    print(_table(["段階", "候補全体の平均", "指名された選手の平均", "差", "指名数(1 リーグ)", "1 巡目の抽選(1 リーグ)"], select_rows))
    print("\n## 巡ごとの、指名された選手の真の総合値の平均\n")
    print(_table(["段階"] + [f"{r} 巡" for r in range(1, ds.rounds + 1)], round_rows))
    print("\n## 評価(A〜E)と、真のピークの見込みの総合値の平均(かっこは 1 リーグあたりの人数)\n")
    print(_table(["段階"] + list(GRADES), grade_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
