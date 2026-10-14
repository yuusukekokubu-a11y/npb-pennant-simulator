"""自由契約市場(作り直し③。D-343、D-370):年俸は自動、前年の順位が低い球団から 1 人ずつ獲得する。

- 市場に並ぶのは、手放された選手・FA で決まらなかった選手・ドラフトで指名されなかった候補(D-204)。
- 年俸は算定の年俸(ContractContext.salary。成績と見せる能力から。球団によらない。D-356)、契約は 1 年(D-360)。選手は断らない。
  指名されなかった候補は最低年俸(算定は能力から決まるので、見せない能力が年俸から分かってしまうため。D-368、D-370)。
- 各球団が獲得の希望を出す。あなたは proc.market_offers(選手 ID → {years, salary}。入れた順が優先の順)。
  AI の希望は FA と同じ方針(fa.choose_offers。D-261):一軍に入る見込みがあり、見込みの WAR が設定値以上の選手を、見込みの高い順に設定の人数まで。
- 締める(close_market):ドラフトの 2 巡目以降と同じ順(前年の勝率が低い球団から。周ごとに往復)で、各球団が希望のうち残っている
  最初の選手を 1 人ずつ獲得する。空き枠がない球団は飛ばし、資金を超える選手は希望から外す。誰も獲得しなくなったら終わり。
- 決まらなかった選手は、そのオフの終わりにリーグを去る(draft.finalize)。事前運転も同じ方式(D-370)。
"""

from __future__ import annotations

from .contracts import set_contract, team_salary
from .fa import MAX_ROSTER, choose_offers
from .models import League, Player, Team
from .procedure import OffseasonProcedure, entry_summary, join


def uses_offers(ctx) -> bool:
    """希望と順番の方式を使うか(契約の文脈がある手続き。ない古い形の呼び出しは、指名の順番の方式。draft.run_ai_turns)。"""
    return ctx is not None


def league_sizes(league: League) -> dict[int, int]:
    sizes: dict[int, int] = {}
    for t in league.teams:
        sizes[t.league_index] = sizes.get(t.league_index, 0) + 1
    return sizes


def offer_terms(player: Player, team_id: str, ctx) -> tuple[int, int, float]:
    """(算定年俸, 年数, 見込みの WAR)。市場の契約は算定の年俸で 1 年(D-360、D-370)。"""
    salary, years, exp = ctx.salary(player, team_id)
    return int(salary), int(years), float(exp)


def market_terms(proc: OffseasonProcedure, player: Player, team_id: str, ctx, former: dict[str, str] | None = None) -> tuple[int, int, float]:
    """市場で獲得したときの (年俸, 年数, 見込みの WAR)。プロの選手は算定、指名されなかった候補は最低年俸(1 年)。"""
    salary, years, exp = offer_terms(player, team_id, ctx)
    if player.id not in (former if former is not None else former_teams(proc)):
        salary = int(ctx.settings.minimum)
    return salary, years, exp


def former_teams(proc: OffseasonProcedure) -> dict[str, str]:
    """選手 → 手放した球団(FA で決まらなかった選手は宣言した球団)。指名されなかった候補はない。"""
    out = {x["player_id"]: x["team_id"] for x in proc.released}
    for pid, x in proc.fa_info.items():
        out.setdefault(pid, x["former_team"])
    return out


def ai_wishes(team: Team, proc: OffseasonProcedure, ctx, sizes: dict[int, int]) -> list[str]:
    """AI の獲得の希望(選手 ID の並び。前ほど優先)。FA と同じ方針(D-261、D-370)。"""
    items = []
    former = former_teams(proc)
    for p in proc.market:
        salary, years, exp = market_terms(proc, p, team.id, ctx, former)
        items.append((p, exp, salary, years))
    return list(choose_offers(team, items, proc, ctx, sizes, 1.0))


def close_market(league: League, proc: OffseasonProcedure, ctx, my_team_id: str | None, scout_sd: dict[str, dict], settings, my_ai: bool = False) -> list[dict]:
    """市場を締める:あなたの希望(proc.market_offers)と AI の希望を合わせて、順番に 1 人ずつ獲得する。my_ai=True なら、あなたの球団も AI と同じ方針。
    戻り値は成立した契約。獲得した選手は入団し(入団時の評価を残す)、指名の履歴(picks)に phase="market" で残る。"""
    teams = {t.id: t for t in league.teams}
    sizes = league_sizes(league)
    wishes: dict[str, list[str]] = {}
    for team in league.teams:
        if team.id == my_team_id and not my_ai:
            wishes[team.id] = [pid for pid in proc.market_offers]
        else:
            wishes[team.id] = ai_wishes(team, proc, ctx, sizes)
        for rank, pid in enumerate(wishes[team.id], start=1):
            proc.market_log.append({"player_id": pid, "team_id": team.id, "rank": rank})
    wanted: dict[str, int] = {}
    for lst in wishes.values():
        for pid in lst:
            wanted[pid] = wanted.get(pid, 0) + 1
    former = former_teams(proc)
    by_id = {p.id: p for p in proc.market}
    signed: list[dict] = []
    rnd = 1
    while True:
        took = False
        order = proc.order if rnd % 2 == 1 else list(reversed(proc.order))
        for tid in order:
            team = teams[tid]
            lst = wishes.get(tid, [])
            if len(team.players) >= MAX_ROSTER:
                lst.clear()
                continue
            while lst:
                pid = lst.pop(0)
                p = by_id.get(pid)
                if p is None or p not in proc.market:
                    continue
                salary, years, _ = market_terms(proc, p, tid, ctx, former)
                cap = ctx.cap(tid)
                if ctx.hard() and cap is not None and team_salary(team) + salary > cap:
                    continue
                proc.market.remove(p)
                join(team, p, proc, scout_sd[tid], settings)
                set_contract(p, int(salary), int(years), proc.year + 1, "market")
                proc.picks.append({"phase": "market", "round": rnd, "team_id": tid, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "salary": int(salary), "years": int(years), **entry_summary(p.scouting)})
                result = {"round": rnd, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "former_team": former.get(p.id), "team_id": tid, "years": int(years), "salary": int(salary), "offers": wanted.get(pid, 0)}
                proc.market_results.append(result)
                signed.append(result)
                took = True
                break
        if not took:
            break
        rnd += 1
    proc.market_offers = {}
    proc.market_done = True
    return signed
