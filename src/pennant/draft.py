"""オフの手続き(F3-1。D-201〜D-207):契約(更改と自由契約)→ FA → ドラフト → 自由契約市場 → 自動補充。

年度の確定(加齢・能力の更新・引退)の後に、OffseasonProcedure を作って段階ごとに進める。
  - 契約(renewal / release):満了した選手は査定の年俸で自動で更改し、FA 権保持者は確率で宣言する。球団は選手を手放せる
    (contract_stage.py。作り直し②。AI の自由契約の方針は ai_release)
  - FA(fa):宣言した選手の提示ラウンド制の市場。選手は金額を中心に選ぶ(fa.py。D-258〜D-264、D-365)
  - ドラフト(draft):1 巡目は全球団の入札と抽選(run_bidding。D-369)。2 巡目から前年の勝率が低い球団から順に往復。
    空き枠・資金がない球団はパス。AI は ai_choose
  - 市場(market):手放された選手 + FA で決まらなかった選手 + 指名されなかった候補。年俸は自動で、各球団の獲得の希望を
    前年の順位が低い球団から 1 人ずつ(market.py。D-370)。残った選手はリーグを去る
  - 完了(done):70 人に満たない球団を自動補充(offseason.replenish。F2 の穴埋め)
AI の判断は、真の能力ではなく、球団ごとのスカウト評価(scouting.quick_value)を使う(D-205)。判断の関数は差し替え可能。
評価のずれ(sd)は {"common", "item"} の 2 層(D-212)。
操作する球団がないとき(観戦のみ)は、run_ai_offseason で一気に進める。事前運転も同じ関数を使う(D-210)。
"""

from __future__ import annotations

import random

from .abilities import BATTER, FIELDER_POSITIONS, PITCHER, PITCHER_POSITIONS
from .config import GenerationConfig, NameParts
from .contracts import set_contract, team_salary
from .models import League, Player, Team
from .names import NameGenerator
from .offseason import OffseasonSettings, PlayerNote, replenish
from .season import derive_seed
from .market import close_market, uses_offers

# 分けた先(procedure.py・contract_stage.py)の名前も、今までどおり draft から使えるようにする(D-302)
from .procedure import (  # noqa: F401
    SUPPORTED_FORMAT_VERSION,
    PHASES,
    PHASE_LABELS,
    STAGES,
    STAGE_LABELS,
    stage_of,
    MAX_ROSTER,
    DraftSettings,
    load_draft_settings,
    validate_draft_settings,
    minimum_positions,
    minimum_batters,
    position_counts,
    shortages,
    can_release,
    releasable,
    new_shortages,
    OffseasonProcedure,
    draft_order,
    candidate_slots,
    make_candidates,
    start_procedure,
    scout_report,
    scout_report_foreign,
    cached_value,
    ai_release,
    ai_value,
    surplus_positions,
    ai_choose,
    _entry_report,
    entry_summary,
    join,
    release_players,
    apply_ai_releases,
    pool_of,
    pass_turn,
    advance_turn,
    phase_finished,
    next_phase,
    joined_players,
)
from .contract_stage import (  # noqa: F401
    ContractContext,
    contract_scout,
    apply_contracts_start,
    CONTRACT_PHASES,
    finish_contract_stage,
    entry_player,
    update_funds,
    initialize_contracts,
    state_war_history,
    state_context,
    over_cap,
    resolve_overrun,
    can_afford,
    offer_for,
)


def _sign(proc: OffseasonProcedure, team: Team, player: Player, scout_sd: dict[str, dict], settings: DraftSettings, ctx: ContractContext | None, extra: dict | None = None) -> None:
    """球団が選手を指名(獲得)して契約する(順番は進めない)。ドラフトは巡ごとの年俸、市場は算定した年俸(D-235)。"""
    pool = pool_of(proc)
    offer = offer_for(team, player, proc, ctx)
    pool.remove(player)
    join(team, player, proc, scout_sd[team.id], settings)
    salary = None
    if offer is not None:
        salary, years, _ = offer
        set_contract(player, salary, years, proc.year + 1, proc.phase)
    proc.picks.append({"phase": proc.phase, "round": proc.round, "team_id": team.id, "player_id": player.id, "name": player.name, "role": player.role, "position": player.position, "age": player.age, "salary": salary, **entry_summary(player.scouting), **(extra or {})})


