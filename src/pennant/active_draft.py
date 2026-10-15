"""現役ドラフト(D-380):全球団が 2 人ずつ候補を出し、前年の順位が低い球団から 1 人ずつ指名する。どの球団も 1 人出て 1 人入る。

- 出せる選手(eligible):そのシーズンの最後の一軍(OffseasonProcedure.season_actives)に入っていなかった支配下の選手。
  外国人・このオフに入った選手(trade.locked_ids)・年俸が最低年俸 × 上限の倍率を超える選手・出すと最低人数を割る選手は出せない。
  一軍の記録がない手続き(事前運転)は、見込みの WAR の上位(一軍の人数)を一軍とみなす。
- AI の候補(ai_offers):出せる選手のうち価値(trade.player_value)の低い順に 2 人。
- 指名(run_turns):順番は proc.order(前年の順位が低い球団から)。指名できるのは、まだ選手を取られていない、ほかの球団の候補だけ。
  最後の 1 球団が自分の候補しか選べない形になる指名はしない。AI は 価値 + 手薄なポジションの加点(trade.need_bonus)の最も高い選手。
- 契約(年俸・年数)はそのまま。1 人出て 1 人入るので、資金の決まりは当てない。
"""

from __future__ import annotations

from .models import League, Player, Team
from .procedure import can_release
from .trade import default_settings, locked_ids, need_bonus, player_value

DEFAULT = {"offers": 2, "max_salary_ratio": 3.0, "active_size": 29}


def settings_of(trade_settings=None) -> dict:
    """現役ドラフトの設定(trade.json の active_draft。足りない項目は既定値)。"""
    s = trade_settings or default_settings()
    return {**DEFAULT, **(s.data.get("active_draft") or {})}


def _value(p: Player, ctx, rate: float, ts) -> float:
    return player_value(p, ctx, rate, ts)


def first_team_ids(league: League, proc, ctx, trade_settings=None) -> set[str]:
    """一軍とみなす選手:終わったシーズンの最後の一軍。記録がなければ(事前運転)、各球団の見込みの WAR の上位(一軍の人数)。"""
    if proc.season_actives:
        return set(proc.season_actives)
    n = int(settings_of(trade_settings)["active_size"])
    out: set[str] = set()
    for team in league.teams:
        ranked = sorted(team.players, key=lambda p: (-float(ctx.expected(p)[0]), p.id))
        out.update(p.id for p in ranked[:n])
    return out


def eligible(team: Team, proc, ctx, mins, min_batters, first_team: set[str], trade_settings=None) -> list[Player]:
    """その球団が現役ドラフトに出せる選手(ID の順)。"""
    s = settings_of(trade_settings)
    locked = locked_ids(proc)
    limit = float(s["max_salary_ratio"]) * int(ctx.settings.minimum)
    out = []
    for p in sorted(team.players, key=lambda p: p.id):
        if p.foreign or p.id in locked or p.id in first_team:
            continue
        if p.contract and int(p.contract["salary"]) > limit:
            continue
        if not can_release(team.players, p, mins, min_batters):
            continue
        out.append(p)
    return out


def ai_offers(team: Team, proc, ctx, mins, min_batters, first_team: set[str], trade_settings=None) -> list[str]:
    """AI の候補:出せる選手のうち価値の低い順に(設定の人数まで)。あなたの球団のおすすめにも使う。"""
    ts = trade_settings or default_settings()
    rate = float(proc.rate) or 1.0
    pool = eligible(team, proc, ctx, mins, min_batters, first_team, ts)
    pool.sort(key=lambda p: (_value(p, ctx, rate, ts), p.id))
    return [p.id for p in pool[: int(settings_of(ts)["offers"])]]


def set_offers(league: League, proc, ctx, mins, min_batters, my_team_id: str | None, my_offer: list[str] | None = None, trade_settings=None) -> None:
    """全球団の候補を決める(一度だけ)。my_offer があれば、あなたの球団はそれ(出せる選手で、設定の人数まで)。"""
    if proc.active_offers_done:
        return
    first_team = first_team_ids(league, proc, ctx, trade_settings)
    for team in league.teams:
        if team.id == my_team_id and my_offer is not None:
            check_offer(team, my_offer, proc, ctx, mins, min_batters, trade_settings)
            proc.active_offers[team.id] = [str(x) for x in my_offer]
        else:
            proc.active_offers[team.id] = ai_offers(team, proc, ctx, mins, min_batters, first_team, trade_settings)
    proc.active_offers_done = True


def check_offer(team: Team, ids: list[str], proc, ctx, mins, min_batters, trade_settings=None) -> None:
    """あなたの候補が出せるか(だめなら ValueError。理由つき)。"""
    n = int(settings_of(trade_settings)["offers"])
    if len(set(ids)) != len(ids):
        raise ValueError("同じ選手が 2 回入っています")
    if not 1 <= len(ids) <= n:
        raise ValueError(f"出す選手は 1〜{n} 人にしてください")
    ok = {p.id for p in eligible(team, proc, ctx, mins, min_batters, first_team_ids_for(proc, team, ctx, trade_settings), trade_settings)}
    bad = [x for x in ids if x not in ok]
    if bad:
        raise ValueError("出せない選手が入っています(一軍にいた選手・外国人・このオフに入った選手・年俸の高い選手・最低人数を割る選手は出せません)")


