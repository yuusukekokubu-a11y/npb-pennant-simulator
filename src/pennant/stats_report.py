"""成績の確認用の集計(開発者向け。画面ではない)。scripts/inspect_stats.py から使う。

  - 整合チェック(元の数どうしが合っているか)
  - 規定到達者の上位(打率・本塁打・打点・OPS、防御率・勝利・セーブ・奪三振)と、チームの得点・失点
  - 真の能力(隠し情報を含む能力値)と1シーズンの成績の関係(確認用。画面には出さない)
"""

from __future__ import annotations

import statistics
from collections import Counter

from .game import GameResult
from .metrics import MetricsConfig, compute, format_value, innings_text
from .models import League
from .records import Records, qualified_batters, qualified_pitchers, qualifying_outs, qualifying_plate_appearances
from .season import SeasonResult


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _total(group: dict[str, Counter]) -> Counter:
    t = Counter()
    for c in group.values():
        t.update(c)
    return t


def consistency_checks(rec: Records, results: list[GameResult], season: SeasonResult | None = None) -> list[tuple[str, bool, str]]:
    """(項目, 合っているか, 値) の一覧。"""
    b, p, t = _total(rec.batters), _total(rec.pitchers), _total(rec.teams)
    games = len(results)
    ties = sum(1 for r in results if r.tie)
    log_outs = sum(x.outs_made for r in results for x in r.log)
    checks = [
        ("全選手の得点の合計 = チームの得点の合計", b["R"] == t["R"], f"{b['R']} / {t['R']}"),
        ("全投手の失点の合計 = チームの失点の合計", p["R"] == t["RA"], f"{p['R']} / {t['RA']}"),
        ("打者の打席の合計 = 投手の対戦打者の合計", b["PA"] == p["BF"], f"{b['PA']} / {p['BF']}"),
        ("投手のアウトの合計 = 打席ログのアウトの合計", p["OUTS"] == log_outs, f"{p['OUTS']} / {log_outs}"),
        ("勝利の合計 = 敗戦の合計 = 試合数 − 引き分け", p["W"] == p["L"] == games - ties, f"{p['W']} / {p['L']} / {games - ties}"),
        ("チームの勝利の合計 = 投手の勝利の合計", t["W"] == p["W"], f"{t['W']} / {p['W']}"),
        ("すべての投手で 自責点 ≦ 失点", all(c["ER"] <= c["R"] for c in rec.pitchers.values()), f"自責点 {p['ER']} / 失点 {p['R']}"),
        ("すべての打者で 打数 ≧ 安打", all(c["AB"] >= c["H"] for c in rec.batters.values()), f"打数 {b['AB']} / 安打 {b['H']}"),
        ("打数 = 打席 − 四球 − 死球 − 犠牲フライ", b["AB"] == b["PA"] - b["BB"] - b["HBP"] - b["SF"], f"{b['AB']}"),
        ("安打 = 単打 + 二塁打 + 三塁打 + 本塁打", b["H"] == b["B1"] + b["B2"] + b["B3"] + b["HR"], f"{b['H']}"),
    ]
    if season is not None:
        same = all(
            (rec.teams[row.team_id]["W"], rec.teams[row.team_id]["L"], rec.teams[row.team_id]["T"]) == (row.wins, row.losses, row.ties)
            for rows in season.standings.values()
            for row in rows
        )
        checks.append(("チームの勝・敗・分 = 順位表", same, "12チーム"))
    return checks