def take(proc: OffseasonProcedure, team: Team, player: Player, scout_sd: dict[str, dict], settings: DraftSettings, ctx: ContractContext | None = None) -> None:
    """今の球団が選手を指名(獲得)して契約し、次の順番へ。"""
    _sign(proc, team, player, scout_sd, settings, ctx)
    advance_turn(proc)


# ---- 1 巡目の入札と抽選(D-369) ----

def bid_pending(proc: OffseasonProcedure) -> list[str]:
    """1 巡目がまだ決まっていない球団(指名の順番の並び。パスした球団は決まった扱い)。"""
    done = {x["team_id"] for x in proc.picks if x["phase"] == "draft" and int(x["round"]) == 1}
    return [tid for tid in proc.order if tid not in done]


def _bid_pass(proc: OffseasonProcedure, team_id: str, reason: str) -> None:
    proc.picks.append({"phase": "draft", "round": 1, "team_id": team_id, "player_id": None, "name": "", "role": "", "position": "", "age": None, "note": reason, "step": proc.bid_step})


def bid_blocked(team: Team, proc: OffseasonProcedure, ctx: ContractContext | None) -> str | None:
    """入札できない理由(空き枠なし full・資金不足 budget・候補なし skip)。入札できるなら None。"""
    if len(team.players) >= MAX_ROSTER:
        return "full"
    if not proc.candidates:
        return "skip"
    if ctx is not None and ctx.hard() and not can_afford(team, ctx.settings.rookie_salary(1), ctx):
        return "budget"
    return None


def pass_bid(proc: OffseasonProcedure, team_id: str) -> None:
    """あなたの球団が 1 巡目の入札をしない(「パス」「次の手続きへ」)。"""
    if team_id in bid_pending(proc):
        _bid_pass(proc, team_id, "pass")


def run_bidding(league: League, proc: OffseasonProcedure, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, mins, min_batters, policy=ai_choose, ctx: ContractContext | None = None, my_bid: Player | None = None) -> bool:
    """1 巡目の入札を進める(D-369)。全球団が同時に 1 人ずつ入札し、同じ候補に重なったら抽選(乱数は derive_seed(手続きのシード,
    "lottery:<回>:<選手>"))。外れた球団は残りの候補から入札し直す。あなたの入札が要るときは True で止まる(my_bid を渡すと、それを入札にする)。
    全球団が決まったら 2 巡目へ進めて False。"""
    teams = {t.id: t for t in league.teams}
    while not proc.bid_done:
        for tid in bid_pending(proc):
            why = bid_blocked(teams[tid], proc, ctx)
            if why is not None:
                _bid_pass(proc, tid, why)
        pending = bid_pending(proc)
        if not pending:
            break
        bids: dict[str, Player] = {}
        if my_team_id in pending:
            if my_bid is None:
                return True
            bids[my_team_id] = my_bid
            my_bid = None
        for tid in pending:
            if tid == my_team_id:
                continue
            choice = policy(teams[tid], proc.candidates, proc, scout_sd[tid], settings, mins, min_batters, "draft")
            if choice is None:
                _bid_pass(proc, tid, "skip")
            else:
                bids[tid] = choice
        by_player: dict[str, list[str]] = {}
        for tid in proc.order:
            if tid in bids:
                by_player.setdefault(bids[tid].id, []).append(tid)
        for pid in sorted(by_player):
            tids = by_player[pid]
            player = bids[tids[0]]
            winner = tids[0] if len(tids) == 1 else random.Random(derive_seed(proc.seed, f"lottery:{proc.bid_step}:{pid}")).choice(tids)
            proc.lottery.append({"step": proc.bid_step, "player_id": pid, "name": player.name, "position": player.position, "teams": list(tids), "winner": winner})
            _sign(proc, teams[winner], player, scout_sd, settings, ctx, {"step": proc.bid_step, "lottery": len(tids) > 1})
        proc.bid_step += 1
    proc.bid_done = True
    proc.round = 2
    proc.index = 0
    return False


