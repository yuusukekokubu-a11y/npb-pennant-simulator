"""スカウト評価の精度と、ドラフトの選抜の効きを表で出す開発者向けスクリプト(F3-1。2 層化:D-212〜D-214。操作画面ではない)。

使い方:
    python scripts/inspect_draft.py                  # リーグ 1〜5 で、ずれの段階(小・中・大)ごとに測る
    python scripts/inspect_draft.py --leagues 2

出力は Markdown の表:
  - 評価の精度:項目の誤差(推定値 − 真の値)の標準偏差、総合の誤差の標準偏差、ふれ幅に真の値が入る割合(項目・総合。75〜85% が目安)、
    項目間の誤差の相関(共通の見誤りで正になる)、同じ候補を何度見ても同じ値か、球団ごとに評価が違うか
  - 選抜の効き:指名された選手の真の総合値の平均と、候補全体の平均(巡ごとも)
  - 天井の段階と、真の潜在能力の関係
真の能力はこのスクリプトの中だけで使う(D-108)。
"""

from __future__ import annotations

import argparse
import statistics
import sys

from pennant.abilities import strength_items_for
from pennant.config import load_generation_config, load_name_parts
from pennant.draft import load_draft_settings, make_candidates, run_ai_offseason, scout_report
from pennant.newgame import new_league
from pennant.offseason import age_update_retire, load_offseason_settings
from pennant.scouting import GRADES, item_sd, overall_sd
from pennant.stats import overall, potential_overall


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
    ceiling_rows = []
    for level in ds.levels:
        sd = ds.level_sd(level)
        errs: list[float] = []
        overall_errs: list[float] = []
        covered = 0
        overall_covered = 0
        pairs: list[tuple[float, float]] = []
        same = True
        differs = 0
        n_players = 0
        drafted: list[float] = []
        pool_all: list[float] = []
        by_round: dict[int, list[float]] = {}
        by_grade: dict[str, list[float]] = {g: [] for g in GRADES}
        ns = []
        for seed in range(1, args.leagues + 1):
            league = new_league(seed, None, config, parts, prerun=True, offseason_settings=off, draft_settings=ds, scout_sd=sd, calibration=off.calibration(level))
            age_update_retire(league, 777 + seed, config, off, 1, off.calibration(level))
            scout_sd = {t.id: sd for t in league.teams}
            whole = make_candidates(league, 777 + seed, config, parts, ds, 1, off.calibration(level))
            pool_all += [overall(p) for p in whole]
            proc, _ = run_ai_offseason(league, 777 + seed, config, parts, off, ds, 1, off.calibration(level), scout_sd, [t.id for t in league.teams])
            picked = {x["player_id"]: x for x in proc.picks if x["player_id"] and x["phase"] == "draft"}
            team_ids = [t.id for t in league.teams]
            for p in whole:  # 候補全体(指名されなかった候補も含む)を、同じ手続きの評価で見る
                items = strength_items_for(p.role)
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
                    e = r1.items[item] - p.ratings[item]
                    es[item] = e
                    errs.append(e)
                    covered += abs(e) <= r1.item_margin
                oe = r1.overall - sum(p.ratings[i] for i in items) / len(items)
                overall_errs.append(oe)
                overall_covered += abs(oe) <= r1.margin
                pairs.append((es[items[0]], es[items[1]]))
                by_grade[r1.ceiling].append(potential_overall(p))
                if p.id in picked:
                    drafted.append(overall(p))
                    by_round.setdefault(picked[p.id]["round"], []).append(overall(p))
        corr = statistics.correlation([a for a, _ in pairs], [b for _, b in pairs])
        expect_corr = sd["common"] ** 2 / (sd["common"] ** 2 + sd["item"] ** 2)
        n_mean = statistics.fmean(ns)
        accuracy_rows.append([level, f"{sd['common']:g} / {sd['item']:g}", f"{item_sd(sd):.2f}", f"{statistics.pstdev(errs):.2f}", f"{100 * covered / len(errs):.1f}%", f"{corr:.2f}({expect_corr:.2f})", "○" if same else "×", f"{100 * differs / n_players:.0f}%"])
        overall_rows.append([level, f"{overall_sd(sd, round(n_mean)):.2f}", f"{statistics.pstdev(overall_errs):.2f}", f"{100 * overall_covered / len(overall_errs):.1f}%", f"±{overall_sd(sd, round(n_mean)) * ds.margin_z:.1f}", f"±{item_sd(sd) * ds.margin_z:.1f}"])
        select_rows.append([level, f"{statistics.fmean(pool_all):.2f}", f"{statistics.fmean(drafted):.2f}", f"{statistics.fmean(drafted) - statistics.fmean(pool_all):+.2f}", str(len(drafted) // args.leagues)])
        round_rows.append([level] + [f"{statistics.fmean(by_round[r]):.1f}" if by_round.get(r) else "-" for r in range(1, ds.rounds + 1)])
        ceiling_rows.append([level] + [f"{statistics.fmean(by_grade[g]):.1f}({len(by_grade[g]) // args.leagues})" if by_grade[g] else "-" for g in GRADES])
    print("## 評価の精度(項目)\n")
    print(_table(["段階", "設定(共通 / 項目ごと)", "項目の誤差の目標(√(共通²+項目²))", "推定値 − 真の値 の標準偏差", "ふれ幅に真の値が入る割合", "項目間の誤差の相関(かっこは理論値)", "何度見ても同じ", "球団で評価が違う候補"], accuracy_rows))
    print("\n## 評価の精度(総合)\n")
    print(_table(["段階", "総合の誤差の目標(√(共通²+項目²/n))", "総合の推定値 − 真の総合値 の標準偏差", "ふれ幅に真の値が入る割合", "総合のふれ幅", "項目のふれ幅"], overall_rows))
    print("\n## 選抜の効き(ドラフトで指名された選手の真の総合値)\n")
    print(_table(["段階", "候補全体の平均", "指名された選手の平均", "差", "指名数(1 リーグ)"], select_rows))
    print("\n## 巡ごとの、指名された選手の真の総合値の平均\n")
    print(_table(["段階"] + [f"{r} 巡" for r in range(1, ds.rounds + 1)], round_rows))
    print("\n## 天井の段階と、真の潜在能力の総合値の平均(かっこは 1 リーグあたりの人数)\n")
    print(_table(["段階"] + list(GRADES), ceiling_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
