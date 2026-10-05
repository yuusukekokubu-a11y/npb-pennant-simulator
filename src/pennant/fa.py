"""FA(F3-2c。D-258〜D-264):一軍登録のシーズンの数え方・FA 権・宣言・提示ラウンド制の FA 市場・AI の提示。

- FA 権の年数 `Player.fa_seasons`:年度の確定のときに、そのシーズンの一軍(`Season.actives`。シーズンの最初に選ばれ、シーズン中は変わらない)
  に入っていた選手に 1 を足す(`count_active_seasons`)。設定の年数(7)以上で FA 権保持者。宣言したら 0 に戻す。
- 宣言(`declare`):更改の提示を断った FA 権保持者は、その場で宣言する(draft.make_offer から呼ぶ)。球団を離れ、契約は終わり、FA 市場へ。
- FA 市場(`close_round`):ラウンドごとに、全球団(あなたと AI)の提示を集め、見込みの WAR の高い選手から順に、空き枠と予算を満たす提示の中で
  志望の満足度(negotiation.judge。出場機会と勝利は提示した球団での見込み)が最も高いものを選び、しきい値以上なら成立。
  判定の乱数は derive_seed(手続きのシード, "fa:<選手>:<球団>:<ラウンド>")。最後のラウンドの後、残った選手は自由契約市場へ。
- 元の球団も他球団と同じ立場(優遇なし)。補償は `compensation`(初期は何もしない)の差し替え口。
- AI(`ai_offers`。D-261):自球団の評価で一軍に入る見込みがあり、見込みの WAR が設定値以上の選手を、見込みの高い順に 1 ラウンド設定値まで、
  空き枠と予算の範囲で。年俸は算定 × ラウンドの倍率(「なし」は算定どおり)、年数は更改と同じ方針。
- 主力級の期待(`star_multiplier`。D-322〜D-325):FA 権を持つ選手は、見込みの WAR が高いほど年俸の軸の基準を 算定 × 期待の倍率 にする
  (更改の判定と FA 市場の判定)。FA 市場の AI は、期待の倍率も掛けた年俸で提示する(「なし」は上限の倍率まで)。
"""

from __future__ import annotations

import random

from .contracts import set_contract, team_salary
from .models import League, Player, Team
from .negotiation import NegotiationSettings, depth_ranks, judge, money_axis_active
from .season import derive_seed

MAX_ROSTER = 70

DEFAULT_FA = {
    "seasons_required": 7,
    "threshold_add": 0.3,
    "rounds": 3,
    "round_multipliers": [1.0, 1.1, 1.2],
    "ai_max_offers": 3,
    "ai_min_expected": 0.5,
    "noise_sd": 0.1,
    "init": {"start_age": 21, "p_active": 0.7, "p_other": 0.25},
    "compensation": None,
    "star": None,  # 主力級の期待(D-322)。設定にない旧版のセーブデータは期待なし
}


def fa_settings(neg: NegotiationSettings | None) -> dict:
    """FA の設定(negotiation.json の fa。足りない項目は既定値)。"""
    out = dict(DEFAULT_FA)
    if neg is not None:
        out.update(neg.data.get("fa") or {})
    return out


def validate_fa(c, root: dict) -> None:
    """negotiation.json の fa の検証(negotiation.validate_negotiation_settings から呼ぶ)。"""
    fa = root.get("fa")
    if fa is None:
        return
    if not isinstance(fa, dict):
        c.add("fa", "まとまり({ })にしてください")
        return
    c.integer(fa.get("seasons_required"), "fa.seasons_required", 1, 30)
    c.number(fa.get("threshold_add"), "fa.threshold_add", -5, 5)
    rounds = c.integer(fa.get("rounds"), "fa.rounds", 1, 10)
    m = fa.get("round_multipliers")
    if not (isinstance(m, list) and m and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in m)):
        c.add("fa.round_multipliers", "正の数の一覧(ラウンドごと)にしてください")
    elif rounds is not None and len(m) < rounds:
        c.add("fa.round_multipliers", f"ラウンド数({rounds})以上の長さにしてください")
    c.integer(fa.get("ai_max_offers"), "fa.ai_max_offers", 0, 30)
    c.number(fa.get("ai_min_expected"), "fa.ai_min_expected", -10, 20)
    c.number(fa.get("noise_sd"), "fa.noise_sd", 0, 5)
    star = fa.get("star")
    if star is not None:
        if not isinstance(star, dict):
            c.add("fa.star", "まとまり({ })にしてください")
        else:
            c.number(star.get("start"), "fa.star.start", -5, 20)
            c.number(star.get("per_war"), "fa.star.per_war", 0, 5)
            c.number(star.get("max_multiplier"), "fa.star.max_multiplier", 1, 3)
            if not isinstance(star.get("reason"), str) or not star.get("reason", "").strip():
                c.add("fa.star.reason", "断られた理由に足す文を書いてください")
            limit = (root.get("money_none") or {}).get("max_ratio", 1.3)
            if isinstance(star.get("max_multiplier"), (int, float)) and isinstance(limit, (int, float)) and star["max_multiplier"] >= limit:
                c.add("fa.star.max_multiplier", f"「なし」の年俸の上限({limit} 倍)より小さくしてください(上限の中で引き留め・獲得ができるように。D-325)")
    init = fa.get("init")
    if not isinstance(init, dict):
        c.add("fa.init", "まとまり({ })にしてください")
    else:
        c.integer(init.get("start_age"), "fa.init.start_age", 15, 40)
        c.number(init.get("p_active"), "fa.init.p_active", 0, 1)
        c.number(init.get("p_other"), "fa.init.p_other", 0, 1)


