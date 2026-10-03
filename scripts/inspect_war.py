"""WAR(第3弾③a)を複数シーズン回して確かめる開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_war.py                 # リーグ 1 を10シーズン回す
    python scripts/inspect_war.py --seasons 3     # シーズン数を変える(速い確認用)

出力は Markdown の表:
  - リーグ全体の WAR の合計(野手・投手)と目標(勝ち数 − .290 × 試合数)
  - チーム単位:控え水準の勝ち数 + チームの WAR と、実際の勝ち数の相関・誤差
  - ポジション別の平均 WAR(600 打席あたり)と、ポジション補正の有無による違い
  - WAR の分布(最大・最小・標準偏差)
  - WAR と真の総合能力の相関(1・3・10 シーズン)。真の能力はこのスクリプトの中だけで使う(D-108)
  - 投手の FIP 版と失点版の相関
  - ポジション別の「打撃 + 走塁」の平均(125試合あたり。ポジション補正の仮置き値との比較用)
"""

from __future__ import annotations

import argparse
import statistics
import sys
from fractions import Fraction

from pennant.abilities import BATTER, PITCHER, POSITION_LABELS
from pennant.baselines import load_baseline_settings, season_baselines
from pennant.parkfactors import load_park_settings, run_seasons
from pennant.records import season_records
from pennant.runvalues import player_park_factors, season_player_runs
from pennant.stats import overall
from pennant.war import POSITIONS_WITH_DH, dh_plate_appearances, load_war_settings, pitcher_park_factors, season_war, target_total_war, war_totals

