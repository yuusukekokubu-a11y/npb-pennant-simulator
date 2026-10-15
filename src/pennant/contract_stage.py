"""オフの手続きの契約の段階(F3-2a〜F3-2c。①b で draft.py から分けた。D-302。作り直し②で査定の自動更改に。D-353〜D-361):
契約の文脈(ContractContext)・資金の増減・手続きの最初の更改(査定)と FA 権保持者の宣言・資金の超過の解消・契約の段階の終わり。FA 市場は fa.py。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .contracts import ContractSettings, assessed_salary, changed_funds, contract_years, expected_war, initial_funds, rate_for, salary_for, set_contract, team_salary
from .fa import declare, declare_probability, is_holder
from .models import League, Player, Team
from .negotiation import NegotiationSettings
from .scouting import quick_value
from .season import derive_seed
from .stats import overall
from .procedure import DraftSettings, OffseasonProcedure, ai_release, apply_ai_releases, next_phase, releasable, release_players


# ---- 契約(F3-2a。D-232〜D-237。作り直し②。D-353〜D-356) ----

@dataclass
class ContractContext:
    """手続きの中で契約を扱うための材料。war_history(選手 ID, 役割) → [(WAR, 出場), ...](新しい順)。scout(選手, 球団) → (総合の推定値, 天井)。
    funds は球団 → 資金(年俸の合計の上限。D-353)。空なら資金の制限なし(古い形の呼び出しと、試験用)。
    乱数はすべて契約の系列(contract:…)で、AI の判断には使わない(D-237)。"""

    settings: ContractSettings
    funds: dict  # 球団 → 資金(万円)
    tiers: dict  # 球団 → large / medium / small(規模)
    war_history: object  # (選手 ID, 役割) → [(WAR, 出場), ...]
    sd_of: object  # 球団 ID → ずれ({common, item})
    scout: object = None  # (選手, 球団) → (総合の推定値, 天井)。bind(proc) で手続きのシードと区切りから作る(FA の出場機会の見込みに使う)
    rate: float = 0.0
    negotiation: NegotiationSettings | None = None  # FA の設定(宣言の確率・選手が選ぶ点数。D-365。None なら宣言も FA もしない:事前運転)
    league_seed: int = 0  # リーグのシード
    slots: dict | None = None  # ポジション → 一軍の枠の目安(出場機会軸)
    upkeep: dict = field(default_factory=dict)  # 球団 → 設備の維持費(万円。D-383)。資金の上限から引く
    _cache: dict = field(default_factory=dict, repr=False, compare=False)  # 選手 → 見込み(手続きの間は年齢も能力も変わらないので使い回す)

    def bind(self, proc: "OffseasonProcedure") -> "ContractContext":
        if self.scout is None:
            self.scout = contract_scout(proc.seed, self.sd_of, proc.cuts)
        return self

    def cap(self, team_id: str) -> int | None:
        """年俸に使える額 = 資金 − 設備の維持費(D-383)。"""
        v = self.funds.get(team_id)
        return None if v is None else int(v) - int(self.upkeep.get(team_id, 0))

    def total_cap(self) -> int:
        """全球団の年俸に使える額の合計(単価を求めるため)。"""
        return sum(int(v) - int(self.upkeep.get(t, 0)) for t, v in self.funds.items())

    def hard(self) -> bool:
        return bool(self.funds)

    def expected(self, player: Player, team_id: str | None = None) -> tuple[float, str]:
        """見込みの WAR(成績と見せる能力から。所属球団によらない。D-356)。team_id は古い呼び出しのため残している。"""
        v = self._cache.get(player.id)
        if v is None:
            v = expected_war(player.age, player.role, self.war_history(player.id, player.role), overall(player), self.settings)
            self._cache[player.id] = v
        return v

    def salary(self, player: Player, team_id: str | None = None) -> tuple[int, int, float]:
        """算定した (年俸, 契約年数, 見込みの WAR)。市場・FA の算定。年数は 1 年(複数年は更改の主力級と、FA の提示だけ。D-360)。"""
        exp, _ = self.expected(player)
        return salary_for(exp, self.rate, self.settings), self.settings.default_years, exp


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


def update_funds(league: League, proc: OffseasonProcedure, ctx: ContractContext) -> None:
    """資金の増減(D-354):前のシーズンのリーグ内の順位で増減する。順位がなければ(事前運転)そのまま。"""
    for team in league.teams:
        if team.id in ctx.funds:
            ctx.funds[team.id] = changed_funds(ctx.funds[team.id], ctx.tiers.get(team.id), proc.ranks.get(team.id), ctx.settings)


def apply_contracts_start(league: League, proc: OffseasonProcedure, ctx: ContractContext, my_team_id: str | None, mins, min_batters) -> None:
    """手続きの最初の契約の処理(作り直し②。D-354〜D-360):資金の増減 → 単価を求め直す → 契約が満了した全員を査定で更改する(全球団。選手は受ける)。
    FA 権保持者は確率で宣言し、宣言しなければ更改する(D-359、D-366。FA の設定がない事前運転では宣言しない)。
    自動 FA では、FA 権を取った選手は契約が残っていても必ず FA になり、その後は満了のたびに FA(D-390)。
    資金の超過の解消と自由契約は、契約の段階の終わり(finish_contract_stage)に行う(あなたの球団は契約の段階で手動)。"""
    if proc.contracts_done:
        return
    ctx.bind(proc)
    update_funds(league, proc, ctx)
    if ctx.funds:  # AI 球団の設備の段階を資金に応じて決め、維持費を資金の上限から引く(D-383)
        from .facilities import ai_decide, league_upkeep

        ai_decide(league, ctx.funds, my_team_id)
        ctx.upkeep = league_upkeep(league)
    expected = {}
    for team in league.teams:
        for p in team.players:
            expected[p.id] = ctx.expected(p)[0]
    total_funds = ctx.total_cap() if ctx.funds else ctx.settings.base_budget * len(league.teams)
    ctx.rate = round(rate_for(list(expected.values()), total_funds, len(expected), ctx.settings), 4)
    proc.rate = ctx.rate
    neg = ctx.negotiation
    auto_fa = neg is not None and proc.game_rules().auto_fa  # 自動 FA(D-390):FA 権を取ったら FA。事前運転(FA の設定なし)は今までどおり更改
    for team in league.teams:
        expiring = [p for p in team.players if p.contract is None or int(p.contract["until"]) <= proc.year]
        if auto_fa:  # FA 権を取ったオフは、契約が残っていても FA(契約は終わる。D-390)
            ids = {p.id for p in expiring}
            expiring += [p for p in team.players if p.id not in ids and not p.fa_free and is_holder(p, neg)]
        for p in expiring:
            exp = expected[p.id]
            old = int(p.contract["salary"]) if p.contract else None
            salary = assessed_salary(exp, ctx.rate, ctx.settings, old)
            years = contract_years(p.age, exp, ctx.settings)
            e = proc.negotiations[p.id] = {
                "team_id": team.id, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age,
                "old_salary": old, "auto_salary": salary, "ai_years": years, "expected": round(exp, 2), "context": {}, "status": "pending", "offers": [],
            }
            if auto_fa and (p.fa_free or is_holder(p, neg)):  # 自動 FA:FA 権を取った選手は必ず FA。その後は満了のたびに FA(D-390)
                p.fa_free = True
                e["context"]["auto_fa"] = True
                declare(team, p, e, proc, ctx)
                proc.fa_info[p.id]["auto"] = True
                if team.id == my_team_id:  # あなたの球団の残留の提示は、最初から入れておく(取り消せる)
                    from .fa import retain_salary

                    proc.fa_offers[p.id] = {"years": int(years), "salary": retain_salary(proc.fa_info[p.id], 1, ctx)}
                continue
            if neg is not None and not auto_fa and is_holder(p, neg):
                e["context"]["fa_holder"] = True
                if random.Random(derive_seed(proc.seed, f"declare:{p.id}")).random() < declare_probability(neg, exp):
                    declare(team, p, e, proc, ctx)
                    continue
            set_contract(p, salary, years, proc.year + 1, "renew" if p.contract else "initial")
            e["status"] = "accepted"
    proc.contracts_done = True


CONTRACT_PHASES = ("renewal", "release")  # 画面ではどちらも「契約」の段階(D-272。release は旧版のセーブデータのため残す)


def finish_contract_stage(league: League, proc: OffseasonProcedure, ctx: ContractContext | None, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, mins, min_batters, my_ai: bool) -> None:
    """契約の段階の終わり(D-272、D-361):AI 球団の資金の超過の解消と自由契約を行い、FA の段階へ進める。
    my_ai なら、あなたの球団の超過の解消と自由契約も AI と同じ方針で行う(「この段階をおまかせ」)。内部の順序は 更改 → 超過の解消 → 自由契約。"""
    teams = {t.id: t for t in league.teams}
    mine = teams.get(my_team_id) if my_team_id is not None else None
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


def entry_player(team: Team, entry: dict) -> Player:
    return next(p for p in team.players if p.id == entry["player_id"])


def initialize_contracts(league: League, seed: int, ctx: ContractContext, prerun_years: int) -> float:
    """新規開始のとき、初期選手の契約を作り直す(D-232、D-356):年俸は見せる能力から算定(校正の後の能力で)、残りの年数は事前運転の契約から引き継ぐ
    (事前運転で契約を扱わなかったときは、更改と同じ方針の年数。主力級は 2〜3 年、ほかは 1 年。D-360)。
    資金を超える球団は、最低年俸を超える分を同じ割合で縮めて資金の中に収める(開始直後に選手を手放さなくて済むように)。
    1 シーズン目から数えた満了シーズンに付け替える。戻り値は単価。"""
    expected = {}
    for team in league.teams:
        for p in team.players:
            expected[p.id] = ctx.expected(p)[0]
    total_funds = ctx.total_cap() if ctx.funds else ctx.settings.base_budget * len(league.teams)
    ctx.rate = round(rate_for(list(expected.values()), total_funds, len(expected), ctx.settings), 4)
    for team in league.teams:
        salaries = {p.id: salary_for(expected[p.id], ctx.rate, ctx.settings) for p in team.players}
        cap = ctx.cap(team.id)
        if cap is not None:  # 育成選手の年俸も総年俸に入るので、その分を除いた資金に収める(D-375)
            cap -= sum(int(p.contract["salary"]) for p in team.development if p.contract)
        if cap is not None and sum(salaries.values()) > cap:
            mn = ctx.settings.minimum
            excess = sum(v - mn for v in salaries.values())
            room = cap - mn * len(salaries)
            scale = max(0.0, room / excess) if excess > 0 else 0.0
            step = ctx.settings.rounding
            salaries = {pid: max(mn, int((mn + (v - mn) * scale) // step) * step) for pid, v in salaries.items()}
        for p in team.players:
            years = contract_years(p.age, expected[p.id], ctx.settings)
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
    """セーブデータの状態から契約の文脈を作る(手続きがあればそのシードと区切りで評価を引く)。資金は状態の値を直接使う(増減は状態に残る)。"""
    from .negotiation import depth_slots

    from .facilities import league_upkeep

    ctx = ContractContext(state.contract_settings, state.team_funds, dict(state.budget_tiers), state_war_history(state), state.scout_sd_of, negotiation=state.negotiation_settings, league_seed=state.league.seed, slots=depth_slots(state.gen_config), upkeep=league_upkeep(state.league, state.facility_settings) if state.team_funds else {})
    if proc is not None:
        ctx.bind(proc)
        ctx.rate = float(proc.rate)
    return ctx


def over_cap(team: Team, ctx: ContractContext) -> int:
    """資金を超えている額(超えていなければ 0)。資金がなければ常に 0。"""
    cap = ctx.cap(team.id)
    if cap is None:
        return 0
    return max(0, team_salary(team) - cap)


def resolve_overrun(team: Team, proc: OffseasonProcedure, ctx: ContractContext, mins, min_batters) -> list[Player]:
    """資金の超過の解消(AI と同じ方針。D-255、D-361):見込みの WAR あたりの年俸(最低年俸を超える分)が高い選手から、超過が解消するまで自由契約。
    最低人数は守るが、守ったままでは解消できないときは最低人数を割っても外す(1 年契約で年俸が毎年算定し直されるため、予算の小さい強い球団で起こる。
    不足は完了のときに市場の売れ残りから最低年俸で取る。F3-2b、D-391)。"""
    out: list[Player] = []
    if not ctx.hard():
        return out
    while over_cap(team, ctx) > 0 and team.players:
        ok = releasable(team.players, mins, min_batters)
        candidates = [p for p in team.players if p.id in ok] or list(team.players)  # 最低人数の選手しか残っていなければ、最低人数を割っても外す(不足は完了のときに最低年俸で自動補充。F3-2b)

        def cost(p: Player) -> float:  # 最低年俸を超える分 ÷ 見込みの WAR(最低年俸の選手は外しても補充と同じ額なので最後。F3-2b)
            exp = max(0.05, ctx.expected(p)[0])
            return (int(p.contract["salary"]) - ctx.settings.minimum) / exp if p.contract else 0.0

        worst = max(candidates, key=lambda p: (cost(p), int(p.contract["salary"]) if p.contract else 0, p.id))
        salary = int(worst.contract["salary"]) if worst.contract else 0
        release_players(team, [worst], proc)
        proc.released[-1]["note"] = "budget"
        proc.budget_releases.append({"team_id": team.id, "player_id": worst.id, "name": worst.name, "role": worst.role, "position": worst.position, "age": worst.age, "salary": salary, "expected": round(ctx.expected(worst)[0], 2)})
        out.append(worst)
    return out


def can_afford(team: Team, salary: int, ctx: ContractContext | None, total: int | None = None) -> bool:
    """この年俸の契約を結んでも資金を超えないか(資金がなければ常に可)。total は今の総年俸(省略時は数える)。"""
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