# ---- FA 権の年数 ----

def seasons_of(player: Player) -> int:
    return int(player.fa_seasons or 0)


def is_holder(player: Player, neg: NegotiationSettings | None) -> bool:
    return seasons_of(player) >= int(fa_settings(neg)["seasons_required"])


def count_active_seasons(league: League, actives) -> int:
    """そのシーズンの一軍に入っていた選手に 1 シーズン足す(年度の確定のとき)。戻り値は数えた人数。"""
    ids = {p.id for a in actives.values() for p in a.players}
    n = 0
    for p in league.all_players():
        if p.id in ids:
            p.fa_seasons = seasons_of(p) + 1
            n += 1
    return n


def estimate_seasons(player: Player, league_seed: int, in_active: bool, past_counted: int, past_seasons: int, neg: NegotiationSettings | None) -> int:
    """年数がない選手の補い方(D-264):(21 歳から今までの年数 − 終えたシーズンの数)回のうち確率 p で数え、履歴で一軍とみなせるシーズンを足す。"""
    init = fa_settings(neg)["init"]
    span = max(0, int(player.age) - int(init["start_age"]) - int(past_seasons))
    p = float(init["p_active"] if in_active else init["p_other"])
    rng = random.Random(derive_seed(league_seed, f"fa-seasons:{player.id}"))
    return sum(1 for _ in range(span) if rng.random() < p) + int(past_counted)


def initialize_seasons(league: League, actives, league_seed: int, neg: NegotiationSettings | None, history=(), only_missing: bool = False) -> None:
    """新規開始の初期選手(only_missing=False)と、旧版のセーブデータの選手(only_missing=True)の年数を補う。
    history は終えたシーズンの集計(SeasonArchive)。その年の成績(打席か投球回)がある年を一軍として数える(旧版には一軍の名簿が残っていない)。"""
    active_ids = {p.id for a in actives.values() for p in a.players}
    archives = list(history)
    for p in league.all_players():
        if only_missing and p.fa_seasons is not None:
            continue
        counted = 0
        for a in archives:
            if p.id in a.records.batters and a.records.batters[p.id].get("PA", 0) > 0:
                counted += 1
            elif p.id in a.records.pitchers and a.records.pitchers[p.id].get("OUTS", 0) > 0:
                counted += 1
        p.fa_seasons = estimate_seasons(p, league_seed, p.id in active_ids, counted, len(archives), neg)


# ---- 主力級の期待(D-322〜D-325) ----

def star_multiplier(neg: NegotiationSettings | None, expected: float) -> float:
    """期待の倍率:FA 権を持つ(宣言した)選手は、見込みの WAR が高いほど自分の価値を算定より高く見る。
    倍率 = min(上限, 1 + 1 WAR あたりの上げ幅 × max(0, 見込みの WAR − 始まり))。年俸の軸の満足度の基準が 算定 × 倍率 になる。設定がなければ 1。"""
    star = fa_settings(neg).get("star")
    if not star:
        return 1.0
    m = 1.0 + float(star["per_war"]) * max(0.0, float(expected) - float(star["start"]))
    return min(float(star["max_multiplier"]), m)


# ---- 宣言 ----

