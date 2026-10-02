"""シーズンの集計(開発者向けの確認用。画面ではない)。scripts/inspect_season.py から使う。"""

from __future__ import annotations

import statistics
from collections import Counter

from .game_stats import GAME_TARGETS, summarize
from .season import Season, SeasonResult

# 校正の目標(一般的な水準の目安。依頼「実装④:シーズン(日程・順位)」で示された範囲)
SEASON_TARGETS = {
    "ホームの勝率(引き分け除く)": (0.52, 0.54),
    "チーム勝率の標準偏差": (0.04, 0.07),
}


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def home_win_rate(result: SeasonResult) -> float:
    decided = [p.result for p in result.games if not p.result.tie]
    return sum(1 for r in decided if r.winner == r.home_team_id) / len(decided)


def pct_sd(result: SeasonResult) -> dict[int, float]:
    """リーグごとの、チーム勝率の標準偏差(ばらつき)。"""
    return {i: statistics.pstdev([row.pct for row in rows]) for i, rows in result.standings.items()}


def home_away_counts(season: Season) -> dict[str, tuple[int, int]]:
    home, away = Counter(), Counter()
    for g in season.schedule.games:
        home[g.home_id] += 1
        away[g.away_id] += 1
    return {t.id: (home[t.id], away[t.id]) for t in season.league.teams}


def starter_usage(result: SeasonResult) -> tuple[Counter, Counter, Counter]:
    """(投手 ID → 先発の回数)、(登板間隔の日数 → 回数)、(チーム ID → 疲労で先発を飛ばした回数)。"""
    starts, last_day, gaps, skips = Counter(), {}, Counter(), Counter()
    for p in result.games:
        day = p.scheduled.day
        for line in p.result.pitchers:
            if line.role != "starter":
                continue
            starts[line.pitcher_id] += 1
            if line.pitcher_id in last_day:
                gaps[day - last_day[line.pitcher_id]] += 1
            last_day[line.pitcher_id] = day
        for team_id, skipped in p.starter_skipped.items():
            skips[team_id] += int(skipped)
    return starts, gaps, skips


def season_summary(result: SeasonResult) -> dict[str, float]:
    games = [p.result for p in result.games]
    s = summarize(games)
    sds = pct_sd(result)
    _, _, skips = starter_usage(result)
    return {
        "試合数": len(games),
        "得点(1チーム1試合)": s["得点(1チーム1試合)"],
        "ホームの勝率(引き分け除く)": home_win_rate(result),
        "引き分け率": s["引き分け率"],
        "チーム勝率の標準偏差(各リーグ)": list(sds.values()),
        "先発を飛ばした割合": sum(skips.values()) / (2 * len(games)),
    }


def build_season_report(season: Season, result: SeasonResult) -> str:
    names = {t.id: t.name for t in season.league.teams}
    league_names = season.league.league_names
    out = ["# シーズンの集計(確認用)", f"- シード: {season.seed} / 試合数: {len(result.games)} / 日数: {season.total_days}"]

    for i, rows in result.standings.items():
        champ = "・".join(names[t] for t in result.champions[i])
        table = [
            [
                str(r.rank),
                r.name,
                str(r.games),
                str(r.wins),
                str(r.losses),
                str(r.ties),
                f"{r.pct:.3f}".lstrip("0") if r.pct < 1 else "1.000",
                "-" if r.games_behind == 0 and r.rank == 1 else f"{r.games_behind:.1f}",
                "-" if r.rank == 1 and r.games_behind_prev == 0 else f"{r.games_behind_prev:.1f}",
            ]
            for r in rows
        ]
        out.append(
            f"## 最終順位表:{league_names[i]}(優勝 {champ})\n"
            + _table(["順位", "チーム", "試合", "勝", "敗", "分", "勝率", "首位との差", "1つ上との差"], table)
        )

    counts = home_away_counts(season)
    home_rec = Counter()
    for p in result.games:
        r = p.result
        if not r.tie and r.winner == r.home_team_id:
            home_rec[(r.home_team_id, "w")] += 1
        elif not r.tie:
            home_rec[(r.home_team_id, "l")] += 1
    rows = [
        [names[t.id], str(counts[t.id][0]), str(counts[t.id][1]), f"{home_rec[(t.id, 'w')]}勝{home_rec[(t.id, 'l')]}敗"]
        for t in season.league.teams
    ]
    out.append("## 各チームのホーム・アウェイの試合数\n" + _table(["チーム", "ホーム", "アウェイ", "ホームでの成績"], rows))

    games = [p.result for p in result.games]
    s = summarize(games)
    sds = pct_sd(result)
    rows = []
    for key in ("得点(1チーム1試合)", "併殺(1チーム1試合)", "犠牲フライ(1チーム1試合)", "先発の平均投球回", "引き分け率"):
        lo, hi = GAME_TARGETS[key]
        v = s[key]
        fmt = (lambda x: f"{100 * x:.1f}%") if "率" in key else (lambda x: f"{x:.2f}")
        rows.append([key, fmt(v), f"{fmt(lo)}〜{fmt(hi)}", "○" if lo <= v <= hi else "×"])
    hw = home_win_rate(result)
    lo, hi = SEASON_TARGETS["ホームの勝率(引き分け除く)"]
    rows.append(["ホームの勝率(引き分け除く)", f"{100 * hw:.1f}%", f"{100 * lo:.0f}%〜{100 * hi:.0f}%", "○" if lo <= hw <= hi else "×"])
    lo, hi = SEASON_TARGETS["チーム勝率の標準偏差"]
    for i, sd in sds.items():
        rows.append([f"チーム勝率の標準偏差({league_names[i]})", f"{sd:.3f}", f"{lo:.2f}〜{hi:.2f}", "○" if lo <= sd <= hi else "×"])
    out.append("## 試合とシーズンの集計\n" + _table(["項目", "値", "校正の目標", "目標内"], rows))

    pcts = sorted((row.pct for rows in result.standings.values() for row in rows), reverse=True)
    out.append("## チーム勝率の分布\n- 高い順: " + "、".join(f"{p:.3f}".lstrip("0") for p in pcts))

    starts, gaps, skips = starter_usage(result)
    by_team = Counter()
    for pid, n in starts.items():
        by_team[season.players[pid].team_id] += 1
    rows = []
    for t in season.league.teams:
        team_starts = sorted((n for pid, n in starts.items() if season.players[pid].team_id == t.id), reverse=True)
        rows.append([names[t.id], str(by_team[t.id]), "・".join(map(str, team_starts)), str(skips[t.id])])
    out.append(
        "## 先発の登板数と、疲労で先発を飛ばした回数\n"
        + _table(["チーム", "先発した投手の数", "各投手の先発の回数(多い順)", "飛ばした回数"], rows)
    )
    total = sum(gaps.values())
    gap_rows = [[f"{d}日", f"{gaps[d]:,}", f"{100 * gaps[d] / total:.1f}%"] for d in sorted(gaps) if d <= 10]
    over = sum(n for d, n in gaps.items() if d > 10)
    gap_rows.append(["11日以上", f"{over:,}", f"{100 * over / total:.1f}%"])
    out.append(
        "## 先発の登板間隔(前の先発から次の先発までの日数)\n"
        f"- 疲労で先発を飛ばした回数: 合計 {sum(skips.values())} 回(先発の機会 {2 * len(games)} 回のうち {100 * sum(skips.values()) / (2 * len(games)):.1f}%)\n\n"
        + _table(["間隔", "回数", "割合"], gap_rows)
    )
    return "\n\n".join(out) + "\n"