def affordable_pool(team: Team, proc: OffseasonProcedure, ctx: ContractContext | None) -> list[Player]:
    """標準以上で、上限の中で結べる選手だけ(ドラフトは巡ごとの年俸、市場は算定)。なし・ゆるいは全員。"""
    pool = pool_of(proc)
    if ctx is None or not ctx.hard():
        return pool
    total = team_salary(team)
    if proc.phase == "draft":
        return pool if can_afford(team, ctx.settings.rookie_salary(proc.round), ctx, total) else []
    return [p for p in pool if can_afford(team, ctx.salary(p, team.id)[0], ctx, total)]


def _choose_within_budget(team: Team, proc: OffseasonProcedure, sd: dict, settings: DraftSettings, mins, min_batters, ctx: ContractContext) -> tuple[Player | None, str]:
    """ai_choose を、上限の中で結べる選手だけから選ぶのと同じ結果で、速く行う(評価の高い順に、結べる最初の選手を探す)。
    戻り値は (選んだ選手, 選ばなかったときの理由 budget / skip)。"""
    pool = pool_of(proc)
    total = team_salary(team)
    if proc.phase == "draft":
        if not can_afford(team, ctx.settings.rookie_salary(proc.round), ctx, total):
            return None, "budget"
        choice = ai_choose(team, pool, proc, sd, settings, mins, min_batters, proc.phase)
        return choice, "skip"
    need = shortages(team.players, mins, min_batters)
    surplus = surplus_positions(team, settings.gen_config) if settings.gen_config is not None else set()
    ranked = sorted(pool, key=lambda p: (ai_value(p, team, proc, sd, settings, need, surplus), p.id), reverse=True)
    best = next((p for p in ranked if can_afford(team, ctx.salary(p, team.id)[0], ctx, total)), None)
    if best is None:
        return None, "budget"
    if not need:
        worst = min((cached_value(proc, p, team.id, sd)[0] for p in team.players), default=0.0)
        if cached_value(proc, best, team.id, sd)[0] < worst + float(settings.ai("market_gain_min")):
            return None, "skip"
    return best, "skip"


def run_ai_turns(league: League, proc: OffseasonProcedure, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, mins, min_batters, policy=ai_choose, ctx: ContractContext | None = None) -> bool:
    """AI の番を進める。自分の番が来たら True で止まる。段階の終わり(全巡終了か候補切れ)なら False。
    資金が足りない球団はパスする(note="budget")。ドラフトの 1 巡目は入札(run_bidding)で、あなたの入札が要るときも True で止まる。"""
    teams = {t.id: t for t in league.teams}
    if proc.bidding() and run_bidding(league, proc, scout_sd, settings, my_team_id, mins, min_batters, policy, ctx):
        return True
    while not phase_finished(proc):
        tid = proc.current_team()
        if tid is None:
            advance_turn(proc)
            continue
        if tid == my_team_id:
            if len(teams[tid].players) >= MAX_ROSTER:
                pass_turn(proc, tid, "full")  # 空き枠がなければ自動でパス(D-203)
                continue
            if proc.phase == "draft" and not affordable_pool(teams[tid], proc, ctx):
                pass_turn(proc, tid, "budget")  # 資金が足りなければ自動でパス(D-235)
                continue
            return True
        team = teams[tid]
        if len(team.players) >= MAX_ROSTER:
            pass_turn(proc, tid, "full")
            continue
        if policy is ai_choose and ctx is not None and ctx.hard() and pool_of(proc):
            choice, why = _choose_within_budget(team, proc, scout_sd[tid], settings, mins, min_batters, ctx)
            if choice is None:
                pass_turn(proc, tid, why)
            else:
                take(proc, team, choice, scout_sd, settings, ctx)
            continue
        pool = affordable_pool(team, proc, ctx)
        if not pool:
            pass_turn(proc, tid, "budget" if pool_of(proc) else "skip")
            continue
        choice = policy(team, pool, proc, scout_sd[tid], settings, mins, min_batters, proc.phase)
        if choice is None:
            pass_turn(proc, tid, "skip")
        else:
            take(proc, team, choice, scout_sd, settings, ctx)
    return False