def declare(team: Team, player: Player, entry: dict, proc, ctx) -> None:
    """FA 宣言:球団を離れ、契約は終わり、年数を 0 に戻して FA 市場へ(D-259)。"""
    team.players = [p for p in team.players if p.id != player.id]
    salary = int(player.contract["salary"]) if player.contract else None
    player.team_id = None
    player.contract = None
    player.fa_seasons = 0
    proc.fa_pool.append(player)
    proc.fa_info[player.id] = {
        "former_team": team.id, "name": player.name, "role": player.role, "position": player.position, "age": player.age,
        "old_salary": salary, "calc_salary": int(entry["auto_salary"]), "expected": float(entry["expected"]), "ai_years": int(entry.get("ai_years", 1)),
        "status": "open", "team_id": None,
    }
    entry["status"] = "declared"


# ---- FA 市場 ----

def _context(player: Player, team: Team, proc, ctx, sizes: dict[int, int]) -> dict:
    """提示した球団での出場機会(自分より評価の高い同じポジションの選手の数)と前年の順位。"""
    est = ctx.scout(player, team.id)[0]
    rank = sum(1 for q in team.players if q.position == player.position and ctx.scout(q, team.id)[0] > est)
    return {"rank": rank, "slots": round((ctx.slots or {}).get(player.position, 1.0), 3), "standing": proc.ranks.get(team.id), "league_size": sizes.get(team.league_index, 6)}


