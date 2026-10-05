"""自由契約市場の提示の方式(①b。D-300):FA と同じ考え方で 1 ラウンド。

- 市場に並ぶのは、手放された選手・FA で決まらなかった選手・ドラフトで指名されなかった候補(D-204。並ぶ選手は変えない)。
- 各球団(あなたと AI)が年数と年俸を提示する。選手は、受けた提示の中から志望の満足度(negotiation.judge。出場機会と勝利は提示した球団での見込み)
  が最も高いものを選び、しきい値以上なら成立する。判定の乱数は derive_seed(手続きのシード, "market:<選手>:<球団>")。
- 算定年俸と見込みの WAR は、提示する球団の評価で求める(ContractContext.salary。履歴のある選手は球団によらない)。
- AI の提示の方針は FA と同じ(fa.choose_offers。D-261)。倍率は 1 ラウンド目の値。
- 決まらなかった選手は、そのオフの終わりにリーグを去る(draft.finalize)。
- 志望の判定の設定がない手続き(事前運転。D-254)では使わない。今までの順番の方式のまま(D-301。draft.run_ai_turns)。
"""

from __future__ import annotations

import random

from .contracts import set_contract, team_salary
from .fa import MAX_ROSTER, _context, choose_offers, fa_settings, round_multiplier
from .models import League, Player, Team
from .negotiation import ai_years, ensure_preferences, judge
from .procedure import OffseasonProcedure, entry_summary, join
from .season import derive_seed


def uses_offers(ctx) -> bool:
    """提示の方式を使うか(志望の判定の設定がある手続き)。"""
    return ctx is not None and ctx.negotiation is not None


def league_sizes(league: League) -> dict[int, int]:
    sizes: dict[int, int] = {}
    for t in league.teams:
        sizes[t.league_index] = sizes.get(t.league_index, 0) + 1
    return sizes


def offer_terms(player: Player, team_id: str, ctx) -> tuple[int, int, float]:
    """その球団から見た (算定年俸, AI の提示の年数, 見込みの WAR)。"""
    salary, _, exp = ctx.salary(player, team_id)
    years = ai_years(player.age, exp, ctx.negotiation, ctx.settings.default_years) if ctx.negotiation is not None else ctx.settings.default_years
    return int(salary), int(years), float(exp)


def former_teams(proc: OffseasonProcedure) -> dict[str, str]:
    """選手 → 手放した球団(FA で決まらなかった選手は宣言した球団)。指名されなかった候補はない。"""
    out = {x["player_id"]: x["team_id"] for x in proc.released}
    for pid, x in proc.fa_info.items():
        out.setdefault(pid, x["former_team"])
    return out


def ai_market_offers(team: Team, proc: OffseasonProcedure, ctx, sizes: dict[int, int]) -> dict[str, tuple[int, int]]:
    """AI の市場の提示(選手 ID → (年数, 年俸))。FA と同じ方針(D-300)。"""
    items = []
    for p in proc.market:
        calc, years, exp = offer_terms(p, team.id, ctx)
        items.append((p, exp, calc, years))
    return choose_offers(team, items, proc, ctx, sizes, round_multiplier(ctx.negotiation, 1))


def close_market(league: League, proc: OffseasonProcedure, ctx, my_team_id: str | None, scout_sd: dict[str, dict], settings, my_ai: bool = False) -> list[dict]:
    """市場を締める:あなたの提示(proc.market_offers)と AI の提示を合わせて、選手が選ぶ。my_ai=True なら、あなたの球団も AI と同じ方針。
    戻り値は成立した契約。成立した選手は入団し(入団時の評価を残す)、指名の履歴(picks)に phase="market" で残る。"""
    neg = ctx.negotiation
    fa = fa_settings(neg)
    teams = {t.id: t for t in league.teams}
    sizes = league_sizes(league)
    ensure_preferences(proc.market, ctx.league_seed, neg)
    offers: dict[str, dict[str, tuple[int, int]]] = {}
    for team in league.teams:
        if team.id == my_team_id and not my_ai:
            mine = {pid: (int(o["years"]), int(o["salary"])) for pid, o in proc.market_offers.items()}
        else:
            mine = ai_market_offers(team, proc, ctx, sizes)
        for pid, (years, salary) in mine.items():
            offers.setdefault(pid, {})[team.id] = (years, salary)
            proc.market_log.append({"player_id": pid, "team_id": team.id, "years": years, "salary": salary})
    former = former_teams(proc)
    by_id = {p.id: p for p in proc.market}
    order = sorted(offers, key=lambda pid: (-max(s for _, s in offers[pid].values()), pid))
    signed = []
    for pid in order:
        p = by_id.get(pid)
        if p is None:
            continue
        best = None
        for tid in sorted(offers[pid]):
            years, salary = offers[pid][tid]
            team = teams[tid]
            if len(team.players) >= MAX_ROSTER:
                continue
            cap = ctx.cap(tid)
            if ctx.hard() and cap is not None and team_salary(team) + salary > cap:
                continue
            calc = offer_terms(p, tid, ctx)[0]
            noise = random.Random(derive_seed(proc.seed, f"market:{pid}:{tid}")).gauss(0.0, float(fa["noise_sd"]))
            r = judge(neg, p.preference or {}, years, salary, calc, p.age, _context(p, team, proc, ctx, sizes), ctx.rule, noise)
            if best is None or r["score"] > best[0]:
                best = (r["score"], tid, years, salary)
        if best is None or best[0] < 0.0:
            continue
        _, tid, years, salary = best
        team = teams[tid]
        proc.market.remove(p)
        join(team, p, proc, scout_sd[tid], settings)
        set_contract(p, int(salary), int(years), proc.year + 1, "market")
        row = {"phase": "market", "round": 1, "team_id": tid, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "salary": int(salary), "years": int(years), **entry_summary(p.scouting)}
        proc.picks.append(row)
        result = {"player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "former_team": former.get(p.id), "team_id": tid, "years": int(years), "salary": int(salary), "offers": len(offers[pid])}
        proc.market_results.append(result)
        signed.append(result)
    proc.market_offers = {}
    proc.market_done = True
    return signed
