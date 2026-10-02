"""球場の倍率(真の値)と、複数シーズンでの実際の結果を表で出す開発者向けスクリプト(操作画面ではない)。

使い方:
    python scripts/inspect_parks.py                 # リーグ 1 の12球場の倍率と、20シーズンの結果
    python scripts/inspect_parks.py --seasons 5     # シーズン数を変える(速い確認用)
    python scripts/inspect_parks.py --seed 3        # 別のリーグ

球場ごとに、本拠地で行った試合(両チーム)の本塁打率(本塁打 ÷ 打席)と BABIP を、
同じチームがアウェイで行った試合の値と並べて、倍率が実際の結果に表れているかを確かめる(第2弾②a)。
出力は Markdown の表。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from pennant.newgame import new_league
from pennant.season import Season

HITS = ("single", "double", "triple", "home_run")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _tally(counts: Counter, log) -> None:
    for x in log:
        r = x.pa.result
        counts["PA"] += 1
        if r == "home_run":
            counts["HR"] += 1
        if r in HITS:
            counts["H"] += 1
        if r == "strikeout":
            counts["SO"] += 1
        if x.sac_fly:
            counts["SF"] += 1
        if r not in ("walk", "hit_by_pitch") and not x.sac_fly:
            counts["AB"] += 1


def _babip(c: Counter) -> float:
    denom = c["AB"] - c["SO"] - c["HR"] + c["SF"]
    return (c["H"] - c["HR"]) / denom if denom else 0.0


def _corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="球場の倍率と、複数シーズンでの実際の結果を表示する")
    parser.add_argument("--seed", type=int, default=1, help="リーグのシード")
    parser.add_argument("--seasons", type=int, default=20, help="回すシーズンの数(シーズンのシードは 1〜N)")
    args = parser.parse_args(argv)

    league = new_league(args.seed)
    names = {t.id: t for t in league.teams}
    print(f"# 球場の倍率(リーグのシード {args.seed})\n")
    rows = [[t.id, t.name, t.stadium, f"{t.park.home_run / 1000:.3f}", f"{t.park.babip / 1000:.3f}"] for t in league.teams]
    print(_table(["ID", "球団", "球場", "本塁打の倍率", "BABIP の倍率"], rows))
    by_league: dict[int, list] = {}
    for t in league.teams:
        by_league.setdefault(t.league_index, []).append(t)
    for i, ts in sorted(by_league.items()):
        print(f"- {league.league_names[i]} の平均:本塁打 {sum(t.park.home_run for t in ts) / len(ts) / 1000:.3f}、BABIP {sum(t.park.babip for t in ts) / len(ts) / 1000:.3f}")

    home: dict[str, Counter] = {t.id: Counter() for t in league.teams}
    away: dict[str, Counter] = {t.id: Counter() for t in league.teams}
    home_wins = games = 0
    for season_seed in range(1, args.seasons + 1):
        season = Season(new_league(args.seed), season_seed)
        for p in season.play_to_end().games:
            r = p.result
            _tally(home[r.home_team_id], r.log)  # この球場で行った試合(両チームの打席)
            _tally(away[r.away_team_id], r.log)  # 相手の球場で行った試合(両チームの打席)
            games += 1
            if r.winner == r.home_team_id:
                home_wins += 1
        print(f"  ... シーズン {season_seed} / {args.seasons} が終わりました", file=sys.stderr)

    print(f"\n# {args.seasons} シーズンでの実際の結果(本拠地の試合 と アウェイの試合。両チームの打席の合計)\n")
    rows = []
    hr_x, hr_y, bb_x, bb_y = [], [], [], []
    for t in league.teams:
        h, a = home[t.id], away[t.id]
        hr_home, hr_away = h["HR"] / h["PA"], a["HR"] / a["PA"]
        bb_home, bb_away = _babip(h), _babip(a)
        hr_x.append(t.park.home_run)
        hr_y.append(hr_home / hr_away if hr_away else 0)
        bb_x.append(t.park.babip)
        bb_y.append(bb_home / bb_away if bb_away else 0)
        rows.append(
            [
                t.name,
                f"{t.park.home_run / 1000:.3f}",
                f"{100 * hr_home:.2f}%",
                f"{100 * hr_away:.2f}%",
                f"{hr_home / hr_away:.3f}",
                f"{t.park.babip / 1000:.3f}",
                f"{bb_home:.3f}",
                f"{bb_away:.3f}",
                f"{bb_home / bb_away:.3f}",
            ]
        )
    print(_table(["球団", "本塁打の倍率", "本拠地の本塁打率", "アウェイの本塁打率", "比", "BABIP の倍率", "本拠地の BABIP", "アウェイの BABIP", "比"], rows))
    print(f"\n- 倍率と「本拠地 ÷ アウェイ」の相関(1に近いほど、倍率が結果に表れている):本塁打 {_corr(hr_x, hr_y):.2f}、BABIP {_corr(bb_x, bb_y):.2f}")
    print(f"- ホームの勝率:{100 * home_wins / games:.1f}%({games} 試合)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