def round_salary_star(calc: int, multiplier: float, expect: float, ctx) -> int:
    """FA 市場の AI の提示の年俸:算定 × ラウンドの倍率 × 期待の倍率(D-325)。「なし」は算定の上限の倍率まで。"""
    salary = round_salary(calc, multiplier * expect, ctx.rule, ctx.settings.rounding, money_axis_active(ctx.negotiation, ctx.rule))
    if ctx.rule == "none" and ctx.negotiation is not None:
        top = int(calc * float(ctx.negotiation.money_none["max_ratio"]) // ctx.settings.rounding) * ctx.settings.rounding
        salary = max(int(calc), min(salary, top))
    return salary


def round_salary(calc: int, multiplier: float, rule: str, rounding: int, money_axis: bool = False) -> int:
    """AI の提示の年俸 = 算定 × ラウンドの倍率(丸め)。「なし」で年俸の軸が効かないときは算定どおり(D-273)。"""
    if rule == "none" and not money_axis:
        return int(calc)
    return max(int(calc), int(round(calc * multiplier / rounding)) * rounding)


def round_multiplier(neg: NegotiationSettings | None, rnd: int) -> float:
    fa = fa_settings(neg)
    return float(fa["round_multipliers"][min(int(rnd), len(fa["round_multipliers"])) - 1])


def choose_offers(team: Team, items, proc, ctx, league_sizes: dict[int, int], mult: float, expect=None) -> dict[str, tuple[int, int]]:
    """AI の方針で提示する選手を選ぶ(FA と市場で共通。D-261、D-300)。items は (選手, 見込みの WAR, 算定年俸, 年数) の並び。
    自球団の評価で一軍に入る見込みがあり、見込みの WAR が設定値以上の選手を、見込みの高い順に設定値まで、空き枠と予算の範囲で。
    expect(選手 ID → 期待の倍率)を渡すと(FA 市場)、主力級には期待の倍率を考えた年俸で提示する(D-325)。"""
    fa = fa_settings(ctx.negotiation)
    space = MAX_ROSTER - len(team.players)
    limit = min(int(fa["ai_max_offers"]), max(0, space))
    if limit <= 0:
        return {}
    cands = []
    for p, expected, calc, years in items:
        if float(expected) < float(fa["ai_min_expected"]):
            continue
        c = _context(p, team, proc, ctx, league_sizes)
        if c["rank"] >= c["slots"]:
            continue  # 自球団の評価で一軍に入る見込みがない
        cands.append((-float(expected), p.id, int(calc), int(years)))
    cands.sort()
    out: dict[str, tuple[int, int]] = {}
    total = team_salary(team)
    cap = ctx.cap(team.id)
    for _, pid, calc, years in cands:
        if len(out) >= limit:
            break
        e = expect(pid) if expect is not None else 1.0
        salary = round_salary_star(calc, mult, e, ctx) if e > 1.0 else round_salary(calc, mult, ctx.rule, ctx.settings.rounding, money_axis_active(ctx.negotiation, ctx.rule))
        if ctx.hard() and cap is not None and total + sum(s for _, s in out.values()) + salary > cap:
            continue
        out[pid] = (years, salary)
    return out


def ai_offers(team: Team, proc, ctx, league_sizes: dict[int, int]) -> dict[str, tuple[int, int]]:
    """AI の 1 ラウンドの提示(選手 ID → (年数, 年俸))。D-261。"""
    items = [(p, proc.fa_info[p.id]["expected"], proc.fa_info[p.id]["calc_salary"], proc.fa_info[p.id]["ai_years"]) for p in proc.fa_pool if proc.fa_info[p.id]["status"] == "open"]
    return choose_offers(team, items, proc, ctx, league_sizes, round_multiplier(ctx.negotiation, proc.fa_round), lambda pid: star_multiplier(ctx.negotiation, proc.fa_info[pid]["expected"]))


def compensation(proc, info: dict, team_id: str) -> None:
    """補償(人的・金銭)の差し替え口。初期はなし(D-260)。"""
    return None


def close_round(league: League, proc, ctx, my_team_id: str | None, my_ai: bool = False) -> list[dict]:
    """1 ラウンドを締める。あなたの提示(proc.fa_offers)と AI の提示を合わせて、選手が選ぶ。my_ai=True なら、あなたの球団も AI と同じ方針。
    戻り値はこのラウンドで成立した契約。"""
    neg = ctx.negotiation
    fa = fa_settings(neg)
    teams = {t.id: t for t in league.teams}
    sizes: dict[int, int] = {}
    for t in league.teams:
        sizes[t.league_index] = sizes.get(t.league_index, 0) + 1
    offers: dict[str, dict[str, tuple[int, int]]] = {}
    for team in league.teams:
        if team.id == my_team_id and not my_ai:
            mine = {pid: (int(o["years"]), int(o["salary"])) for pid, o in proc.fa_offers.items()}
        else:
            mine = ai_offers(team, proc, ctx, sizes)
        for pid, (years, salary) in mine.items():
            offers.setdefault(pid, {})[team.id] = (years, salary)
            proc.fa_log.append({"round": proc.fa_round, "player_id": pid, "team_id": team.id, "years": years, "salary": salary})
    signed = []
    rnd = int(proc.fa_round)
    open_players = sorted((p for p in proc.fa_pool if proc.fa_info[p.id]["status"] == "open"), key=lambda p: (-float(proc.fa_info[p.id]["expected"]), p.id))
    for p in open_players:
        info = proc.fa_info[p.id]
        best = None
        for tid in sorted(offers.get(p.id, {})):
            years, salary = offers[p.id][tid]
            team = teams[tid]
            if len(team.players) >= MAX_ROSTER:
                continue
            cap = ctx.cap(tid)
            if ctx.hard() and cap is not None and team_salary(team) + salary > cap:
                continue
            noise = random.Random(derive_seed(proc.seed, f"fa:{p.id}:{tid}:{rnd}")).gauss(0.0, float(fa["noise_sd"]))
            context = {**_context(p, team, proc, ctx, sizes), "salary_expect": star_multiplier(neg, info["expected"])}  # 主力級の期待(D-322)
            r = judge(neg, p.preference or {}, years, salary, int(info["calc_salary"]), p.age, context, ctx.rule, noise) if neg is not None else {"score": 0.0}
            if best is None or r["score"] > best[0]:
                best = (r["score"], tid, years, salary)
        info["offers"] = info.get("offers", 0) + len(offers.get(p.id, {}))
        if best is None or best[0] < 0.0:
            continue
        _, tid, years, salary = best
        team = teams[tid]
        p.team_id = tid
        p.state.fatigue = 0.0
        p.fa_seasons = 0
        team.players.append(p)
        set_contract(p, int(salary), int(years), proc.year + 1, "fa")
        info.update({"status": "signed", "team_id": tid, "years": int(years), "salary": int(salary), "round": rnd})
        compensation(proc, info, tid)
        row = {"round": rnd, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "former_team": info["former_team"], "team_id": tid, "years": int(years), "salary": int(salary), "offers": len(offers.get(p.id, {}))}
        proc.fa_results.append(row)
        signed.append(row)
    proc.fa_pool = [p for p in proc.fa_pool if proc.fa_info[p.id]["status"] == "open"]
    proc.fa_offers = {}
    proc.fa_round = rnd + 1
    if proc.fa_round > int(fa["rounds"]) or not proc.fa_pool:
        finish_market(proc)
    return signed


def finish_market(proc) -> None:
    """最後のラウンドの後:残った選手は自由契約市場へ(契約なし)。"""
    for p in proc.fa_pool:
        proc.fa_info[p.id]["status"] = "unsigned"
        proc.market.append(p)
    proc.fa_pool = []
    proc.fa_done = True


def run_all_rounds(league: League, proc, ctx, my_team_id: str | None, my_ai: bool = True) -> None:
    """残りのラウンドを最後まで(「おまかせ」「次の手続きへ」。あなたの球団も AI と同じ方針)。"""
    while not proc.fa_done:
        close_round(league, proc, ctx, my_team_id, my_ai)
