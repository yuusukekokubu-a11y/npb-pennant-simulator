"""試合の集計と、1試合の流れの文章化(開発者向けの確認用。画面ではない)。

試合を続けて回すときは、日ごとに「各リーグの6球団を3組に分けて対戦 → 疲労を加算 → 1日進める(回復)」を繰り返す。
本当の日程(シーズン)は次のステップで作る。ここでは確認用の簡易な組み合わせ。
"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from typing import Iterable

from .abilities import POSITION_LABELS
from .fatigue import advance_day, apply_game_fatigue
from .game import BOTTOM, GameResult, simulate_game
from .game_config import GameConfig
from .manager import DH, RELIEF_ROLE_LABELS, SimpleManager
from .models import League, Player
from .pa_stats import count_hits_by_position, fmt_rate, hits_by_position_table, rates_from
from .plate_appearance import PlateAppearanceModel

# 校正の目標(一般的な水準の目安。依頼「実装③:1試合の進行」で示された範囲)
GAME_TARGETS = {
    "得点(1チーム1試合)": (3.8, 4.8),
    "併殺(1チーム1試合)": (0.5, 1.0),
    "犠牲フライ(1チーム1試合)": (0.08, 0.25),
    "先発の平均投球回": (5.0, 6.5),
    "引き分け率": (0.01, 0.05),
}


def play_games(
    league: League,
    n_games: int,
    seed: int,
    config: GameConfig,
    model: PlateAppearanceModel | None = None,
    manager: SimpleManager | None = None,
) -> list[GameResult]:
    """リーグの球団どうしで n_games 試合を回す(疲労と回復も進める)。選手の疲労は書き換わる。"""
    manager = manager or SimpleManager(config)
    rng = random.Random(seed)
    players = {p.id: p for p in league.all_players()}
    actives = {t.id: manager.select_active(t) for t in league.teams}
    rotation = {t.id: 0 for t in league.teams}
    pitchers = [p for a in actives.values() for p in a.starters + a.relievers]
    groups: dict[int, list] = {}
    for t in league.teams:
        groups.setdefault(t.league_index, []).append(t)
    results: list[GameResult] = []
    while len(results) < n_games:
        for teams in groups.values():
            order = list(teams)
            rng.shuffle(order)
            for home, away in zip(order[0::2], order[1::2]):
                if len(results) >= n_games:
                    break
                hs, rotation[home.id] = manager.prepare(home, rng, rotation[home.id], actives[home.id])
                aw, rotation[away.id] = manager.prepare(away, rng, rotation[away.id], actives[away.id])
                result = simulate_game(hs, aw, rng, model=model, config=config, manager=manager, park=home.park)
                apply_game_fatigue(result.batters_faced(), players, config)
                results.append(result)
        advance_day(pitchers, config)
    return results


def summarize(results: list[GameResult]) -> dict[str, float]:
    team_games = 2 * len(results)
    starters = [p for r in results for p in r.pitchers if p.role == "starter"]
    relievers = [p for r in results for p in r.pitchers if p.role == "reliever"]
    log = [x for r in results for x in r.log]
    subs = sum(1 for r in results for slots in r.lineups.values() for s in slots if s[3] is not None)
    sub_pa = 0
    for r in results:
        sub_slots = {(team, s[0]) for team, slots in r.lineups.items() for s in slots if s[3] is not None}
        sub_pa += sum(1 for x in log_of(r) if (x.batting_team_id, x.lineup_slot) in sub_slots)
    return {
        "試合数": len(results),
        "得点(1チーム1試合)": sum(r.home_runs + r.away_runs for r in results) / team_games,
        "併殺(1チーム1試合)": sum(1 for x in log if x.double_play) / team_games,
        "犠牲フライ(1チーム1試合)": sum(1 for x in log if x.sac_fly) / team_games,
        "先発の平均投球回": statistics.fmean(p.innings_pitched for p in starters),
        "完投の割合": sum(1 for p in starters if p.exit_reason == "game_end") / len(starters),
        "先発が失点の上限で降板した割合": sum(1 for p in starters if p.exit_reason == "run_limit") / len(starters),
        "救援の登板数(1チーム1試合)": len(relievers) / team_games,
        "引き分け率": sum(1 for r in results if r.tie) / len(results),
        "延長の割合": sum(1 for r in results if r.extra_innings) / len(results),
        "サヨナラの割合": sum(1 for r in results if r.walkoff) / len(results),
        "9回裏を行わなかった割合": sum(1 for r in results if any(v is None for v in r.line[r.home_team_id])) / len(results),
        "平均イニング数": statistics.fmean(r.innings for r in results),
        "ホームの勝率(引き分け除く)": _home_win_rate(results),
        "休養で控えが入った人数(1チーム1試合)": subs / team_games,
        "控えの打席の割合": sub_pa / len(log),
        "打席数(1チーム1試合)": len(log) / team_games,
    }


def log_of(result: GameResult):
    return result.log


def _home_win_rate(results: list[GameResult]) -> float:
    decided = [r for r in results if not r.tie]
    return sum(1 for r in decided if r.winner == r.home_team_id) / len(decided) if decided else float("nan")


def pa_rates(results: Iterable[GameResult]) -> dict[str, float]:
    return rates_from(Counter(x.pa.result for r in results for x in r.log))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _fmt(key: str, v: float) -> str:
    if key in ("試合数",):
        return f"{int(v):,}"
    if "割合" in key or "率" in key:
        return f"{100 * v:.1f}%"
    return f"{v:.2f}"


def build_game_report(results: list[GameResult], league: League) -> str:
    out = ["# 試合の集計(確認用)"]
    s = summarize(results)
    rows = []
    for key, value in s.items():
        target = GAME_TARGETS.get(key)
        if target:
            ok = "○" if target[0] <= value <= target[1] else "×"
            rows.append([key, _fmt(key, value), f"{_fmt(key, target[0])}〜{_fmt(key, target[1])}", ok])
        else:
            rows.append([key, _fmt(key, value), "-", "-"])
    out.append("## 試合単位の集計\n" + _table(["項目", "値", "校正の目標", "目標内"], rows))

    rates = pa_rates(results)
    keys = ("K%", "BB%", "HBP%", "HR%", "BABIP", "失策率(インプレーあたり)", "打率", "出塁率", "長打率", "OPS")
    out.append(
        "## 打席の結果の割合(試合を通して)\n" + _table(["項目", "値"], [[k, fmt_rate(k, rates[k])] for k in keys])
    )

    out.append(
        "## 担当ポジション別の安打の内訳(試合を通して。D-072)\n"
        + hits_by_position_table(count_hits_by_position(x.pa for r in results for x in r.log))
    )

    runs = Counter(min(n, 10) for r in results for n in (r.home_runs, r.away_runs))
    total = sum(runs.values())
    out.append(
        "## 1チーム1試合の得点の分布\n"
        + _table(["得点", "割合"], [[f"{k}{'点以上' if k == 10 else '点'}", f"{100 * runs[k] / total:.1f}%"] for k in range(11)])
    )

    ip = Counter(min(int(p.innings_pitched), 9) for r in results for p in r.pitchers if p.role == "starter")
    total = sum(ip.values())
    out.append(
        "## 先発の投球回の分布(整数部分)\n"
        + _table(["投球回", "割合"], [[f"{k}回{'(完投相当)' if k == 9 else ''}", f"{100 * ip[k] / total:.1f}%"] for k in range(10)])
    )
    return "\n\n".join(out) + "\n"


# ---- 1試合の流れを文章に ----

POS_SHORT = {"P": "投", "C": "捕", "1B": "一", "2B": "二", "3B": "三", "SS": "遊", "LF": "左", "CF": "中", "RF": "右"}
RESULT_TEXT = {
    "strikeout": "三振",
    "walk": "四球",
    "hit_by_pitch": "死球",
    "home_run": "本塁打",
    "ground_out": "ゴロ",
    "line_out": "直",
    "fly_out": "飛",
    "single": "安",
    "double": "二塁打",
    "triple": "三塁打",
    "error": "失策",
}
BASE_TEXT = {1: "一塁", 2: "二塁", 3: "三塁"}
LINE_TEXT = {"1B": "一塁線", "3B": "三塁線"}


def describe(x) -> str:
    r = x.pa.result
    if x.double_play:
        return f"{POS_SHORT[x.pa.fielder]}ゴロ併殺打"
    if x.sac_fly:
        return f"{POS_SHORT[x.pa.fielder]}犠飛"
    if r in ("ground_out", "line_out", "fly_out", "single", "error"):
        return f"{POS_SHORT[x.pa.fielder]}{RESULT_TEXT[r]}"
    if r in ("double", "triple"):
        if x.pa.fielder in LINE_TEXT:  # 一塁手・三塁手の担当の二塁打は、線を抜けた打球(D-072)
            return f"{LINE_TEXT[x.pa.fielder]}{RESULT_TEXT[r]}"
        return f"{POS_SHORT[x.pa.fielder]}{RESULT_TEXT[r]}"
    return RESULT_TEXT[r]


def _runners_text(state) -> str:
    on = [BASE_TEXT[i + 1] for i, flag in enumerate((state.first, state.second, state.third)) if flag]
    outs = "無死一死二死"[state.outs * 2 : state.outs * 2 + 2]
    return f"{outs}{'・'.join(on) if on else '走者なし'}"


EXIT_REASON_TEXT = {"batters_limit": "打者数の上限", "run_limit": "失点の上限", "inning_end": "イニング終了", "game_end": "試合終了"}


def game_story(result: GameResult, players: dict[str, Player], team_names: dict[str, str], decisions=None) -> dict:
    """1試合の流れを、画面でも文章でも使える構造のデータにする(最小のブラウザ画面②。D-114)。

    文章(narrate)は、この構造から作る。画面の文章ログと、確認用スクリプトの文章が同じ内容になる。
    decisions(勝敗・セーブ・ホールド)を渡すと、投手の成績に結果の印を付ける。
    """
    from .decisions import decide
    from .records import earned_flags

    home, away = result.home_team_id, result.away_team_id
    d = decisions or decide(result)
    marks: dict[str, str] = {}
    if d.win:
        marks[d.win] = "勝"
    if d.loss:
        marks[d.loss] = "敗"
    if d.save:
        marks[d.save] = "S"
    for pid in d.holds:
        marks[pid] = "H"
    earned: dict[str, int] = {}
    for x, row in zip(result.log, earned_flags(result)):
        for m, is_earned in zip([m for m in x.moves if m.scored], row):
            if is_earned:
                earned[m.responsible_pitcher_id] = earned.get(m.responsible_pitcher_id, 0) + 1
    hits: dict[str, int] = {}
    for x in result.log:
        if x.pa.result in ("single", "double", "triple", "home_run"):
            hits[x.pitcher_id] = hits.get(x.pitcher_id, 0) + 1

    teams = []
    for team in (away, home):
        starter = next(p for p in result.pitchers if p.team_id == team and p.role == "starter")
        teams.append(
            {
                "team_id": team,
                "name": team_names[team],
                "side": "先攻" if team == away else "後攻",
                "starter": {"id": starter.pitcher_id, "name": players[starter.pitcher_id].name},
                "lineup": [
                    {
                        "order": o,
                        "id": pid,
                        "name": players[pid].name,
                        "position": "指名打者" if pos == DH else POSITION_LABELS[pos],
                        "rest_sub": rep is not None,
                    }
                    for o, pid, pos, rep in result.lineups[team]
                ],
            }
        )

    halves: list[dict] = []
    pitcher_now: dict[str, str] = {}
    score = {home: 0, away: 0}
    for x in result.log:
        key = (x.inning, x.half)
        if not halves or (halves[-1]["inning"], halves[-1]["half"]) != key:
            halves.append(
                {
                    "inning": x.inning,
                    "half": x.half,
                    "title": f"{x.inning}回{'裏' if x.half == BOTTOM else '表'}",
                    "batting_team": team_names[x.batting_team_id],
                    "events": [],
                }
            )
        events = halves[-1]["events"]
        if pitcher_now.get(x.fielding_team_id) not in (None, x.pitcher_id):
            events.append({"type": "pitching_change", "team": team_names[x.fielding_team_id], "pitcher_id": x.pitcher_id, "pitcher": players[x.pitcher_id].name})
        pitcher_now[x.fielding_team_id] = x.pitcher_id
        score[x.batting_team_id] += x.runs
        events.append(
            {
                "type": "plate_appearance",
                "situation": _runners_text(x.base_out),
                "order": x.lineup_slot,
                "batter_id": x.batter_id,
                "batter": players[x.batter_id].name,
                "result": describe(x),
                "runs": x.runs,
                "score": {"away": score[away], "home": score[home]},
            }
        )

    n = max(len(result.line[home]), len(result.line[away]))
    line = {
        "innings": [str(i + 1) for i in range(n)],
        "rows": [
            {
                "team_id": team,
                "name": team_names[team],
                "cells": [("X" if v is None else str(v)) for v in result.line[team]] + [""] * (n - len(result.line[team])),
                "total": result.away_runs if team == away else result.home_runs,
            }
            for team in (away, home)
        ],
    }
    tags = []
    if result.tie:
        tags.append("引き分け")
    if result.extra_innings:
        tags.append("延長")
    if result.walkoff:
        tags.append("サヨナラ")
    pitchers = [
        {
            "team_id": p.team_id,
            "team": team_names[p.team_id],
            "id": p.pitcher_id,
            "name": players[p.pitcher_id].name,
            "role": "先発" if p.role == "starter" else "救援",
            "innings": f"{p.outs // 3}回{'' if p.outs % 3 == 0 else f' {p.outs % 3}/3'}",
            "batters_faced": p.batters_faced,
            "hits": hits.get(p.pitcher_id, 0),
            "runs": p.runs,
            "earned_runs": earned.get(p.pitcher_id, 0),
            "exit_reason": EXIT_REASON_TEXT.get(p.exit_reason, "-"),
            "decision": marks.get(p.pitcher_id, ""),
        }
        for p in result.pitchers
    ]
    return {
        "away": {"team_id": away, "name": team_names[away], "runs": result.away_runs},
        "home": {"team_id": home, "name": team_names[home], "runs": result.home_runs},
        "teams": teams,
        "halves": halves,
        "line": line,
        "tags": tags,
        "pitchers": pitchers,
    }


def story_text(story: dict) -> str:
    """game_story の構造を、確認用の文章(Markdown)にする。"""
    away, home = story["away"]["name"], story["home"]["name"]
    lines = [f"# {away}(先攻) 対 {home}(後攻)"]
    for team in story["teams"]:
        order = "、".join(
            f"{s['order']}番 {s['name']}({s['position']})" + ("※休養の代わり" if s["rest_sub"] else "") for s in team["lineup"]
        )
        lines.append(f"- {team['name']}:先発 {team['starter']['name']}\n  - 打順:{order}")
    for half in story["halves"]:
        lines.append(f"\n## {half['title']}({half['batting_team']}の攻撃)")
        for e in half["events"]:
            if e["type"] == "pitching_change":
                lines.append(f"  - 【投手交代】{e['team']}:{e['pitcher']}")
                continue
            extra = f" → {e['runs']}点(スコア {away} {e['score']['away']} - {e['score']['home']} {home})" if e["runs"] else ""
            lines.append(f"- [{e['situation']}] {e['order']}番 {e['batter']}:{e['result']}{extra}")
    lines.append("\n## スコア")
    header = ["チーム"] + story["line"]["innings"] + ["計"]
    lines.append(_table(header, [[r["name"]] + r["cells"] + [str(r["total"])] for r in story["line"]["rows"]]))
    if story["tags"]:
        lines.append(f"\n({'・'.join(story['tags'])})")
    lines.append("\n## 投手")
    rows = [[p["team"], p["name"], p["role"], p["innings"], str(p["batters_faced"]), str(p["runs"]), p["exit_reason"]] for p in story["pitchers"]]
    lines.append(_table(["チーム", "投手", "区分", "投球回", "対戦打者", "失点", "降板の理由"], rows))
    return "\n".join(lines) + "\n"


def narrate(result: GameResult, players: dict[str, Player], team_names: dict[str, str]) -> str:
    """1試合の流れ(各打席の結果とイニングごとの得点)を文章にする。"""
    return story_text(game_story(result, players, team_names))
