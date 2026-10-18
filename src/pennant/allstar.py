"""オールスター(D-436)。

リーグ戦の途中の休みに、両リーグの選抜チームどうしで試合をする。
  - 選手は、そのリーグの全球団の支配下の選手から、一軍の選び方と同じ決まりで、
    基準を「総合 + 今シーズンの成績の点」にして自動で選ぶ。
  - 試合は選手の写しで行うので、成績・疲労・調子・WAR には入れない(写しの疲労は 0)。
  - 球場は、ホームのリーグの球団から乱数で選ぶ。乱数は derive_seed(シーズンのシード, "allstar:<回>")。
  - 結果は、得点・イニングごとの得点・勝ったリーグ・出た選手・MVP(勝ったリーグの、塁打 + 打点の最も多い打者。
    同じなら打順の早い方。引き分けは両リーグから)を、保存できる形(辞書)で残す。
"""

from __future__ import annotations

import copy
import random

from .ability_model import overall
from .game import simulate_game
from .models import Team
from .records import qualifying_outs, qualifying_plate_appearances, season_records

STAT_POINTS = 5.0  # 成績の点の大きさ(内部の能力の目盛り)。仮置き


def _stat_points(rec, team_games: dict[str, int]):
    """今シーズンの成績の点(-5〜+5)。打者は OPS、投手は防御率で、出場が少ないほど小さく。"""

    def points(p) -> float:
        c = rec.batters.get(p.id) if p.role == "batter" else rec.pitchers.get(p.id)
        if not c:
            return 0.0
        g = team_games.get(p.id, 0)
        if p.role == "batter":
            if not c["PA"]:
                return 0.0
            ab = c["AB"]
            obp = (c["H"] + c["BB"] + c["HBP"]) / max(1, ab + c["BB"] + c["HBP"] + c["SF"])
            slg = c["TB"] / max(1, ab)
            share = min(1.0, c["PA"] / max(1, qualifying_plate_appearances(g)))
            value = (obp + slg - 0.700) * 25.0
        else:
            if not c["OUTS"]:
                return 0.0
            era = c["ER"] * 27 / c["OUTS"]
            need = qualifying_outs(g) if c["GS"] else max(1, g)  # 救援は 1 試合 1 アウトで満たす
            share = min(1.0, c["OUTS"] / max(1, need))
            value = (3.50 - era) * 1.5
        return max(-STAT_POINTS, min(STAT_POINTS, value)) * share

    return points


def select_teams(season) -> tuple[dict[int, Team], object]:
    """リーグごとの選抜チーム(選手は写し。疲労は 0)と、成績の点の関数。"""
    rec = season_records(p.result for p in season.played)
    team_games = {p.id: rec.teams[t.id]["G"] if t.id in rec.teams else 0 for t in season.league.teams for p in t.players}
    points = _stat_points(rec, team_games)
    names = season.league.league_names
    teams = {}
    for li in season.league_indexes():
        players = copy.deepcopy([p for t in season.league.teams if t.league_index == li for p in t.players])
        for p in players:
            p.state.fatigue = 0.0
        name = names[li] if li < len(names) else f"リーグ {li + 1}"
        team = Team(id=f"AS{li + 1}", league_index=li, place=name, nickname="選抜", stadium="", players=players)
        teams[li] = team
    return teams, points


def play(season, number: int, day: int) -> dict:
    """number 回目(0 から)のオールスターを行い、結果の辞書を返す。"""
    from .season import derive_seed

    teams, points = select_teams(season)
    idx = sorted(teams)
    home_li = idx[number % len(idx)]
    away_li = next(i for i in idx if i != home_li)
    rng = random.Random(derive_seed(season.seed, f"allstar:{number}"))
    hosts = [t for t in season.league.teams if t.league_index == home_li]
    host = hosts[rng.randrange(len(hosts))]
    setups = {}
    for li in (home_li, away_li):
        t = teams[li]
        active = season.manager.select_active(t, lambda p: overall(p) + points(p))
        setups[li], _ = season.manager.prepare(t, rng, 0, active, None)
    result = simulate_game(setups[home_li], setups[away_li], rng, model=season.model, config=season.game_config, manager=season.manager, park=host.park)
    runs = {home_li: result.home_runs, away_li: result.away_runs}
    winner = None if result.tie else (home_li if result.home_runs > result.away_runs else away_li)
    owner = {p.id: t.id for t in season.league.teams for p in t.players}
    names = {p.id: p.name for t in teams.values() for p in t.players}
    players = {}
    for li in (home_li, away_li):
        a = setups[li].active
        players[str(li)] = [{"id": p.id, "name": names[p.id], "team_id": owner.get(p.id), "role": p.role, "position": p.position} for p in a.batters + a.starters + a.relievers]
    return {
        "number": number,
        "day": int(day),
        "home_league": home_li,
        "away_league": away_li,
        "park_team_id": host.id,
        "runs": {str(k): v for k, v in runs.items()},
        "line": {"home": list(result.line[result.home_team_id]), "away": list(result.line[result.away_team_id])},
        "innings": result.innings,
        "winner": winner,
        "mvp": _mvp(result, teams, winner, owner, names),
        "players": players,
    }


def _mvp(result, teams, winner, owner, names) -> list[dict]:
    rec = season_records([result])
    leagues = [winner] if winner is not None else sorted(teams)
    out = []
    for li in leagues:
        tid = teams[li].id
        order = {pid: slot for slot, pid, _, _ in result.lineups[tid]}
        best = sorted((pid for pid in order if pid in rec.batters), key=lambda pid: (-(rec.batters[pid]["TB"] + rec.batters[pid]["RBI"]), order[pid]))
        if best:
            pid = best[0]
            c = rec.batters[pid]
            out.append({"id": pid, "name": names[pid], "team_id": owner.get(pid), "league": li, "H": c["H"], "HR": c["HR"], "RBI": c["RBI"]})
    return out
