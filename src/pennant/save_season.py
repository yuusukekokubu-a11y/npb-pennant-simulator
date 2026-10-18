"""読み込みのうち、シーズンの状態を戻す部品(打順の計画・調子と一軍の入れ替え・二軍とけが・ポストシーズン)。

savegame.py が 800 行を超えたので分けた(二軍とけが。D-288)。
"""

from __future__ import annotations

from .save_checks import _game_from, _read_json
from .schedule import ScheduledGame
from .season import GameContext, PlayedGame, Season


def _check_plan(d, where: str, team_players: set, p) -> dict | None:
    """打順の計画の点検(D-373):型は決まったもの、スタメンは 9 つの枠に 1 人ずつ、そのチームの選手。"""
    from .game_config import STEAL_STRATEGIES
    from .manager import LINEUP_POSITIONS, LINEUP_STYLES

    if not isinstance(d, dict) or d.get("style") not in LINEUP_STYLES:
        p.add(where, f"打順の型が正しくありません(使えるもの: {' / '.join(LINEUP_STYLES)})")
        return None
    slots = d.get("slots")
    if slots is not None:
        ok = isinstance(slots, list) and len(slots) == len(LINEUP_POSITIONS) and all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x) for x in slots)
        if not ok or sorted(pos for _, pos in slots) != sorted(LINEUP_POSITIONS) or len({pid for pid, _ in slots}) != len(slots) or any(pid not in team_players for pid, _ in slots):
            p.add(f"{where}.slots", "スタメンの形が違います(9 つの守備位置に 1 人ずつ、そのチームの別々の選手)")
            return None
    out = {"style": d["style"], "slots": None if slots is None else [[pid, pos] for pid, pos in slots]}
    steal = d.get("steal", "normal")
    if steal not in STEAL_STRATEGIES:
        p.add(f"{where}.steal", f"盗塁の作戦が正しくありません(使えるもの: {' / '.join(STEAL_STRATEGIES)})")
        return None
    if steal != "normal":
        out["steal"] = steal
    if isinstance(d.get("set_day"), int):
        out["set_day"] = d["set_day"]
    n = d.get("notice")
    if isinstance(n, dict) and isinstance(n.get("day"), int) and n.get("reason") in ("review", "roster"):
        out["notice"] = {"day": n["day"], "reason": n["reason"]}
    return out


def _restore_roster(season: Season, season_d: dict, p) -> None:
    """調子と一軍の入れ替え(D-424〜D-426):調子を始めた日・外した日・AI に任せる・一軍の顔ぶれ(移り変わりの記録の最後)。"""
    from .condition import Conditions

    cond = season_d.get("condition") or {}
    demoted = season_d.get("demoted") or {}
    auto = season_d.get("roster_auto") or {}
    start = cond.get("start", 0) if isinstance(cond, dict) else None
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        p.add("state.json.season.condition.start", f"調子を始めた日は 0 以上の整数にしてください(値: {start!r})")
        return
    if not (isinstance(demoted, dict) and all(isinstance(d, int) and not isinstance(d, bool) and d >= 0 for d in demoted.values())):
        p.add("state.json.season.demoted", "一軍から外した日の形が違います")
        return
    if not (isinstance(auto, dict) and all(tid in season.teams and isinstance(v, bool) for tid, v in auto.items())):
        p.add("state.json.season.roster_auto", "AI に任せるかの形が違います(球団 → true / false)")
        return
    legacy_form = cond.get("legacy_form") or {}
    try:
        season.legacy_form = {str(k): float(v) for k, v in legacy_form.items()}
    except (TypeError, ValueError, AttributeError):
        p.add("state.json.season.condition.legacy_form", "好調・不調の形が違います")
        return
    season.legacy_roster = bool(cond.get("legacy", False))
    season.conditions = Conditions(season.seed, season.conditions.cfg, start)
    season.demoted = {str(k): int(v) for k, v in demoted.items()}
    season.roster_auto = {str(k): bool(v) for k, v in auto.items()}
    running = season_d.get("running_start", 0)  # 版 25 以前は変換で入る(D-433)
    if isinstance(running, bool) or not isinstance(running, int) or running < 0:
        p.add("state.json.season.running_start", f"盗塁などを始めた日は 0 以上の整数にしてください(値: {running!r})")
        return
    season.running_start = running
    stars = season_d.get("allstar", [])
    held = sum(1 for d in season.schedule.allstar_days if d < season.day)
    if not (isinstance(stars, list) and all(isinstance(x, dict) and x.get("number") == i for i, x in enumerate(stars))) or len(stars) != held:
        p.add("state.json.season.allstar", f"オールスターの結果の形が違います({held} 試合分の結果が、回の順に必要です)")
        return
    season.allstar = [dict(x) for x in stars]
    for tid, team in season.teams.items():
        hist = season.active_history.get(tid)
        if hist and not season.complete:
            season.actives[tid] = season.active_on(tid, season.day)
        elif season.legacy_roster:  # 版 24:今までの選び方(総合だけ)
            season.actives[tid] = season.manager.select_active(team)
    season.apply_conditions()