CHECKPOINTS = (1, 3, 10)


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
    parser = argparse.ArgumentParser(description="WAR を複数シーズンで確かめる")
    parser.add_argument("--seed", type=int, default=1, help="リーグのシード")
    parser.add_argument("--seasons", type=int, default=10, help="回すシーズンの数")
    parser.add_argument("--min-pa", type=int, default=300, help="ポジション別・能力との相関に入れる、1シーズンの打席数の下限")
    parser.add_argument("--min-outs", type=int, default=150, help="投手の相関に入れる、1シーズンのアウト数の下限(50 回)")
    args = parser.parse_args(argv)
    settings = load_baseline_settings()
    war_settings = load_war_settings()

    seasons = []  # (lines, rec, runs, dh_pa)
    holder = {}

    def on_results(k, results, league, estimates):
        holder["league"] = league
        base = season_baselines(results, settings, settings.default_baselines())
        rec = season_records(results)
        pfs = player_park_factors(results, estimates)
        runs = season_player_runs(results, base, lambda pid: pfs.get(pid, Fraction(1)), rec)
        ppf = pitcher_park_factors(results, estimates)
        lines = season_war(results, base, runs, war_settings, rec, lambda pid: ppf.get(pid, Fraction(1)))
        seasons.append((lines, rec, runs, dh_plate_appearances(results)))
        print(f"  ... シーズン {k} / {args.seasons}", file=sys.stderr)

    run_seasons(args.seed, args.seasons, load_park_settings(), on_results=on_results)
    league = holder["league"]
    players = {p.id: p for p in league.all_players()}
    games = max(c["G"] for c in seasons[0][1].teams.values())

    print(f"# WAR(リーグ {args.seed}、{args.seasons} シーズン)\n")
    # 1. 合計
    print("## リーグ全体の WAR の合計と目標\n")
    rows = []
    for s, (lines, rec, _, _) in enumerate(seasons):
        t = war_totals(lines)
        target = target_total_war(rec, war_settings)
        total = t["batters"] + t["pitchers_ra"]
        rows.append([str(s + 1), f"{float(target):.1f}", f"{float(total):.1f}", f"{100 * float(total / target - 1):+.1f}%", f"{float(t['batters']):.1f}", f"{float(t['pitchers_ra']):.1f}", f"{float(t['pitchers_fip']):.1f}", f"{100 * float(t['batters'] / total):.0f}:{100 * float(t['pitchers_ra'] / total):.0f}"])
    print(_table(["シーズン", "目標(勝ち数 − .290 × 試合数)", "合計(野手 + 投手の失点版)", "差", "野手", "投手(失点版)", "投手(FIP 版)", "野手:投手"], rows))

    # 2. チーム単位
    print("\n## チーム単位:控え水準の勝ち数 + チームの WAR と、実際の勝ち数\n")
    xs, ys = [], []
    for lines, rec, _, _ in seasons:
        team_war = {}
        for v in lines.values():
            team_war[v.team_id] = team_war.get(v.team_id, Fraction(0)) + (v.war if v.role == "batter" else v.war_ra)
        for tid, c in rec.teams.items():
            xs.append(float(war_settings.replacement_win_pct * c["G"] + team_war.get(tid, Fraction(0))))
            ys.append(float(c["W"]))
    rmse = (sum((x - y) ** 2 for x, y in zip(xs, ys)) / len(xs)) ** 0.5
    print(f"- チーム・シーズン数 {len(xs)}。相関 {corr(xs, ys):.3f}、誤差(二乗平均平方根) {rmse:.2f} 勝、平均の差(予想 − 実際) {statistics.fmean(x - y for x, y in zip(xs, ys)):+.2f} 勝")

    # 3. ポジション別(主なポジション = 守備アウト数が最も多い位置。指名打者は DH の打席が最多のとき)
    def primary_position(pid, runs, dh_pa):
        r = runs.get(pid)
        best = max(r.outs_by_position.items(), key=lambda kv: kv[1], default=(None, 0)) if r else (None, 0)
        if dh_pa.get(pid, 0) * 27 / 4.3 > best[1]:
            return "DH"
        return best[0]

    print("\n## ポジション別の平均 WAR(600 打席あたり。打席 {0} 以上)\n".format(args.min_pa))
    by_pos = {p: [] for p in POSITIONS_WITH_DH}
    by_pos_nopos = {p: [] for p in POSITIONS_WITH_DH}
    by_pos_batrun = {p: [] for p in POSITIONS_WITH_DH}
    for lines, rec, runs, dh_pa in seasons:
        for pid, v in lines.items():
            if v.role != "batter" or v.plate_appearances < args.min_pa:
                continue
            pos = primary_position(pid, runs, dh_pa)
            if pos not in by_pos:
                continue
            scale = 600 / v.plate_appearances
            by_pos[pos].append(float(v.war) * scale)
            by_pos_nopos[pos].append(float(v.war - v.position / v.runs_per_win) * scale)
            by_pos_batrun[pos].append(float(v.batting + v.baserunning) / v.plate_appearances * rec.batters[pid]["PA"] / rec.batters[pid]["G"] * games if rec.batters[pid]["G"] else 0.0)
    rows = []
    for pos in POSITIONS_WITH_DH:
        if by_pos[pos]:
            rows.append([POSITION_LABELS.get(pos, pos), str(len(by_pos[pos])), f"{statistics.fmean(by_pos[pos]):+.2f}", f"{statistics.fmean(by_pos_nopos[pos]):+.2f}", f"{float(war_settings.position_runs(pos)):+.0f}"])
    print(_table(["ポジション", "選手・シーズン数", "平均 WAR / 600 打席", "ポジション補正なしの WAR / 600 打席", "ポジション補正(点/125試合)"], rows))

    print("\n## ポジション別の「打撃 + 走塁」の平均(125試合あたりの点。仮置きのポジション補正との比較用)\n")
    rows = []
    for pos in POSITIONS_WITH_DH:
        if by_pos_batrun[pos]:
            m = statistics.fmean(by_pos_batrun[pos])
            rows.append([POSITION_LABELS.get(pos, pos), f"{m:+.1f}", f"{-m:+.1f}", f"{float(war_settings.position_runs(pos)):+.0f}"])
    print(_table(["ポジション", "打撃 + 走塁の平均(点/125試合)", "それを打ち消す補正(= −平均)", "仮置きのポジション補正"], rows))

    # 4. 分布
    print("\n## WAR の分布(シーズンごとの全選手)\n")
    rows = []
    for label, pick in (("野手", lambda v: v.role == "batter" and v.plate_appearances > 0), ("投手(失点版)", lambda v: v.role == "pitcher" and v.outs > 0), ("投手(FIP 版)", lambda v: v.role == "pitcher" and v.outs > 0)):
        vals = []
        for lines, _, _, _ in seasons:
            for v in lines.values():
                if pick(v):
                    vals.append(float(v.war if v.role == "batter" else (v.war_fip if "FIP" in label else v.war_ra)))
        rows.append([label, str(len(vals)), f"{max(vals):+.2f}", f"{min(vals):+.2f}", f"{statistics.fmean(vals):+.2f}", f"{statistics.pstdev(vals):.2f}"])
    print(_table(["区分", "選手・シーズン数", "最大", "最小", "平均", "標準偏差"], rows))

    # 5. 能力との相関
    print("\n## WAR と真の総合能力の相関(累計の平均。野手は打席 {0} 以上、投手はアウト {1} 以上を毎シーズン満たす選手)\n".format(args.min_pa, args.min_outs))
    bat_ids = [pid for pid in players if players[pid].role == BATTER and all(pid in s[0] and s[0][pid].plate_appearances >= args.min_pa for s in seasons)]
    pit_ids = [pid for pid in players if players[pid].role == PITCHER and all(pid in s[0] and s[0][pid].outs >= args.min_outs for s in seasons)]
    rows = []
    for n in CHECKPOINTS:
        if n > len(seasons):
            continue
        bw = [sum(float(seasons[s][0][pid].war) for s in range(n)) / n for pid in bat_ids]
        pf = [sum(float(seasons[s][0][pid].war_fip) for s in range(n)) / n for pid in pit_ids]
        pr = [sum(float(seasons[s][0][pid].war_ra) for s in range(n)) / n for pid in pit_ids]
        rows.append([str(n), f"{corr(bw, [overall(players[p]) for p in bat_ids]):.2f}", f"{corr(pf, [overall(players[p]) for p in pit_ids]):.2f}", f"{corr(pr, [overall(players[p]) for p in pit_ids]):.2f}", f"{corr(pf, pr):.2f}"])
    print(_table(["シーズン数", f"野手({len(bat_ids)}人)", f"投手 FIP 版({len(pit_ids)}人)", "投手 失点版", "FIP 版と失点版の相関"], rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