def build_stats_report(rec: Records, results: list[GameResult], league: League, config: MetricsConfig, season: SeasonResult | None = None, top: int = 10) -> str:
    players = {p.id: p for p in league.all_players()}
    teams = {t.id: t for t in league.teams}
    name = lambda pid: f"{players[pid].name}({teams[players[pid].team_id].place})"  # noqa: E731
    out = ["# 成績の集計(確認用)"]

    rows = [[label, "○" if ok else "×", value] for label, ok, value in consistency_checks(rec, results, season)]
    out.append("## 整合チェック\n" + _table(["項目", "結果", "値"], rows))

    team_games = max(c["G"] for c in rec.teams.values())
    out.append(
        f"## 規定\n- チーム試合数 {team_games}:規定打席 {qualifying_plate_appearances(team_games)}、"
        f"規定投球回 {innings_text(qualifying_outs(team_games))}\n"
        f"- 規定打席に達した打者 {len(qualified_batters(rec))} 人、規定投球回に達した投手 {len(qualified_pitchers(rec))} 人"
    )

    bats = {pid: compute(config, "batter", rec.batters[pid]) for pid in qualified_batters(rec)}
    for title, key, is_metric in (("打率", "avg", True), ("本塁打", "HR", False), ("打点", "RBI", False), ("OPS", "ops", True)):
        val = (lambda pid: bats[pid][key]) if is_metric else (lambda pid: rec.batters[pid][key])
        order = sorted(bats, key=lambda pid: (-(val(pid) or 0), pid))[:top]
        rows = [
            [str(i + 1), name(pid), format_value(config, key, val(pid)) if is_metric else str(val(pid)),
             str(rec.batters[pid]["PA"]), format_value(config, "avg", bats[pid]["avg"]), str(rec.batters[pid]["HR"]),
             str(rec.batters[pid]["RBI"]), format_value(config, "ops", bats[pid]["ops"])]
            for i, pid in enumerate(order)
        ]
        out.append(f"## 打者:{title}の上位(規定打席以上)\n" + _table(["順", "選手", title, "打席", "打率", "本塁打", "打点", "OPS"], rows))

    pits = {pid: compute(config, "pitcher", rec.pitchers[pid]) for pid in qualified_pitchers(rec)}
    for title, key, low_first, pool in (
        ("防御率", "era", True, pits),
        ("勝利", "W", False, rec.pitchers),
        ("セーブ", "SV", False, rec.pitchers),
        ("奪三振", "SO", False, pits),
    ):
        if key == "era":
            order = sorted(pool, key=lambda pid: (pits[pid]["era"], pid))[:top]
        else:
            order = sorted(pool, key=lambda pid: (-rec.pitchers[pid][key], pid))[:top]
        rows = []
        for i, pid in enumerate(order):
            c = rec.pitchers[pid]
            era = compute(config, "pitcher", c)["era"]
            rows.append(
                [str(i + 1), name(pid), format_value(config, "era", era), innings_text(c["OUTS"]),
                 f"{c['W']}勝{c['L']}敗", str(c["SV"]), str(c["HLD"]), str(c["SO"])]
            )
        cond = "規定投球回以上" if pool is pits else "全投手"
        out.append(f"## 投手:{title}の上位({cond})\n" + _table(["順", "投手", "防御率", "投球回", "勝敗", "セーブ", "ホールド", "奪三振"], rows))

    rows = []
    for tid, c in sorted(rec.teams.items(), key=lambda kv: (teams[kv[0]].league_index, -(kv[1]["W"] / max(1, kv[1]["W"] + kv[1]["L"])))):
        rows.append([teams[tid].name, str(c["G"]), f"{c['W']}勝{c['L']}敗{c['T']}分", str(c["R"]), str(c["RA"]), f"{c['R'] - c['RA']:+d}"])
    out.append("## チームの得点・失点\n" + _table(["チーム", "試合", "勝敗", "得点", "失点", "得失点差"], rows))

    out.append("## 真の能力と1シーズンの成績の関係(確認用。画面には出さない)\n" + ability_relations(rec, players, config))
    return "\n\n".join(out) + "\n"


def ability_relations(rec: Records, players: dict, config: MetricsConfig) -> str:
    """規定到達者で、能力値と成績の相関係数(−1〜1。1 に近いほど、能力が高いと成績も高い)。"""
    rows = []
    qb = qualified_batters(rec)
    qp = qualified_pitchers(rec)
    pairs = [
        ("打者のコンタクト と K%", qb, "contact", lambda c: compute(config, "batter", c)["k_pct"], "batter"),
        ("打者の選球眼 と BB%", qb, "eye", lambda c: compute(config, "batter", c)["bb_pct"], "batter"),
        ("打者の長打力 と 本塁打 ÷ 打席", qb, "power", lambda c: c["HR"] / c["PA"], "batter"),
        ("打者の打球の質 と BABIP", qb, "batted_ball_quality", lambda c: compute(config, "batter", c)["babip"], "batter"),
        ("投手の奪三振力 と K%", qp, "strikeout", lambda c: compute(config, "pitcher", c)["k_pct"], "pitcher"),
        ("投手の制球力 と BB%", qp, "control", lambda c: compute(config, "pitcher", c)["bb_pct"], "pitcher"),
    ]
    for label, ids, item, stat, role in pairs:
        group = rec.batters if role == "batter" else rec.pitchers
        xs = [players[pid].ratings[item] for pid in ids]
        ys = [float(stat(group[pid])) for pid in ids]
        r = statistics.correlation(xs, ys) if len(ids) > 2 else float("nan")
        rows.append([label, str(len(ids)), f"{r:+.2f}"])
    return _table(["組み合わせ", "人数", "相関係数"], rows)