def make_room(team: Team, count: int, proc: OffseasonProcedure, sd: dict, mins: dict[str, int], min_batters: int) -> list[Player]:
    """最低人数を満たす補充のために、空き枠が足りない分だけ、自球団の評価が低い順に選手を外す(不足を増やさない選手だけ)。
    外した選手はそのオフの終わりにリーグを去る(履歴には自由契約として残る)。"""
    out: list[Player] = []
    for p in sorted(team.players, key=lambda p: (cached_value(proc, p, team.id, sd)[0], p.id)):
        if len(out) >= count:
            break
        if can_release(team.players, p, mins, min_batters):
            release_players(team, [p], proc)
            proc.released[-1]["note"] = "room"
            out.append(p)
    return out


def finalize(league: League, proc: OffseasonProcedure, config: GenerationConfig, parts: NameParts, settings: DraftSettings, scout_sd: dict[str, dict], calibration: dict[str, float] | None, mins, min_batters, id_prefix: str = "Y", ctx: ContractContext | None = None) -> list[PlayerNote]:
    """完了:70 人に満たない球団を自動補充(最低人数を満たすポジションから、次に人数の目安との差が大きいポジション)。市場の残りはリーグを去る。"""
    from .development import take_for_slot

    rng = random.Random(derive_seed(proc.seed, "fill"))
    names = NameGenerator(parts, rng, {(p.family_name, p.given_name) for p in league.all_players() + league.development_players()})
    counter = [0]
    target = {pos: int(n) for pos, n in config["roster"]["pitchers"].items()}
    target.update({pos: int(n) for pos, n in config["roster"]["fielders"].items()})
    added: list[PlayerNote] = []
    for team in league.teams:
        slots: list[tuple[str, str]] = []
        current = position_counts(team.players)
        for pos, n in shortages(team.players, mins, min_batters).items():
            if pos == "batter":  # 野手の合計が足りないときは、人数の目安との差が大きい野手のポジションで埋める
                for _ in range(n):
                    fill_pos = max(FIELDER_POSITIONS, key=lambda k: (target[k] - current.get(k, 0), -list(target).index(k)))
                    slots.append((BATTER, fill_pos))
                    current[fill_pos] = current.get(fill_pos, 0) + 1
                continue
            slots += [(PITCHER if pos in PITCHER_POSITIONS else BATTER, pos)] * n
            current[pos] += n
        excess = len(team.players) + len(slots) - MAX_ROSTER
        if excess > 0:  # 70 人のまま最低人数が足りない球団は、評価の低い選手を外して枠を空ける(上限 70 人と最低人数の両方を守る。D-203)
            make_room(team, excess, proc, scout_sd[team.id], mins, min_batters)
            current = position_counts(team.players)
            for _, pos in slots:
                current[pos] = current.get(pos, 0) + 1
        while len(team.players) + len(slots) < MAX_ROSTER:
            pos = max(target, key=lambda k: (target[k] - current.get(k, 0), -list(target).index(k)))
            slots.append((PITCHER if pos in PITCHER_POSITIONS else BATTER, pos))
            current[pos] = current.get(pos, 0) + 1
        rest = []
        for role, pos in slots:  # 育成選手のうち、同じポジションで推定値の高い選手を先に支配下に上げる(D-375)
            if take_for_slot(team, role, pos, proc, scout_sd[team.id], ctx) is None:
                rest.append((role, pos))
        for p in replenish(team, rest, config, names, rng, proc.year, counter, calibration, id_prefix):
            p.scouting = _entry_report(proc, p, team.id, scout_sd[team.id], settings)
            salary = None
            if ctx is not None:  # 自動補充は最低年俸(資金に関わらず結べる。D-235)
                salary = ctx.settings.minimum
                set_contract(p, salary, ctx.settings.default_years, proc.year + 1, "fill")
            added.append(PlayerNote(p.id, p.name, team.id, p.role, p.position, p.age, p.origin))
            proc.filled.append({"phase": "fill", "round": 0, "team_id": team.id, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "salary": salary, **entry_summary(p.scouting)})
    proc.market = []
    proc.candidates = []
    proc.phase = "done"
    return added


