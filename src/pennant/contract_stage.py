"""オフの手続きの契約の段階(F3-2a〜F3-2c。①b で draft.py から分けた。D-302):契約の文脈(ContractContext)・手続きの最初の契約の処理・
契約更改の交渉・予算超過の解消・契約の段階の終わり。FA 宣言は fa.py。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .contracts import ContractSettings, cap_of, contract_years, expected_war, is_hard, rate_for, salary_for, set_contract, team_salary
from .fa import declare, fa_settings, is_holder, star_multiplier
from .models import League, Player, Team
from .negotiation import NegotiationSettings, ai_offer, ai_years, depth_ranks, depth_slots, ensure_preferences, judge, noise_for
from .scouting import ceiling_cuts, quick_value
from .season import derive_seed
from .procedure import DraftSettings, OffseasonProcedure, ai_release, apply_ai_releases, next_phase, releasable, release_players


# ---- 契約(F3-2a。D-232〜D-237) ----

@dataclass
class ContractContext:
    """手続きの中で契約を扱うための材料。war_history(選手 ID, 役割) → [(WAR, 出場), ...](新しい順)。scout(選手, 球団) → (総合の推定値, 天井)。
    乱数はすべて契約の系列(contract:…)で、AI の判断には使わない(D-237)。"""

    settings: ContractSettings
    rule: str  # none / loose / standard / strict
    tiers: dict  # 球団 → large / medium / small(きびしいのとき)
    war_history: object  # (選手 ID, 役割) → [(WAR, 出場), ...]
    sd_of: object  # 球団 ID → ずれ({common, item})
    scout: object = None  # (選手, 球団) → (総合の推定値, 天井)。bind(proc) で手続きのシードと区切りから作る
    rate: float = 0.0
    negotiation: NegotiationSettings | None = None  # 志望の判定の設定(F3-2b)。None なら判定なしで全員が受ける(F3-2a と同じ)
    league_seed: int = 0  # 志望の乱数のもと
    slots: dict | None = None  # ポジション → 一軍の枠の目安(出場機会軸)
    _cache: dict = field(default_factory=dict, repr=False, compare=False)  # (選手, 球団) → 見込み(手続きの間は年齢も能力も変わらないので使い回す)

    def bind(self, proc: "OffseasonProcedure") -> "ContractContext":
        if self.scout is None:
            self.scout = contract_scout(proc.seed, self.sd_of, proc.cuts)
        return self

    def cap(self, team_id: str) -> int | None:
        return cap_of(self.rule, self.tiers.get(team_id), self.settings)

    def hard(self) -> bool:
        return is_hard(self.rule)

    def expected(self, player: Player, team_id: str) -> tuple[float, str]:
        key = (player.id, team_id)
        v = self._cache.get(key)
        if v is None:
            v = expected_war(player.age, player.role, self.war_history(player.id, player.role), self.scout(player, team_id), self.settings)
            self._cache[key] = v
        return v

    def salary(self, player: Player, team_id: str) -> tuple[int, int, float]:
        """算定した (年俸, 契約年数, 見込みの WAR)。"""
        exp, _ = self.expected(player, team_id)
        return salary_for(exp, self.rate, self.settings), contract_years(player.age, exp, self.settings), exp


def contract_scout(seed: int, sd_of, cuts: list[float]):
    """所属球団の評価(契約用。乱数は contract の系列)を返す関数を作る。同じ (選手, 球団) は覚えておく(手続きの間は能力が変わらない)。"""
    contract_seed = derive_seed(seed, "contract")
    memo: dict = {}

    def scout(player: Player, team_id: str) -> tuple[float, str]:
        key = (player.id, team_id)
        v = memo.get(key)
        if v is None:
            v = quick_value(player, team_id, contract_seed, sd_of(team_id), cuts)
            memo[key] = v
        return v

    return scout


def apply_contracts_start(league: League, proc: OffseasonProcedure, ctx: ContractContext, my_team_id: str | None, mins, min_batters) -> None:
    """手続きの最初の契約の処理(D-235、D-244):単価を求め直す → 契約が満了した全員の交渉を作る → AI 球団は交渉を最後まで進める(D-251)。
    AI 球団の予算超過の解消と自由契約は、契約の段階の終わり(finish_contract_stage)に行う(D-272。順序は 更改 → 超過の解消 → 自由契約 のまま)。
    あなたの球団の交渉・超過の解消・自由契約は契約の段階で手動(「この段階をおまかせ」は AI と同じ)。"""
    if proc.contracts_done:
        return
    ctx.bind(proc)
    expected = {}
    for team in league.teams:
        for p in team.players:
            expected[p.id] = ctx.expected(p, team.id)[0]
    ctx.rate = round(rate_for(list(expected.values()), len(league.teams), len(expected), ctx.settings), 4)
    proc.rate = ctx.rate
    neg = ctx.negotiation
    if neg is not None:
        ensure_preferences(league.all_players(), ctx.league_seed, neg)
        ensure_preferences(proc.candidates, ctx.league_seed, neg)
    sizes: dict[int, int] = {}
    for team in league.teams:
        sizes[team.league_index] = sizes.get(team.league_index, 0) + 1
    for team in league.teams:
        expiring = [p for p in team.players if p.contract is None or int(p.contract["until"]) <= proc.year]
        if not expiring:
            continue
        ranks = depth_ranks(team.players, lambda p: ctx.scout(p, team.id)[0]) if neg is not None else {}
        for p in expiring:
            exp = expected[p.id]
            context = {"rank": ranks.get(p.id, 0), "slots": round((ctx.slots or {}).get(p.position, 1.0), 3), "standing": proc.ranks.get(team.id), "league_size": sizes[team.league_index]}
            if neg is not None and is_holder(p, neg):  # FA 権保持者の更改は厳しい。断ったら宣言する(F3-2c。D-259)
                context["fa_holder"] = True
                context["threshold_add"] = float(fa_settings(neg)["threshold_add"])
            proc.negotiations[p.id] = {
                "team_id": team.id, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age,
                "old_salary": int(p.contract["salary"]) if p.contract else None, "auto_salary": salary_for(exp, ctx.rate, ctx.settings),
                "ai_years": ai_years(p.age, exp, neg, ctx.settings.default_years) if neg is not None else ctx.settings.default_years,
                "expected": round(exp, 2), "context": context, "status": "pending", "offers": [],
            }
    teams = {t.id: t for t in league.teams}
    for team in league.teams:
        if team.id == my_team_id:
            continue
        for e in [e for e in proc.negotiations.values() if e["team_id"] == team.id and e["status"] == "pending"]:
            ai_negotiate_entry(teams[e["team_id"]], e, proc, ctx)
    proc.contracts_done = True


CONTRACT_PHASES = ("renewal", "release")  # 画面ではどちらも「契約」の段階(D-272。release は旧版のセーブデータのため残す)


def finish_contract_stage(league: League, proc: OffseasonProcedure, ctx: ContractContext | None, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, mins, min_batters, my_ai: bool) -> None:
    """契約の段階の終わり(D-272):my_ai なら、あなたの球団の残りの交渉・超過の解消・自由契約を AI と同じ方針で行う(「この段階をおまかせ」)。
    続いて、AI 球団の予算超過の解消(標準以上)と自由契約を行い、FA の段階へ進める。内部の順序は 更改 → 超過の解消 → 自由契約。"""
    teams = {t.id: t for t in league.teams}
    mine = teams.get(my_team_id) if my_team_id is not None else None
    if mine is not None and my_ai and ctx is not None:
        for e in open_entries(proc, my_team_id):
            ai_negotiate_entry(mine, e, proc, ctx)
    if ctx is not None and ctx.hard():
        for team in league.teams:
            if team.id != my_team_id:
                resolve_overrun(team, proc, ctx, mins, min_batters)
    if mine is not None and my_ai and ctx is not None:
        resolve_overrun(mine, proc, ctx, mins, min_batters)
    apply_ai_releases(league, proc, scout_sd, settings, my_team_id, mins, min_batters)
    if mine is not None and my_ai and not proc.my_release_done:
        release_players(mine, ai_release(mine, proc, scout_sd[mine.id], settings, mins, min_batters), proc)
    proc.my_release_done = True
    proc.phase = "release"
    next_phase(proc)


# ---- 契約更改の交渉(F3-2b。D-244、D-250〜D-252) ----

def entry_player(team: Team, entry: dict) -> Player:
    return next(p for p in team.players if p.id == entry["player_id"])


def projected_total(team: Team, proc: OffseasonProcedure) -> int:
    """見込みの総年俸:契約が残る選手と更改済の選手は契約の年俸、未決定の選手は自動案の年俸(D-252)。"""
    total = 0
    for p in team.players:
        e = proc.negotiations.get(p.id)
        if e is not None and e["status"] == "pending":
            total += int(e["auto_salary"])
        elif p.contract:
            total += int(p.contract["salary"])
    return total


def offers_left(entry: dict, settings: NegotiationSettings | None) -> int:
    return 0 if settings is None else max(0, settings.max_offers - len(entry["offers"]))


def make_offer(team: Team, entry: dict, years: int, salary: int, proc: OffseasonProcedure, ctx: ContractContext) -> dict:
    """提示して答えを記録する。受けたら契約、断られて回数を使い切ったら自由契約(市場へ)。戻り値は提示の記録。"""
    player = entry_player(team, entry)
    neg = ctx.negotiation
    if neg is None:
        rec = {"years": int(years), "salary": int(salary), "accepted": True, "reason": None}
    else:
        context = entry["context"]
        expect = star_multiplier(neg, entry["expected"]) if context.get("fa_holder") else 1.0  # 主力級の期待(D-322)。保存はしない(見込みの WAR から毎回求める)
        if expect > 1.0:
            context = {**context, "salary_expect": expect}
        r = judge(neg, player.preference or {}, int(years), int(salary), int(entry["auto_salary"]), player.age, context, ctx.rule, noise_for(proc.seed, player.id, neg))
        rec = {"years": int(years), "salary": int(salary), "accepted": bool(r["accepted"]), "reason": None if r["accepted"] else r["reason"]}
        if not r["accepted"] and r["reason"] == "salary" and expect > 1.0:
            rec["star"] = True  # 断られた理由の文に、主力としての評価を望んでいることを足す(D-322)
    entry["offers"].append(rec)
    if rec["accepted"]:
        set_contract(player, int(salary), int(years), proc.year + 1, "renew" if player.contract else "initial", offers=len(entry["offers"]))
        entry["status"] = "accepted"
    elif neg is not None and entry["context"].get("fa_holder"):
        declare(team, player, entry, proc, ctx)  # FA 権保持者は、断ったらその場で宣言する(D-259)
    elif neg is not None and len(entry["offers"]) >= neg.max_offers:
        release_entry(team, entry, proc)
    return rec


def release_entry(team: Team, entry: dict, proc: OffseasonProcedure) -> None:
    """交渉をやめて自由契約(市場へ)。"""
    player = entry_player(team, entry)
    release_players(team, [player], proc)
    proc.released[-1]["note"] = "negotiation"
    entry["status"] = "released"


def ai_negotiate_entry(team: Team, entry: dict, proc: OffseasonProcedure, ctx: ContractContext) -> None:
    """AI の方針で交渉を最後まで進める(D-251):最初は自動案(若手の高い見込みは複数年)、断られたら見込みの高い選手にだけ年俸と年数を上げて再提示。
    標準以上で上限を超えるなら年俸は上げない。再提示しない選手は自由契約。"""
    neg = ctx.negotiation
    while entry["status"] == "pending":
        if neg is None:
            make_offer(team, entry, entry["ai_years"], entry["auto_salary"], proc, ctx)
            break
        o = ai_offer(len(entry["offers"]), int(entry["auto_salary"]), int(entry["ai_years"]), float(entry["expected"]), neg, ctx.rule, ctx.settings.max_years, ctx.settings.rounding)
        if o is None:
            release_entry(team, entry, proc)
            break
        years, salary = o
        auto = int(entry["auto_salary"])
        if salary > auto and ctx.hard():
            cap = ctx.cap(team.id)
            if cap is not None and projected_total(team, proc) - auto + salary > cap:
                salary = auto
        make_offer(team, entry, years, salary, proc, ctx)


def open_entries(proc: OffseasonProcedure, team_id: str) -> list[dict]:
    return [e for e in proc.negotiations.values() if e["team_id"] == team_id and e["status"] == "pending"]


def initialize_contracts(league: League, seed: int, ctx: ContractContext, prerun_years: int) -> float:
    """新規開始のとき、初期選手の契約を作り直す(D-232):年俸は所属球団の評価から算定(校正の後の能力で)、残りの年数は事前運転の契約から引き継ぐ
    (事前運転で契約を扱わなかったときは、AI 球団の更改と同じ方針の年数。見込みの高い若手は 2〜3 年、ほかは 1 年。D-243)。
    1 シーズン目から数えた満了シーズンに付け替える。戻り値は単価。"""
    all_players = league.all_players()
    ctx.scout = contract_scout(derive_seed(seed, "contract:init"), ctx.sd_of, ceiling_cuts(all_players, {"S": 0.05, "A": 0.15, "B": 0.30, "C": 0.30, "D": 0.20}))
    expected = {}
    for team in league.teams:
        for p in team.players:
            expected[p.id] = ctx.expected(p, team.id)[0]
    ctx.rate = round(rate_for(list(expected.values()), len(league.teams), len(expected), ctx.settings), 4)
    for team in league.teams:
        salaries = {p.id: salary_for(expected[p.id], ctx.rate, ctx.settings) for p in team.players}
        cap = ctx.cap(team.id)
        if ctx.hard() and cap is not None and sum(salaries.values()) > cap:
            # 標準以上で上限を超える球団は、最低年俸を超える分を同じ割合で縮めて、開始時に上限の中に収める(開始直後に選手を手放さなくて済むように)
            mn = ctx.settings.minimum
            excess = sum(v - mn for v in salaries.values())
            room = cap - mn * len(salaries)
            scale = max(0.0, room / excess) if excess > 0 else 0.0
            step = ctx.settings.rounding
            salaries = {pid: max(mn, int((mn + (v - mn) * scale) // step) * step) for pid, v in salaries.items()}
        for p in team.players:
            exp = expected[p.id]
            years = ai_years(p.age, exp, ctx.negotiation, ctx.settings.default_years) if ctx.negotiation is not None else ctx.settings.default_years
            remaining = max(1, int(p.contract["until"]) - prerun_years) if p.contract else years
            p.contract = None
            set_contract(p, salaries[p.id], remaining, 1, "initial")
    return ctx.rate


def state_war_history(state):
    """セーブデータの状態から、(選手 ID, 役割) → 直近 3 シーズンの [(WAR, 出場)](新しい順)を返す関数(履歴の集計から)。"""
    archives = list(state.history)[-3:][::-1]

    def war_history(pid: str, role: str) -> list[tuple[float, float]]:
        out = []
        for a in archives:
            line = a.war.get(pid)
            if line is None:
                continue
            if role == "batter":
                usage = float(a.records.batters.get(pid, {}).get("PA", 0))
                out.append((float(line.war), usage))
            else:
                usage = float(a.records.pitchers.get(pid, {}).get("OUTS", 0)) / 3.0
                out.append((float(line.war_ra), usage))
        return out

    return war_history


def state_context(state, proc: "OffseasonProcedure | None" = None) -> ContractContext:
    """セーブデータの状態から契約の文脈を作る(手続きがあればそのシードと区切りで評価を引く)。"""
    ctx = ContractContext(state.contract_settings, state.money_rule, dict(state.budget_tiers), state_war_history(state), state.scout_sd_of, negotiation=state.negotiation_settings, league_seed=state.league.seed, slots=depth_slots(state.gen_config))
    if proc is not None:
        ctx.bind(proc)
        ctx.rate = float(proc.rate)
    return ctx


def fill_missing_contracts(state) -> None:
    """版9以前のセーブデータ:契約のない選手に、算定した年俸と 1〜3 年の残り(別の乱数系列)を付ける(D-235)。"""
    league = state.league
    seed = derive_seed(league.seed, "contract:migrate")
    rng = random.Random(seed)
    ctx = ContractContext(state.contract_settings, state.money_rule, dict(state.budget_tiers), state_war_history(state), state.scout_sd_of)
    ctx.scout = contract_scout(seed, state.scout_sd_of, ceiling_cuts(league.all_players(), {"S": 0.05, "A": 0.15, "B": 0.30, "C": 0.30, "D": 0.20}))
    expected = {p.id: ctx.expected(p, t.id)[0] for t in league.teams for p in t.players}
    rate = rate_for(list(expected.values()), len(league.teams), len(expected), ctx.settings)
    year = int(state.year)
    for team in league.teams:
        for p in sorted(team.players, key=lambda p: p.id):
            if p.contract is not None:
                continue
            years = rng.randint(1, 3)
            set_contract(p, salary_for(expected[p.id], rate, ctx.settings), years, year, "migrate")
    state.contract_rates.setdefault(str(year), rate)


def over_cap(team: Team, ctx: ContractContext) -> int:
    """上限(標準以上)か目安(ゆるい)を超えている額(超えていなければ 0)。なしは常に 0。"""
    cap = ctx.cap(team.id)
    if cap is None:
        return 0
    return max(0, team_salary(team) - cap)


def resolve_overrun(team: Team, proc: OffseasonProcedure, ctx: ContractContext, mins, min_batters) -> list[Player]:
    """予算超過の解消(AI と同じ方針):見込みの WAR あたりの年俸(最低年俸を超える分)が高い選手から、超過が解消するまで自由契約。
    最低人数は守るが、守ったままでは解消できないときは最低人数を割っても外す(1 年契約で年俸が毎年算定し直されるため、予算の小さい強い球団で起こる。
    不足は完了のときに最低年俸で自動補充される。F3-2b)。"""
    out: list[Player] = []
    if not ctx.hard():
        return out
    while over_cap(team, ctx) > 0 and team.players:
        ok = releasable(team.players, mins, min_batters)
        candidates = [p for p in team.players if p.id in ok] or list(team.players)  # 最低人数の選手しか残っていなければ、最低人数を割っても外す(不足は完了のときに最低年俸で自動補充。F3-2b)

        def cost(p: Player) -> float:  # 最低年俸を超える分 ÷ 見込みの WAR(最低年俸の選手は外しても補充と同じ額なので最後。F3-2b)
            exp = max(0.05, ctx.expected(p, team.id)[0])
            return (int(p.contract["salary"]) - ctx.settings.minimum) / exp if p.contract else 0.0

        worst = max(candidates, key=lambda p: (cost(p), int(p.contract["salary"]) if p.contract else 0, p.id))
        salary = int(worst.contract["salary"]) if worst.contract else 0
        release_players(team, [worst], proc)
        proc.released[-1]["note"] = "budget"
        proc.budget_releases.append({"team_id": team.id, "player_id": worst.id, "name": worst.name, "role": worst.role, "position": worst.position, "age": worst.age, "salary": salary, "expected": round(ctx.expected(worst, team.id)[0], 2)})
        out.append(worst)
    return out


def can_afford(team: Team, salary: int, ctx: ContractContext | None, total: int | None = None) -> bool:
    """標準以上で、この年俸の契約を結んでも上限を超えないか(なし・ゆるいは常に可)。total は今の総年俸(省略時は数える)。"""
    if ctx is None or not ctx.hard():
        return True
    cap = ctx.cap(team.id)
    if cap is None:
        return True
    return (team_salary(team) if total is None else total) + int(salary) <= cap


def offer_for(team: Team, player: Player, proc: OffseasonProcedure, ctx: ContractContext | None) -> tuple[int, int, float] | None:
    """指名(獲得)したときの契約 (年俸, 年数, 見込み)。ドラフトは巡ごとの表、市場は算定。契約の文脈がなければ None。"""
    if ctx is None:
        return None
    if proc.phase == "draft":
        return ctx.settings.rookie_salary(proc.round), ctx.settings.rookie_years, 0.0
    return ctx.salary(player, team.id)
