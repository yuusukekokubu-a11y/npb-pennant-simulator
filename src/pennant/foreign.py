"""外国人市場(D-376):オフの手続きの「外国人」の段階(FA の後・ドラフトの前)。

- 候補は procedure.make_foreign_candidates で毎年のオフに作る。見せるのは評価(A〜E)と一言だけ(外国人の候補の中での評価)。
- 年俸は評価の段階の表(proc.foreign_salary。球団によらない「市場の見立て」の評価)、契約は 1 年。選手は断らない。
- 各球団が獲得の希望を出す。あなたは proc.foreign_offers(入れた順が優先)。AI は外国人が目安(ai_target)より少なければ、
  点数(総合の推定値 + 評価の加点 + 足りないポジションの加点)の高い候補を ai_wishes 人まで(目安との差まで)希望する。
- 締める(close_foreign):自由契約市場と同じく、前年の順位が低い球団から 1 人ずつ、希望のうち残っている最初の候補を獲得する。
  空き枠(70 人)がない球団は飛ばし、資金を超える候補は希望から外す。決まらなかった候補はリーグを去る。
"""

from __future__ import annotations

from .contracts import set_contract, team_salary
from .models import League, Team
from .procedure import MAX_ROSTER, DraftSettings, OffseasonProcedure, ai_value, entry_summary, scout_report_foreign, shortages


def foreign_count(team: Team) -> int:
    return sum(1 for p in team.players if p.foreign)


def can_take(team: Team, proc: OffseasonProcedure, player_id: str, ctx) -> bool:
    """空き枠があり、資金の中で結べるか。"""
    if len(team.players) >= MAX_ROSTER:
        return False
    cap = ctx.cap(team.id) if ctx is not None else None
    return not (ctx is not None and ctx.hard() and cap is not None and team_salary(team) + int(proc.foreign_salary.get(player_id, 0)) > cap)


def ai_foreign_wishes(team: Team, proc: OffseasonProcedure, sd: dict, settings: DraftSettings, ctx, mins, min_batters) -> list[str]:
    """AI の希望(選手 ID の並び。前ほど優先)。"""
    s = settings.foreign
    want = min(int(s["ai_wishes"]), int(s["ai_target"]) - foreign_count(team), MAX_ROSTER - len(team.players))
    if want <= 0:
        return []
    need = shortages(team.players, mins, min_batters)
    pool = [p for p in proc.foreign_pool if can_take(team, proc, p.id, ctx)]
    ranked = sorted(pool, key=lambda p: (-ai_value(p, team, proc, sd, settings, need), p.id))
    return [p.id for p in ranked[:want]]


def join_foreign(team: Team, player, proc: OffseasonProcedure, sd: dict, settings: DraftSettings) -> None:
    player.team_id = team.id
    d = scout_report_foreign(proc, player, team.id, sd, settings).to_dict()
    d["year"] = None if proc.prerun else proc.year + 1
    player.scouting = d
    player.state.fatigue = 0.0
    team.players.append(player)


def close_foreign(league: League, proc: OffseasonProcedure, ctx, my_team_id: str | None, scout_sd: dict[str, dict], settings: DraftSettings, mins, min_batters, my_ai: bool = False) -> list[dict]:
    """外国人市場を締める。my_ai=True なら、あなたの球団も AI と同じ方針。戻り値は成立した契約。"""
    teams = {t.id: t for t in league.teams}
    wishes: dict[str, list[str]] = {}
    for team in league.teams:
        if team.id == my_team_id and not my_ai:
            wishes[team.id] = [pid for pid in proc.foreign_offers]
        else:
            wishes[team.id] = ai_foreign_wishes(team, proc, scout_sd[team.id], settings, ctx, mins, min_batters)
    wanted: dict[str, int] = {}
    for lst in wishes.values():
        for pid in lst:
            wanted[pid] = wanted.get(pid, 0) + 1
    by_id = {p.id: p for p in proc.foreign_pool}
    signed: list[dict] = []
    rnd = 1
    while True:
        took = False
        order = proc.order if rnd % 2 == 1 else list(reversed(proc.order))
        for tid in order:
            team = teams[tid]
            lst = wishes.get(tid, [])
            while lst:
                pid = lst.pop(0)
                p = by_id.get(pid)
                if p is None or p not in proc.foreign_pool or not can_take(team, proc, pid, ctx):
                    continue
                salary = int(proc.foreign_salary.get(pid, 0))
                proc.foreign_pool.remove(p)
                join_foreign(team, p, proc, scout_sd[tid], settings)
                if ctx is not None:
                    set_contract(p, salary, 1, proc.year + 1, "foreign")
                proc.picks.append({"phase": "foreign", "round": rnd, "team_id": tid, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "salary": salary, "years": 1, **entry_summary(p.scouting)})
                result = {"round": rnd, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "team_id": tid, "years": 1, "salary": salary, "offers": wanted.get(pid, 0), **entry_summary(p.scouting)}
                proc.foreign_results.append(result)
                signed.append(result)
                took = True
                break
        if not took:
            break
        rnd += 1
    proc.foreign_offers = {}
    proc.foreign_pool = []  # 決まらなかった候補はリーグを去る
    proc.foreign_done = True
    return signed