def _restore_farm_injuries(season: Season, season_d: dict, p) -> None:
    """二軍の試合とけが(D-445、D-446。版 30)。"""
    log = season_d.get("injury_log") or []
    ok = isinstance(log, list) and all(isinstance(x, dict) and isinstance(x.get("player_id"), str) and isinstance(x.get("until"), int) and not isinstance(x.get("until"), bool) and x.get("team_id") in season.teams for x in log)
    if not ok:
        p.add("state.json.season.injury_log", "けがの記録の形が違います(選手・球団・戻れる日が必要です)")
        return
    season.injury_log = [dict(x) for x in log]
    season.injuries = {x["player_id"]: x for x in season.injury_log}
    farm_d = season_d.get("farm")
    if farm_d is not None:
        from .farm import Farm

        try:
            season.farm = Farm.from_dict(season, farm_d)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            p.add("state.json.season.farm", f"二軍の試合の記録の形が違います({exc})")


def _restore_postseason(season: Season, post_d, raw: bytes, year: int, p) -> None:
    """ポストシーズンの状態と試合(D-441)。日程は状態から求めるので、試合の日と組み合わせを確かめる。"""
    from .postseason import STAGE_KEYS, Postseason

    where = "state.json.season.postseason"
    cfg = season.postseason_settings()
    if cfg is None or not season.is_over or not isinstance(post_d, dict):
        p.add(where, "ポストシーズンは、設定に postseason があり、レギュラーシーズンが終わってからです")
        return
    series_d, games_d = post_d.get("series"), post_d.get("games")
    log_name = f"logs/post-{year}.jsonl"
    lines = [line for line in raw.split(b"\n") if line.strip()]
    if not isinstance(series_d, list) or not isinstance(games_d, list) or len(lines) != len(games_d):
        p.add(where, f"シリーズと試合の形が違います(試合の記録は {log_name} と同じ数)")
        return
    post = Postseason(season, cfg)
    try:
        for s in series_d:
            if s["stage"] not in STAGE_KEYS or any(t not in season.teams for t in s["teams"]) or (s["winner"] is not None and s["winner"] not in s["teams"]):
                raise ValueError("知らない球団かステージ")
            post.series.append({"stage": str(s["stage"]), "league": None if s["league"] is None else int(s["league"]), "teams": [str(t) for t in s["teams"]], "seeds": [int(x) for x in s.get("seeds", [])],
                                "wins": [int(x) for x in s["wins"]], "ties": int(s["ties"]), "start": int(s["start"]), "games": [int(x) for x in s["games"]], "winner": s["winner"], "mvp": s.get("mvp")})
    except (KeyError, TypeError, ValueError) as exc:
        p.add(f"{where}.series", f"シリーズの形が違います({exc})")
        return
    for i, (gd, raw_line) in enumerate(zip(games_d, lines)):
        row = _read_json(raw_line, f"{log_name} の {i + 1} 行目", p)
        if row is None:
            return
        try:
            result = _game_from(row["result"])
            if row["number"] != i or gd["number"] != i or (result.home_team_id, result.away_team_id) != (gd["home"], gd["away"]):
                raise ValueError("番号か組み合わせが合いません")
            context = GameContext(dict(gd["rotation"]), {k: float(v) for k, v in gd["fatigue"].items()})
            for tid, x in (gd.get("lineups") or {}).items():
                plan = _check_plan(x, f"{where}.games[{i}].lineups.{tid}", set(season.players), p)
                if plan is not None:
                    context.lineups[tid] = plan
            g = ScheduledGame(i, int(gd["day"]), season.teams[gd["home"]].league_index, gd["home"], gd["away"])
            post.games.append(PlayedGame(g, result, context, {k: bool(v) for k, v in gd["starter_skipped"].items()}))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            p.add(f"{where}.games[{i}]", f"ポストシーズンの試合の形が違います({exc})")
            return
    if sorted(n for s in post.series for n in s["games"]) != list(range(len(post.games))):
        p.add(where, "シリーズの試合と、試合の記録が合いません")
        return
    season.postseason = post