def run_ai_offseason(league: League, seed: int, config: GenerationConfig, parts: NameParts, offseason_settings: OffseasonSettings, settings: DraftSettings, year: int, calibration: dict[str, float] | None, scout_sd: dict[str, dict], records_or_order, id_prefix: str = "D", game_config=None, ctx: ContractContext | None = None) -> tuple[OffseasonProcedure, list[PlayerNote]]:
    """観戦のみ(全球団 AI)の手続きを一気に進める(事前運転・指紋 (n)・観戦のみの年度の確定で使う)。
    records_or_order は 球団 → (勝, 敗) か、1 巡目の順番の一覧。戻り値は手続きと、入団した全選手。"""
    mins = minimum_positions(game_config)
    min_batters = minimum_batters(game_config)
    if isinstance(records_or_order, list):
        proc = start_procedure(league, seed, config, parts, settings, year, calibration, None, id_prefix)
        proc.order = list(records_or_order)
    else:
        proc = start_procedure(league, seed, config, parts, settings, year, calibration, records_or_order, id_prefix)
    proc.prerun = id_prefix == "B"
    if ctx is not None:
        apply_contracts_start(league, proc, ctx, None, mins, min_batters)
    filled = complete(league, proc, config, parts, settings, scout_sd, calibration, None, mins, min_batters, "Y" if id_prefix == "D" else id_prefix, ctx)
    return proc, joined_players(proc) + filled


def finish_fa_market(proc: OffseasonProcedure) -> None:
    from .fa import finish_market

    if not proc.fa_done:
        finish_market(proc)


def complete(league: League, proc: OffseasonProcedure, config: GenerationConfig, parts: NameParts, settings: DraftSettings, scout_sd: dict[str, dict], calibration, my_team_id: str | None, mins, min_batters, fill_prefix: str = "Y", ctx: ContractContext | None = None) -> list[PlayerNote]:
    """残りの手続きを AI の方針で最後まで進める(「おまかせ」。自分の球団も AI と同じ方針。資金の超過の解消も AI と同じ)。戻り値は自動補充で入った選手。"""
    while proc.phase != "done":
        if proc.phase in CONTRACT_PHASES:  # 「おまかせ」:残りの交渉・超過の解消・自由契約を AI と同じ方針で(D-251、D-272)
            finish_contract_stage(league, proc, ctx, scout_sd, settings, my_team_id, mins, min_batters, my_ai=True)
        elif proc.phase == "fa":
            if ctx is not None:
                from .fa import run_all_rounds

                run_all_rounds(league, proc, ctx, my_team_id, my_ai=True)
            else:
                finish_fa_market(proc)
            next_phase(proc)
        elif proc.phase == "foreign":  # 外国人市場(D-376)
            if not proc.foreign_done:
                from .foreign import close_foreign

                close_foreign(league, proc, ctx, my_team_id, scout_sd, settings, mins, min_batters, my_ai=True)
            next_phase(proc)
        elif proc.phase == "dev_draft":  # 育成ドラフト(D-375)
            from .development import run_dev_turns

            run_dev_turns(league, proc, scout_sd, settings, my_team_id, ctx, my_ai=True)
            next_phase(proc)
        elif proc.phase == "market" and uses_offers(ctx):  # 市場は年俸が自動で、前年の順位が低い球団から 1 人ずつ(D-370)
            if not proc.market_done:
                close_market(league, proc, ctx, my_team_id, scout_sd, settings, my_ai=True)
            next_phase(proc)
        elif proc.phase in ("draft", "market"):
            run_ai_turns(league, proc, scout_sd, settings, None, mins, min_batters, ctx=ctx)
            next_phase(proc)
        else:
            break
    return finalize(league, proc, config, parts, settings, scout_sd, calibration, mins, min_batters, fill_prefix, ctx)
