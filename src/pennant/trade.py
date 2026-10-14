"""トレード(D-378):支配下の選手どうしの交換(1〜3 人 対 1〜3 人)。お金は付けない。

- 選手の価値 = 見込みの WAR − 価値の年俸の重み ×(年俸 − 最低年俸)÷ 単価 + 若さの上乗せ(若い年齢より下の 1 歳ごとに)。
  見込みの WAR は契約と同じ(ContractContext.expected。成績と見せる能力から。球団によらない)。
- 成り立つ条件(check):両球団の選手であること・育成選手でないこと・出せない選手(locked)でないこと・人数(各 1〜max_players)・
  交換の後も 70 人以内・最低人数の不足を増やさない・総年俸が資金を超えない(すでに超えていれば増やさない)。
- AI の判断(ai_answer):受け取る価値の合計 + 手薄なポジションが埋まる加点 ≥ 出す価値の合計 + 余裕 なら受ける。
- AI どうし(ai_trades)・AI からあなたへの提案(make_proposal):足りないポジション(人数の目安より少ない)を、余っている球団から 1 対 1 で埋める。
  相手には、余っているポジションの選手から価値が釣り合う選手を出す。両方が受けるときだけ成立(提案は AI 側が受けるものだけ)。
- 実行(execute)は選手の所属を移し、記録を返す。シーズン中の一軍の選び直しは season.py、オフの段階は procedure.py。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json
from .contracts import team_salary
from .models import League, Player, Team
from .procedure import MAX_ROSTER, new_shortages

SUPPORTED_FORMAT_VERSION = 1


@dataclass(frozen=True)
class TradeSettings:
    data: dict
    source: str

    @property
    def max_players(self) -> int:
        return int(self.data["max_players"])

    @property
    def deadline_ratio(self) -> float:
        return float(self.data["deadline_ratio"])

    def value(self, key: str) -> float:
        return float(self.data["value"][key])

    def ai(self, key: str):
        return self.data["ai"][key]


def load_trade_settings(path: str | Path | None = None) -> TradeSettings:
    data, source = _read_json(path, "trade.json")
    return validate_trade_settings(data, source)


def validate_trade_settings(data, source: str = "(辞書)") -> TradeSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    c.integer(c.get(root, "max_players", ""), "max_players", 1, 10)
    c.number(c.get(root, "deadline_ratio", ""), "deadline_ratio", 0, 1)
    v = c.section(c.get(root, "value", ""), "value")
    c.number(c.get(v, "salary_weight", "value"), "value.salary_weight", 0, 5)
    c.integer(c.get(v, "young_age", "value"), "value.young_age", 15, 45)
    c.number(c.get(v, "young_per_year", "value"), "value.young_per_year", 0, 2)
    a = c.section(c.get(root, "ai", ""), "ai")
    c.number(c.get(a, "accept_margin", "ai"), "ai.accept_margin", -5, 5)
    c.number(c.get(a, "need_bonus", "ai"), "ai.need_bonus", 0, 5)
    checks = c.get(a, "season_checks", "ai")
    if checks is not None and (not isinstance(checks, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x < 1 for x in checks)):
        c.add("ai.season_checks", "0 より大きく 1 より小さい数(全日程の割合)の並びにしてください")
    c.integer(c.get(a, "season_ai_trades", "ai"), "ai.season_ai_trades", 0, 20)
    c.number(c.get(a, "season_proposal_probability", "ai"), "ai.season_proposal_probability", 0, 1)
    c.integer(c.get(a, "offseason_ai_trades", "ai"), "ai.offseason_ai_trades", 0, 20)
    c.integer(c.get(a, "offseason_proposals", "ai"), "ai.offseason_proposals", 0, 10)
    c.number(c.get(a, "offseason_proposal_probability", "ai"), "ai.offseason_proposal_probability", 0, 1)
    c.integer(c.get(a, "attempts", "ai"), "ai.attempts", 1, 1000)
    if c.problems:
        raise ConfigError(source, c.problems)
    return TradeSettings(copy.deepcopy(root), source)


_DEFAULT: list[TradeSettings] = []


def default_settings() -> TradeSettings:
    if not _DEFAULT:
        _DEFAULT.append(load_trade_settings())
    return _DEFAULT[0]


# ---- 価値 ----

def player_value(player: Player, ctx, rate: float, settings: TradeSettings | None = None) -> float:
    """選手の価値(見込みの WAR − 年俸の分 + 若さの上乗せ)。"""
    s = settings or default_settings()
    exp = float(ctx.expected(player)[0])
    salary = int(player.contract["salary"]) if player.contract else int(ctx.settings.minimum)
    cost = s.value("salary_weight") * max(0, salary - int(ctx.settings.minimum)) / rate if rate > 0 else 0.0
    young = max(0, int(s.value("young_age")) - int(player.age)) * s.value("young_per_year")
    return exp - cost + young


def roster_target(config) -> dict[str, int]:
    target = {pos: int(n) for pos, n in config["roster"]["pitchers"].items()}
    target.update({pos: int(n) for pos, n in config["roster"]["fielders"].items()})
    return target


def need_bonus(team: Team, gives: list[Player], gets: list[Player], config, settings: TradeSettings) -> float:
    """手薄なポジションが埋まる加点:交換の前に人数の目安より少ないポジションに、選手が入るなら(ポジションごとに 1 回)。"""
    target = roster_target(config)
    count: dict[str, int] = {}
    for p in team.players:
        count[p.position] = count.get(p.position, 0) + 1
    filled = {p.position for p in gets if count.get(p.position, 0) < target.get(p.position, 0)}
    lost = {p.position for p in gives if count.get(p.position, 0) <= target.get(p.position, 0)} - {p.position for p in gets}
    return float(settings.ai("need_bonus")) * (len(filled) - len(lost))


# ---- 条件 ----

def locked_ids(proc=None, season_trades=()) -> set[str]:
    """出せない選手:そのオフに入った選手(手続きの記録から)と、そのシーズン・オフにトレードで入った選手(D-378)。"""
    out: set[str] = set()
    for x in season_trades:
        out.update(x["gives"])  # 受け取った側から見て入った選手
        out.update(x["gets"])
    if proc is not None:
        out.update(x["player_id"] for x in proc.picks if x.get("player_id"))
        out.update(x["player_id"] for x in proc.fa_results)
        out.update(x["player_id"] for x in proc.foreign_results)
        out.update(x["player_id"] for x in proc.market_results)
        out.update(x["player_id"] for x in proc.promotions)
        for x in proc.trades:
            out.update(x["gives"])
            out.update(x["gets"])
    return out


def check(team_a: Team, team_b: Team, gives: list[str], gets: list[str], ctx, locked: set[str], mins, min_batters, settings: TradeSettings | None = None) -> str | None:
    """team_a が gives を出して team_b から gets を受け取る交換が成り立つか。成り立たなければ理由(画面に出す文)。"""
    s = settings or default_settings()
    if team_a.id == team_b.id:
        return "同じ球団どうしでは交換できません"
    if not (1 <= len(gives) <= s.max_players and 1 <= len(gets) <= s.max_players):
        return f"出す選手・受け取る選手は、それぞれ 1〜{s.max_players} 人にしてください"
    if len(set(gives)) != len(gives) or len(set(gets)) != len(gets):
        return "同じ選手が 2 回入っています"
    a_ids = {p.id for p in team_a.players}
    b_ids = {p.id for p in team_b.players}
    if not set(gives) <= a_ids:
        return "出す選手が、自分の球団の支配下にいません(育成選手は出せません)"
    if not set(gets) <= b_ids:
        return "受け取る選手が、相手の球団の支配下にいません(育成選手は受け取れません)"
    blocked = [pid for pid in gives + gets if pid in locked]
    if blocked:
        return "そのオフ(シーズン)に入ったばかりの選手は、次の開幕まで出せません"
    give_p = [p for p in team_a.players if p.id in gives]
    get_p = [p for p in team_b.players if p.id in gets]
    after_a = [p for p in team_a.players if p.id not in gives] + get_p
    after_b = [p for p in team_b.players if p.id not in gets] + give_p
    for team, after in ((team_a, after_a), (team_b, after_b)):
        if len(after) > MAX_ROSTER:
            return f"{team.name} の支配下が {MAX_ROSTER} 人を超えます"
        if new_shortages(team.players, after, mins, min_batters):
            return f"{team.name} の最低人数(ポジション)が足りなくなります"
        cap = ctx.cap(team.id) if ctx is not None else None
        if ctx is not None and ctx.hard() and cap is not None:
            now = team_salary(team)
            dev = sum(int(p.contract["salary"]) for p in team.development if p.contract)
            new = dev + sum(int(p.contract["salary"]) for p in after if p.contract)
            if new > cap and new > now:
                return f"{team.name} の総年俸が資金を超えます"
    return None


def ai_answer(team: Team, gives: list[Player], gets: list[Player], ctx, rate: float, config, settings: TradeSettings | None = None) -> tuple[bool, str]:
    """AI 球団 team が gives を出して gets を受け取るかどうかと、理由の一言。"""
    s = settings or default_settings()
    out_v = sum(player_value(p, ctx, rate, s) for p in gives)
    in_v = sum(player_value(p, ctx, rate, s) for p in gets)
    bonus = need_bonus(team, gives, gets, config, s)
    if in_v + bonus >= out_v + float(s.ai("accept_margin")):
        return True, "受けます"
    if bonus < 0:
        return False, "そのポジションの選手を出すと手薄になります"
    return False, "釣り合いません(こちらが出す選手のほうが価値が高い)"


# ---- 実行 ----

def execute(team_a: Team, team_b: Team, gives: list[str], gets: list[str], year: int, day: int | None, by: str) -> dict:
    """交換を行い、記録を返す({year, day, team_a, team_b, gives(a → b), gets(b → a), names, by})。"""
    give_p = [p for p in team_a.players if p.id in gives]
    get_p = [p for p in team_b.players if p.id in gets]
    team_a.players = [p for p in team_a.players if p.id not in gives]
    team_b.players = [p for p in team_b.players if p.id not in gets]
    for p in give_p:
        p.team_id = team_b.id
        team_b.players.append(p)
    for p in get_p:
        p.team_id = team_a.id
        team_a.players.append(p)
    return {
        "year": int(year), "day": day, "team_a": team_a.id, "team_b": team_b.id,
        "gives": [p.id for p in give_p], "gets": [p.id for p in get_p],
        "give_names": [p.name for p in give_p], "get_names": [p.name for p in get_p],
        "give_positions": [p.position for p in give_p], "get_positions": [p.position for p in get_p],
        "by": by,
    }


# ---- AI の組み立て ----

def _counts(team: Team) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in team.players:
        out[p.position] = out.get(p.position, 0) + 1
    return out


def find_deal(rng: random.Random, team_a: Team, partners: list[Team], ctx, rate: float, config, locked: set[str], mins, min_batters, settings: TradeSettings, need_b_accept: bool = True) -> tuple[Team, list[str], list[str]] | None:
    """team_a の足りないポジションを、余っている相手から 1 対 1 で埋める交換を探す。見つからなければ None。
    need_b_accept=False なら相手(あなたの球団)の答えは見ない(提案にする)。"""
    target = roster_target(config)
    ca = _counts(team_a)
    wants = sorted((pos for pos, n in target.items() if ca.get(pos, 0) < n), key=lambda pos: (ca.get(pos, 0) - target[pos], pos))
    spares = [pos for pos, n in target.items() if ca.get(pos, 0) > n]
    if not wants or not spares:
        return None
    order = list(partners)
    rng.shuffle(order)
    for pos in wants:
        for team_b in order:
            cb = _counts(team_b)
            if cb.get(pos, 0) < target[pos] or (need_b_accept and cb.get(pos, 0) == target[pos]):  # 提案はあなたが決めるので、目安ちょうどの球団にも持ちかける
                continue
            gets = sorted((p for p in team_b.players if p.position == pos and p.id not in locked), key=lambda p: (player_value(p, ctx, rate, settings), p.id))
            if not gets:
                continue
            want = gets[len(gets) // 2]  # 相手の余っているポジションの、真ん中くらいの選手
            wv = player_value(want, ctx, rate, settings)
            fits = {q for q, n in target.items() if cb.get(q, 0) <= n} if need_b_accept else set(target)  # 相手に足りないポジションの選手を出す(提案は問わない)
            offers = [p for p in team_a.players if p.position in spares and p.position in fits and p.id not in locked]
            offers.sort(key=lambda p: (abs(player_value(p, ctx, rate, settings) - wv - float(settings.ai("accept_margin"))), p.id))
            for give in offers[:3]:
                if check(team_a, team_b, [give.id], [want.id], ctx, locked, mins, min_batters, settings):
                    continue
                ok_a, _ = ai_answer(team_a, [give], [want], ctx, rate, config, settings)
                if not ok_a:
                    continue
                if need_b_accept:
                    ok_b, _ = ai_answer(team_b, [want], [give], ctx, rate, config, settings)
                    if not ok_b:
                        continue
                return team_b, [give.id], [want.id]
    return None


def ai_trades(rng: random.Random, league: League, my_team_id: str | None, count: int, ctx, rate: float, config, locked: set[str], mins, min_batters, year: int, day: int | None, settings: TradeSettings | None = None) -> list[dict]:
    """AI どうしのトレードを最大 count 件行う。戻り値は記録の一覧。"""
    s = settings or default_settings()
    teams = [t for t in league.teams if t.id != my_team_id]
    out: list[dict] = []
    tries = 0
    while len(out) < count and tries < int(s.ai("attempts")):
        tries += 1
        a = teams[int(rng.random() * len(teams))]
        deal = find_deal(rng, a, [t for t in teams if t.id != a.id], ctx, rate, config, locked, mins, min_batters, s)
        if deal is None:
            continue
        b, gives, gets = deal
        rec = execute(a, b, gives, gets, year, day, "ai")
        locked.update(gives + gets)
        out.append(rec)
    return out


def make_proposal(rng: random.Random, league: League, my_team_id: str, ctx, rate: float, config, locked: set[str], mins, min_batters, year: int, day: int | None, number: int, settings: TradeSettings | None = None) -> dict | None:
    """AI 球団からあなたへの提案を 1 つ作る(AI 側が受ける交換だけ)。提案は {id, team_id, gives(AI が出す), gets(あなたが出す), ...}。"""
    s = settings or default_settings()
    mine = next(t for t in league.teams if t.id == my_team_id)
    teams = [t for t in league.teams if t.id != my_team_id]
    order = list(teams)
    rng.shuffle(order)
    for a in order[: int(s.ai("attempts"))]:
        deal = find_deal(rng, a, [mine], ctx, rate, config, locked, mins, min_batters, s, need_b_accept=False)
        if deal is None:
            continue
        _, gives, gets = deal
        pa = {p.id: p for p in a.players}
        pm = {p.id: p for p in mine.players}
        return {"id": f"P{year}-{day if day is not None else 'off'}-{number}", "year": int(year), "day": day, "team_id": a.id,
                "gives": gives, "gets": gets, "give_names": [pa[x].name for x in gives], "get_names": [pm[x].name for x in gets]}
    return None


# ---- オフのトレードの段階・シーズン中の決まった日 ----

def start_offseason_trades(league: League, proc, ctx, config, my_team_id: str | None, mins, min_batters, proposals: bool = True, settings: TradeSettings | None = None) -> None:
    """オフのトレードの段階の始め:AI どうしのトレードと、AI からあなたへの提案を作る(一度だけ)。"""
    from .season import derive_seed

    if proc.trade_started:
        return
    s = settings or default_settings()
    proc.trade_started = True
    if ctx is None:
        return
    rng = random.Random(derive_seed(proc.seed, "trade"))
    locked = locked_ids(proc)
    rate = float(proc.rate) or 1.0
    proc.trades.extend(ai_trades(rng, league, my_team_id, int(s.ai("offseason_ai_trades")), ctx, rate, config, locked, mins, min_batters, proc.year, None, s))
    if proposals and my_team_id is not None:
        for i in range(int(s.ai("offseason_proposals"))):
            if rng.random() >= float(s.ai("offseason_proposal_probability")):
                continue
            prop = make_proposal(rng, league, my_team_id, ctx, rate, config, locked, mins, min_batters, proc.year, None, i + 1, s)
            if prop is not None and not any(p["gets"] == prop["gets"] for p in proc.trade_offers):
                proc.trade_offers.append(prop)


def deadline_day(total_days: int, settings: TradeSettings | None = None) -> int:
    """シーズン中のトレードの期限(この日数の試合をするまで。0 から数えた日数)。"""
    s = settings or default_settings()
    return int(total_days * s.deadline_ratio)


def season_check_days(total_days: int, settings: TradeSettings | None = None) -> list[int]:
    """シーズン中に AI どうしのトレードと提案を行う日(その日数の試合をした後)。"""
    s = settings or default_settings()
    return sorted({max(1, int(total_days * float(x))) for x in s.ai("season_checks")})