def first_team_ids_for(proc, team: Team, ctx, trade_settings=None) -> set[str]:
    """1 球団分の一軍とみなす選手(候補の点検用)。"""
    if proc.season_actives:
        return set(proc.season_actives)
    n = int(settings_of(trade_settings)["active_size"])
    ranked = sorted(team.players, key=lambda p: (-float(ctx.expected(p)[0]), p.id))
    return {p.id for p in ranked[:n]}


# ---- 指名 ----

def _taken_from(proc) -> set[str]:
    return {x["from_team"] for x in proc.active_picks}


def _picked(proc) -> set[str]:
    return {x["team_id"] for x in proc.active_picks}


def participants(proc) -> list[str]:
    """参加する球団(候補が 1 人以上)を、指名の順番で。"""
    return [tid for tid in proc.order if proc.active_offers.get(tid)]


def pickers_left(proc) -> list[str]:
    done = _picked(proc)
    return [tid for tid in participants(proc) if tid not in done]


def current_picker(proc) -> str | None:
    left = pickers_left(proc)
    return left[0] if left else None


def choices(league: League, proc, team_id: str) -> list[Player]:
    """その球団が今、指名できる選手:まだ選手を取られていない、ほかの球団の候補(最後の 1 球団が自分の候補しか選べなくなる指名は除く)。"""
    players = {p.id: p for p in league.all_players()}
    pickers = pickers_left(proc)
    losers = [tid for tid in participants(proc) if tid not in _taken_from(proc)]
    out = []
    for u in losers:
        if u == team_id:
            continue
        rest_p = [x for x in pickers if x != team_id]
        rest_l = [x for x in losers if x != u]
        if len(rest_p) == 1 and rest_p == rest_l:
            continue
        out += [players[pid] for pid in proc.active_offers[u] if pid in players and players[pid].team_id == u]
    return out


def ai_pick(team: Team, options: list[Player], proc, ctx, config, trade_settings=None) -> Player | None:
    """AI の指名:価値 + 手薄なポジションの加点の最も高い選手(同じなら ID の順)。"""
    if not options:
        return None
    ts = trade_settings or default_settings()
    rate = float(proc.rate) or 1.0
    return max(sorted(options, key=lambda p: p.id), key=lambda p: _value(p, ctx, rate, ts) + need_bonus(team, [], [p], config, ts))


def take(league: League, proc, team_id: str, player: Player, by: str) -> dict:
    """指名した選手を移す(契約はそのまま)。記録を返す。"""
    teams = {t.id: t for t in league.teams}
    frm = teams[player.team_id]
    to = teams[team_id]
    frm.players = [p for p in frm.players if p.id != player.id]
    player.team_id = to.id
    player.state.fatigue = 0.0
    to.players.append(player)
    rec = {"year": proc.year, "team_id": to.id, "from_team": frm.id, "player_id": player.id, "name": player.name, "role": player.role, "position": player.position,
           "age": player.age, "salary": int(player.contract["salary"]) if player.contract else None, "by": by}
    proc.active_picks.append(rec)
    return rec


def run_turns(league: League, proc, ctx, config, my_team_id: str | None, my_pick: str | None = None, trade_settings=None) -> bool:
    """指名を順番に進める。あなたの番で my_pick がなければ True で止まる。全球団が終われば False。
    my_team_id が None なら全球団 AI(おまかせ)。"""
    teams = {t.id: t for t in league.teams}
    while True:
        tid = current_picker(proc)
        if tid is None:
            return False
        options = choices(league, proc, tid)
        if tid == my_team_id:
            if my_pick is None:
                return True
            player = next((p for p in options if p.id == str(my_pick)), None)
            if player is None:
                raise ValueError("その選手は指名できません(ほかの球団の候補で、まだ取られていない球団の選手から選びます)")
            take(league, proc, tid, player, "you")
            my_pick = None
            continue
        player = ai_pick(teams[tid], options, proc, ctx, config, trade_settings)
        if player is None:  # 選べる候補がない(起こらないはずだが、止まらないように飛ばす)
            proc.active_picks.append({"year": proc.year, "team_id": tid, "from_team": None, "player_id": None, "name": "", "role": "", "position": "", "age": None, "salary": None, "by": "skip"})
            continue
        take(league, proc, tid, player, "ai")


def run_all(league: League, proc, ctx, config, mins, min_batters, my_team_id: str | None = None, trade_settings=None) -> None:
    """現役ドラフトを最後まで(おまかせ・事前運転):候補が決まっていなければ AI の方針で決め、残りの指名も AI。"""
    if ctx is None:
        return
    set_offers(league, proc, ctx, mins, min_batters, my_team_id, None, trade_settings)
    run_turns(league, proc, ctx, config, None, None, trade_settings)
