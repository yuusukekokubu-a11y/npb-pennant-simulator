"""契約と年俸(F3-2a。D-230〜D-237。作り直し②で資金と査定に作り直し。D-353〜D-361)。

- 年俸(架空の「万円」)は見える情報だけで決める:見込みの WAR = 成績の分(直近 3 シーズンの WAR の加重平均。新しいほど重く、出場の少ない年は軽い)と
  能力の分(見せる能力の平均から設定の直線で求めた WAR の目安)を重みで混ぜた値。30 歳以上は年齢で割り引く。
  年俸 = 最低年俸 + 単価 × max(0, 見込み) を丸めたもの(下限は最低年俸)。更改では前の年俸の 75% を下回らない(D-356、D-357)。
- 単価(1 WAR あたり)は毎年のオフに、(資金の合計 ÷ 1.1 − 最低年俸 × 選手数)÷ リーグの見込みの WAR の合計 で求める(D-355)。
- 資金:球団の規模(大・中・小)ごとの基準額。毎年のオフの最初に、前のシーズンの順位で増減する(D-353、D-354)。
- 契約の乱数は別の系列(contract:…)で、AI の判断には使わない(D-237)。
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json
from .models import Player, Team
from .season import derive_seed

TIERS = ("large", "medium", "small")
TIER_LABELS = {"large": "大", "medium": "中", "small": "小"}


@dataclass(frozen=True)
class ContractSettings:
    data: dict
    source: str

    @property
    def minimum(self) -> int:
        return int(self.data["salary"]["minimum"])

    @property
    def rounding(self) -> int:
        return int(self.data["salary"]["rounding"])

    @property
    def weights(self) -> list[float]:
        return [float(w) for w in self.data["salary"]["war_weights"]]

    def usage_full(self, role: str) -> float:
        return float(self.data["salary"]["usage_full"][role])

    @property
    def age_discount(self) -> dict:
        return self.data["salary"]["age_discount"]

    @property
    def ability_weight(self) -> float:
        """能力の分の重み(成績の重みと同じ単位。直近の満の出場 1 シーズンが war_weights[0])。D-356。"""
        return float(self.data["salary"]["ability_weight"])

    @property
    def decline_floor(self) -> float:
        """更改の年俸の下限(前の年俸に対する割合。D-357)。"""
        return float(self.data["salary"]["decline_floor"])

    @property
    def base_budget(self) -> int:
        return int(self.data["budget"]["base"])

    def tier_factor(self, tier: str) -> float:
        return float(self.data["budget"]["tiers"][tier])

    @property
    def tier_counts(self) -> dict[str, int]:
        return {t: int(n) for t, n in self.data["budget"]["tier_counts"].items()}

    @property
    def payroll_divisor(self) -> float:
        return float(self.data["budget"]["payroll_divisor"])

    @property
    def change_by_rank(self) -> list[float]:
        return [float(x) for x in self.data["budget"]["change_by_rank"]]

    @property
    def funds_range(self) -> tuple[float, float]:
        lo, hi = self.data["budget"]["range"]
        return float(lo), float(hi)

    def rookie_salary(self, round_no: int) -> int:
        table = self.data["rookie_salaries"]
        key = str(min(int(round_no), max(int(k) for k in table)))
        return int(table[key])

    @property
    def rookie_years(self) -> int:
        return int(self.data["years"]["rookie"])

    @property
    def default_years(self) -> int:
        return int(self.data["years"]["default"])

    @property
    def max_years(self) -> int:
        return int(self.data["years"]["max"])

    @property
    def min_years(self) -> int:
        return int(self.data["years"]["min"])

    @property
    def multi_year(self) -> dict:
        return self.data["years"]["multi_year"]

    def ability_war(self, role: str) -> dict:
        return self.data["ability_war"][role]


def load_contract_settings(path: str | Path | None = None) -> ContractSettings:
    data, source = _read_json(path, "contracts.json")
    return validate_contract_settings(data, source)


def validate_contract_settings(root, source: str = "contracts.json") -> ContractSettings:
    c = _Checker()
    if not isinstance(root, dict):
        raise ConfigError(source, ["全体がまとまり({ })ではありません"])
    b = c.section(c.get(root, "budget", ""), "budget")
    c.integer(c.get(b, "base", "budget"), "budget.base", 1, 100000000)
    c.number(c.get(b, "payroll_divisor", "budget"), "budget.payroll_divisor", 1, 3)
    tiers = c.section(c.get(b, "tiers", "budget"), "budget.tiers")
    counts = c.section(c.get(b, "tier_counts", "budget"), "budget.tier_counts")
    for t in TIERS:
        c.number(c.get(tiers, t, "budget.tiers"), f"budget.tiers.{t}", 0.1, 3)
        c.integer(c.get(counts, t, "budget.tier_counts"), f"budget.tier_counts.{t}", 0, 12)
    if counts is not None and all(isinstance(counts.get(t), int) for t in TIERS) and sum(counts[t] for t in TIERS) != 12:
        c.add("budget.tier_counts", f"合計が 12 球団になるようにしてください(値: {sum(counts[t] for t in TIERS)})")
    ch = c.get(b, "change_by_rank", "budget")
    if not (isinstance(ch, list) and ch and all(isinstance(x, (int, float)) and not isinstance(x, bool) and -1 < x < 1 for x in ch)):
        c.add("budget.change_by_rank", "順位ごとの増減の割合(−1〜1 の数)の一覧にしてください(1 位から順)")
    rg = c.get(b, "range", "budget")
    if not (isinstance(rg, list) and len(rg) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in rg) and 0 < rg[0] <= 1 <= rg[1] <= 3):
        c.add("budget.range", "[下限, 上限] の 2 つの数(0 < 下限 ≦ 1 ≦ 上限 ≦ 3)にしてください")
    s = c.section(c.get(root, "salary", ""), "salary")
    c.integer(c.get(s, "minimum", "salary"), "salary.minimum", 1, 1000000)
    c.integer(c.get(s, "rounding", "salary"), "salary.rounding", 1, 100000)
    c.number(c.get(s, "ability_weight", "salary"), "salary.ability_weight", 0, 10)
    c.number(c.get(s, "decline_floor", "salary"), "salary.decline_floor", 0, 1)
    w = c.get(s, "war_weights", "salary")
    if not (isinstance(w, list) and w and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in w)):
        c.add("salary.war_weights", "正の数の一覧(新しいシーズンから順)にしてください")
    uf = c.section(c.get(s, "usage_full", "salary"), "salary.usage_full")
    for role in ("batter", "pitcher"):
        c.number(c.get(uf, role, "salary.usage_full"), f"salary.usage_full.{role}", 1, 10000)
    ad = c.section(c.get(s, "age_discount", "salary"), "salary.age_discount")
    c.integer(c.get(ad, "start", "salary.age_discount"), "salary.age_discount.start", 18, 60)
    c.number(c.get(ad, "per_year", "salary.age_discount"), "salary.age_discount.per_year", 0, 1)
    c.number(c.get(ad, "floor", "salary.age_discount"), "salary.age_discount.floor", 0, 1)
    y = c.section(c.get(root, "years", ""), "years")
    for key in ("min", "max", "default", "rookie"):
        c.integer(c.get(y, key, "years"), f"years.{key}", 1, 10)
    my = c.section(c.get(y, "multi_year", "years"), "years.multi_year")
    c.integer(c.get(my, "max_age", "years.multi_year"), "years.multi_year.max_age", 18, 60)
    table = c.get(my, "table", "years.multi_year")
    if not (isinstance(table, list) and all(isinstance(r, list) and len(r) == 2 and isinstance(r[0], (int, float)) and isinstance(r[1], int) and 1 <= r[1] <= 10 for r in table)):
        c.add("years.multi_year.table", "[見込みの WAR の下限, 年数] の一覧(高い順)にしてください")
    rs = c.section(c.get(root, "rookie_salaries", ""), "rookie_salaries")
    if rs is not None:
        for k, v in rs.items():
            c.integer(v, f"rookie_salaries.{k}", 1, 1000000)
    aw = c.section(c.get(root, "ability_war", ""), "ability_war")
    for role in ("batter", "pitcher"):
        r = c.section(c.get(aw, role, "ability_war"), f"ability_war.{role}")
        c.number(c.get(r, "intercept", f"ability_war.{role}"), f"ability_war.{role}.intercept", -10, 10)
        c.number(c.get(r, "slope", f"ability_war.{role}"), f"ability_war.{role}.slope", 0, 5)
    if c.problems:
        raise ConfigError(source, c.problems)
    return ContractSettings(copy.deepcopy(root), source)


# ---- 見込みの WAR・年俸・契約年数 ----

def history_parts(season_wars: list[tuple[float, float]], role: str, settings: ContractSettings) -> tuple[float, float]:
    """直近のシーズンの (WAR, 出場) から、成績の分の (重みつきの WAR の合計, 重みの合計)。出場は打者が打席数、投手が投球回。
    重みは新しいシーズンから順の設定値 × 出場の係数(出場 ÷ 設定値。上限 1)。"""
    full = settings.usage_full(role)
    num = 0.0
    den = 0.0
    for w, (war, usage) in zip(settings.weights, season_wars):
        f = min(1.0, max(0.0, float(usage)) / full)
        num += w * f * float(war)
        den += w * f
    return num, den


def history_war(season_wars: list[tuple[float, float]], role: str, settings: ContractSettings) -> float | None:
    """成績の分だけの見込み(出場がまったくなければ None)。"""
    num, den = history_parts(season_wars, role, settings)
    return num / den if den > 0 else None


def ability_war(role: str, overall_value: float, settings: ContractSettings) -> float:
    """見せる能力の平均(総合値)から求めた WAR の目安(D-356)。"""
    t = settings.ability_war(role)
    return float(t["intercept"]) + float(t["slope"]) * (float(overall_value) - 50.0)


def age_factor(age: int, settings: ContractSettings) -> float:
    d = settings.age_discount
    start = int(d["start"])
    if age < start:
        return 1.0
    return max(float(d["floor"]), 1.0 - float(d["per_year"]) * (age - start + 1))


def expected_war(age: int, role: str, season_wars: list[tuple[float, float]], overall_value: float | None, settings: ContractSettings) -> tuple[float, str]:
    """見込みの WAR と、その元(history:成績が中心 / ability:能力だけ / none)。成績の分と能力の分を重みで混ぜる。年齢の割引を含む(D-356)。"""
    num, den = history_parts(season_wars, role, settings)
    if overall_value is not None:
        k = settings.ability_weight
        num += k * ability_war(role, overall_value, settings)
        den += k
    if den <= 0:
        return 0.0, "none"
    source = "history" if num and den > settings.ability_weight else "ability"
    return num / den * age_factor(age, settings), source


def salary_for(expected: float, rate: float, settings: ContractSettings) -> int:
    """年俸 = 最低年俸 + 単価 × max(0, 見込みの WAR)。丸めは設定値、下限は最低年俸。"""
    raw = settings.minimum + rate * max(0.0, float(expected))
    step = settings.rounding
    return max(settings.minimum, int(round(raw / step)) * step)


def assessed_salary(expected: float, rate: float, settings: ContractSettings, old_salary: int | None = None) -> int:
    """更改の査定の年俸:算定の年俸。前の年俸があれば、その 75%(設定値)を下回らない(丸めは切り上げ。D-357)。"""
    salary = salary_for(expected, rate, settings)
    if old_salary:
        step = settings.rounding
        floor = int(math.ceil(int(old_salary) * settings.decline_floor / step)) * step
        salary = max(salary, floor)
    return salary


def contract_years(age: int, expected: float, settings: ContractSettings) -> int:
    """更改の契約年数(D-360):主力級(見込みの WAR が表の値以上で、年齢が上限以下)は複数年、ほかは既定(1 年)。新規開始の初期選手にも使う。"""
    m = settings.multi_year
    if int(age) <= int(m["max_age"]):
        for low, n in m["table"]:
            if float(expected) >= float(low):
                return min(int(n), settings.max_years)
    return settings.default_years


def rate_for(expected_wars: list[float], total_funds: float, n_players: int, settings: ContractSettings) -> float:
    """単価(1 WAR あたりの万円)。(資金の合計 ÷ 1.1 − 最低年俸 × 選手数)÷ Σ max(0, 見込み)(D-355)。
    こうすると、全球団の総年俸の合計が資金の合計の約 9 割に合う(年俸は見込みが 0 未満なら最低年俸のため)。"""
    total = sum(max(0.0, float(w)) for w in expected_wars)
    pool = float(total_funds) / settings.payroll_divisor - settings.minimum * n_players
    if total <= 0:
        return 0.0
    return max(0.0, pool / total)


# ---- 資金(D-353、D-354) ----

def assign_tiers(team_ids: list[str], seed: int, settings: ContractSettings) -> dict[str, str]:
    """球団の規模(大・中・小)を、新規開始のシードから割り当てる(別の乱数系列。D-231、D-353)。"""
    rng = random.Random(derive_seed(seed, "budget-tiers"))
    ids = sorted(team_ids)
    rng.shuffle(ids)
    counts = settings.tier_counts
    out = {}
    i = 0
    for tier in TIERS:
        for _ in range(counts[tier]):
            if i < len(ids):
                out[ids[i]] = tier
                i += 1
    for tid in ids[i:]:
        out[tid] = "medium"
    return out


def base_funds(tier: str | None, settings: ContractSettings) -> int:
    """規模の基準額(資金の初期値)。"""
    return int(round(settings.base_budget * settings.tier_factor(tier or "medium")))


def initial_funds(tiers: dict[str, str], settings: ContractSettings) -> dict[str, int]:
    return {tid: base_funds(t, settings) for tid, t in tiers.items()}


def changed_funds(funds: int, tier: str | None, rank: int | None, settings: ContractSettings) -> int:
    """前のシーズンの順位(1 から)で増減した資金。規模の基準額の範囲に収め、丸める(D-354)。順位がなければそのまま。"""
    if rank is None:
        return int(funds)
    table = settings.change_by_rank
    change = table[min(max(int(rank), 1), len(table)) - 1]
    base = base_funds(tier, settings)
    lo, hi = settings.funds_range
    step = settings.rounding
    value = min(max(float(funds) * (1.0 + change), base * lo), base * hi)
    return int(round(value / step)) * step


def team_salary(team: Team) -> int:
    """総年俸(育成選手の年俸も含む。D-375)。"""
    return sum(int(p.contract["salary"]) for p in team.players + team.development if p.contract)


# ---- 契約の付け替え ----

def set_contract(player: Player, salary: int, years: int, year: int, reason: str, offers: int | None = None) -> dict:
    """新しい契約(year シーズンから years 年。満了は year + years − 1)。履歴に残す(更改は提示回数 offers も。F3-2b)。"""
    old = player.contract or {}
    history = list(old.get("history", []))
    entry = {"year": int(year), "salary": int(salary), "years": int(years), "reason": reason}
    if offers is not None:
        entry["offers"] = int(offers)
    history.append(entry)
    player.contract = {"salary": int(salary), "until": int(year) + int(years) - 1, "history": history}
    return player.contract


def remaining_years(contract: dict | None, year: int) -> int:
    """今シーズン(year)を含めた残りの契約年数(0 なら今シーズンで満了)。"""
    if not contract:
        return 0
    return max(0, int(contract["until"]) - int(year) + 1)


def is_expiring(contract: dict | None, year: int) -> bool:
    """year シーズンの終わりで満了か(契約がなければ満了扱い)。"""
    return not contract or int(contract["until"]) <= int(year)
