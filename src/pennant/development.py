"""育成選手(D-375):育成ドラフト・支配下に上げる・AI の判断。

- 育成選手は Team.development に入る(支配下 70 人とは別の枠。上限は settings.development["max"])。試合に出ない。
- 育成ドラフト(phase "dev_draft"):ドラフトの後、指名されなかった候補から、前年の順位が低い球団から往復で指名する。
  年俸は育成の年俸(固定)、契約は育成の上限のシーズン数(更改しない)。空き(上限)がない・資金が足りない球団は飛ばす。
  AI は育成が目安(ai_target)より少なければ、ai_max_age 歳以下の候補から点数の高い候補を指名する。
- 支配下に上げる(promote):70 人に空きがあれば、育成選手を支配下に移す(年俸は最低年俸、契約 1 年)。
  あなたは市場の段階で選ぶ。AI は市場を締めるときに ai_promote、オフの終わりの最低人数の補充(draft.finalize)は take_for_slot で先に上げる。
- 育成で上限のシーズン数を過ごした選手は、年度の確定で自由契約(offseason.age_update_retire)。
"""

from __future__ import annotations

from .contracts import set_contract, team_salary
from .models import League, Player, Team
from .procedure import (
    MAX_ROSTER,
    DraftSettings,
    OffseasonProcedure,
    _entry_report,
    advance_turn,
    cached_value,
    entry_summary,
    pass_turn,
    phase_finished,
)


def dev_room(team: Team, settings: DraftSettings) -> int:
    return int(settings.development["max"]) - len(team.development)


def dev_blocked(team: Team, settings: DraftSettings, ctx) -> str | None:
    """育成ドラフトで指名できない理由(full:育成の上限)。指名できれば None。
    育成の年俸は低い固定の額なので、資金を超えていても結べる(D-235、D-375)。"""
    if dev_room(team, settings) <= 0:
        return "full"
    return None


def dev_pick(proc: OffseasonProcedure, team: Team, player: Player, sd: dict, settings: DraftSettings, ctx) -> None:
    """育成ドラフトで指名する(その時点で育成選手として入団)。"""
    proc.candidates.remove(player)
    player.team_id = team.id
    player.scouting = _entry_report(proc, player, team.id, sd, settings)
    player.state.fatigue = 0.0
    player.dev_seasons = 0
    salary = int(settings.development["salary"])
    if ctx is not None:
        set_contract(player, salary, int(settings.development["seasons"]), proc.year + 1, "development")
    team.development.append(player)
    proc.picks.append({"phase": "dev_draft", "round": proc.round, "team_id": team.id, "player_id": player.id, "name": player.name, "role": player.role, "position": player.position, "age": player.age, "salary": salary, **entry_summary(player.scouting)})
    advance_turn(proc)


def ai_dev_choose(team: Team, proc: OffseasonProcedure, sd: dict, settings: DraftSettings) -> Player | None:
    """AI の育成ドラフトの指名:育成が目安より少なければ、若い候補から総合の推定値 + 評価の加点の高い候補。"""
    d = settings.development
    if len(team.development) >= min(int(d["ai_target"]), int(d["max"])):
        return None
    pool = [p for p in proc.candidates if p.age <= int(d["ai_max_age"])]
    if not pool:
        return None

    def score(p):
        est, grade = cached_value(proc, p, team.id, sd)
        return est + float(settings.ai("grade_bonus")[grade])

    return max(pool, key=lambda p: (score(p), p.id))


def run_dev_turns(league: League, proc: OffseasonProcedure, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, ctx, my_ai: bool = False) -> bool:
    """育成ドラフトの AI の番を進める。あなたの番が来たら True で止まる。終われば False。"""
    teams = {t.id: t for t in league.teams}
    while not phase_finished(proc):
        tid = proc.current_team()
        if tid is None:
            advance_turn(proc)
            continue
        team = teams[tid]
        why = dev_blocked(team, settings, ctx)
        if why:
            pass_turn(proc, tid, why)
            continue
        if tid == my_team_id and not my_ai:
            return True
        choice = ai_dev_choose(team, proc, scout_sd[tid], settings)
        if choice is None:
            pass_turn(proc, tid, "skip")
        else:
            dev_pick(proc, team, choice, scout_sd[tid], settings, ctx)
    return False


def promote(team: Team, player: Player, proc: OffseasonProcedure, ctx, by: str) -> None:
    """育成選手を支配下に上げる(年俸は最低年俸、契約 1 年)。by は you / ai / fill。"""
    if len(team.players) >= MAX_ROSTER:
        raise ValueError("支配下の枠(70 人)に空きがありません")
    team.development.remove(player)
    player.dev_seasons = None
    if ctx is not None:
        set_contract(player, int(ctx.settings.minimum), 1, proc.year + 1, "promote")
    team.players.append(player)
    proc.promotions.append({"team_id": team.id, "player_id": player.id, "name": player.name, "role": player.role, "position": player.position, "age": player.age, "by": by})


def promote_fits(team: Team, player: Player, ctx) -> bool:
    """支配下に上げても資金の中か(育成の年俸が最低年俸に変わる差だけ増える)。最低人数の補充で上げるときは見ない(D-235、D-391)。"""
    cap = ctx.cap(team.id) if ctx is not None else None
    if ctx is None or not ctx.hard() or cap is None:
        return True
    now = int(player.contract["salary"]) if player.contract else 0
    return team_salary(team) - now + int(ctx.settings.minimum) <= cap


def ai_promote(team: Team, proc: OffseasonProcedure, sd: dict, ctx) -> list[Player]:
    """AI:空きがあれば、支配下の最も低い選手(推定値)より高い育成選手を、高い順に上げる。そのオフに指名した選手は上げない(育成で 1 シーズン以上)。"""
    out = []
    while len(team.players) < MAX_ROSTER:
        pool = [p for p in team.development if (p.dev_seasons or 0) >= 1 and promote_fits(team, p, ctx)]
        if not pool:
            break
        best = max(pool, key=lambda p: (cached_value(proc, p, team.id, sd)[0], p.id))
        worst = min((cached_value(proc, p, team.id, sd)[0] for p in team.players), default=0.0)
        if cached_value(proc, best, team.id, sd)[0] < worst:
            break
        promote(team, best, proc, ctx, "ai")
        out.append(best)
    return out


def take_for_slot(team: Team, role: str, position: str, proc: OffseasonProcedure, sd: dict, ctx) -> Player | None:
    """最低人数の補充の 1 枠を、同じポジションの育成選手(推定値の高い順)で埋める。いなければ None。"""
    pool = [p for p in team.development if p.role == role and p.position == position]
    if not pool:
        return None
    best = max(pool, key=lambda p: (cached_value(proc, p, team.id, sd)[0], p.id))
    promote(team, best, proc, ctx, "fill")
    return best
