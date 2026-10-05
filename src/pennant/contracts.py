"""契約と年俸(F3-2a。D-230〜D-237)。

- 年俸(架空の「万円」)は見える情報だけで決める:見込みの WAR = 直近 3 シーズンの WAR の加重平均(新しいほど重い。出場の少ない年は重みを小さく)。
  履歴がない選手は、所属球団のスカウト評価(総合の推定値と天井)から設定の表で見込みの WAR を求める。30 歳以上は年齢で割り引く。
  年俸 = 最低年俸 + 単価 × max(0, 見込み) を丸めたもの(下限は最低年俸)。真の能力・潜在能力・成長タイプ・型は使わない(D-232)。
- 単価(1 WAR あたり)は毎年のオフに、(基準予算 × 球団数 − 最低年俸 × 選手数)÷ リーグの見込みの WAR の合計 で求める(D-234)。
- お金のルール:none(なし)/ loose(ゆるい:目安。警告のみ)/ standard(標準:上限)/ strict(きびしい:上限 + 球団ごとの格差)(D-230)。
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

MONEY_RULES = ("none", "loose", "standard", "strict")
RULE_LABELS = {"none": "なし", "loose": "ゆるい", "standard": "標準", "strict": "きびしい"}
RULE_NOTES = {
    "none": "予算の機能なし。更改と FA では、年俸を算定の 1.0〜1.3 倍で提示できます(年数も選べます。D-273)。",
    "loose": "予算は目安。超えると警告が出るだけで、契約は結べます。全球団同額。",
    "standard": "予算は上限。超える契約は結べません(あなたも AI も)。全球団同額。",
    "strict": "標準に加えて、球団ごとの予算の格差(大・中・小)があります。",
}
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
    def base_budget(self) -> int:
        return int(self.data["budget"]["base"])

    @property
    def cap_factor(self) -> float:
        return float(self.data["budget"]["cap_factor"])

    def tier_factor(self, tier: str) -> float:
        return float(self.data["budget"]["tiers"][tier])

    @property
    def tier_counts(self) -> dict[str, int]:
        return {t: int(n) for t, n in self.data["budget"]["tier_counts"].items()}

    @property
    def default_rule(self) -> str:
        return str(self.data["rules"]["default"])

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

    def scouting_war(self, role: str) -> dict:
        return self.data["scouting_war"][role]


def load_contract_settings(path: str | Path | None = None) -> ContractSettings:
    data, source = _read_json(path, "contracts.json")
    return validate_contract_settings(data, source)


def validate_contract_settings(root, source: str = "contracts.json") -> ContractSettings:
    c = _Checker()
    if not isinstance(root, dict):
        raise ConfigError(source, ["全体がまとまり({ })ではありません"])
    rules = c.section(c.get(root, "rules", ""), "rules")
    if rules is not None:
        if c.get(rules, "default", "rules") not in MONEY_RULES:
            c.add("rules.default", f"none / loose / standard / strict のどれかにしてください(値: {rules.get('default')!r})")
    b = c.section(c.get(root, "budget", ""), "budget")
    c.integer(c.get(b, "base", "budget"), "budget.base", 1, 100000000)
    c.number(c.get(b, "cap_factor", "budget"), "budget.cap_factor", 1, 3)
    tiers = c.section(c.get(b, "tiers", "budget"), "budget.tiers")
    counts = c.section(c.get(b, "tier_counts", "budget"), "budget.tier_counts")
    for t in TIERS:
        c.number(c.get(tiers, t, "budget.tiers"), f"budget.tiers.{t}", 0.1, 3)
        c.integer(c.get(counts, t, "budget.tier_counts"), f"budget.tier_counts.{t}", 0, 12)
    if counts is not None and all(isinstance(counts.get(t), int) for t in TIERS) and sum(counts[t] for t in TIERS) != 12:
        c.add("budget.tier_counts", f"合計が 12 球団になるようにしてください(値: {sum(counts[t] for t in TIERS)})")
    s = c.section(c.get(root, "salary", ""), "salary")
    c.integer(c.get(s, "minimum", "salary"), "salary.minimum", 1, 1000000)
    c.integer(c.get(s, "rounding", "salary"), "salary.rounding", 1, 100000)
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
    rs = c.section(c.get(root, "rookie_salaries", ""), "rookie_salaries")
    if rs is not None:
        for k, v in rs.items():
            c.integer(v, f"rookie_salaries.{k}", 1, 1000000)
    sw = c.section(c.get(root, "scouting_war", ""), "scouting_war")
    for role in ("batter", "pitcher"):
        r = c.section(c.get(sw, role, "scouting_war"), f"scouting_war.{role}")
        c.number(c.get(r, "intercept", f"scouting_war.{role}"), f"scouting_war.{role}.intercept", -10, 10)
        c.number(c.get(r, "slope", f"scouting_war.{role}"), f"scouting_war.{role}.slope", 0, 5)
        cb = c.section(c.get(r, "ceiling_bonus", f"scouting_war.{role}"), f"scouting_war.{role}.ceiling_bonus")
        for g in ("S", "A", "B", "C", "D"):
            c.number(c.get(cb, g, f"scouting_war.{role}.ceiling_bonus"), f"scouting_war.{role}.ceiling_bonus.{g}", -5, 5)
    if c.problems:
        raise ConfigError(source, c.problems)
    return ContractSettings(copy.deepcopy(root), source)


# ---- 見込みの WAR・年俸・契約年数 ----

def history_war(season_wars: list[tuple[float, float]], role: str, settings: ContractSettings) -> float | None:
    """直近のシーズンの (WAR, 出場) から見込みの WAR(履歴の分)。出場は打者が打席数、投手が投球回。
    重みは新しいシーズンから順の設定値 × 出場の係数(出場 ÷ 設定値。上限 1)。出場がまったくなければ None。"""
    full = settings.usage_full(role)
    num = 0.0
    den = 0.0
    for w, (war, usage) in zip(settings.weights, season_wars):
        f = min(1.0, max(0.0, float(usage)) / full)
        num += w * f * float(war)
        den += w * f
    return num / den if den > 0 else None


def scouting_war(role: str, overall_estimate: float, ceiling: str, settings: ContractSettings) -> float:
    """所属球団のスカウト評価(総合の推定値と天井)から見込みの WAR(履歴がない選手。D-232)。"""
    t = settings.scouting_war(role)
    return float(t["intercept"]) + float(t["slope"]) * (float(overall_estimate) - 50.0) + float(t["ceiling_bonus"].get(ceiling, 0.0))


def age_factor(age: int, settings: ContractSettings) -> float:
    d = settings.age_discount
    start = int(d["start"])
    if age < start:
        return 1.0
    return max(float(d["floor"]), 1.0 - float(d["per_year"]) * (age - start + 1))


def expected_war(age: int, role: str, season_wars: list[tuple[float, float]], scouting: tuple[float, str] | None, settings: ContractSettings) -> tuple[float, str]:
    """見込みの WAR と、その元(history / scouting)。年齢の割引を含む。"""
    hist = history_war(season_wars, role, settings)
    if hist is not None:
        base, source = hist, "history"
    elif scouting is not None:
        base, source = scouting_war(role, scouting[0], scouting[1], settings), "scouting"
    else:
        base, source = 0.0, "none"
    return base * age_factor(age, settings), source


def salary_for(expected: float, rate: float, settings: ContractSettings) -> int:
    """年俸 = 最低年俸 + 単価 × max(0, 見込みの WAR)。丸めは設定値、下限は最低年俸。"""
    raw = settings.minimum + rate * max(0.0, float(expected))
    step = settings.rounding
    return max(settings.minimum, int(round(raw / step)) * step)


def contract_years(age: int, expected: float, settings: ContractSettings) -> int:
    """既定の契約年数(F3-2b から 1 年。D-243)。複数年は、更改であなたが選んだ選手と、AI の若手だけ(negotiation.ai_years)。
    引数の年齢と見込みは、F3-2a の形と合わせるために残している(使わない)。"""
    return settings.default_years


def rate_for(expected_wars: list[float], n_teams: int, n_players: int, settings: ContractSettings) -> float:
    """単価(1 WAR あたりの万円)。(基準予算 × 球団数 − 最低年俸 × 選手数)÷ Σ max(0, 見込み)。
    こうすると、全球団の総年俸の合計が基準予算 × 球団数にちょうど合う(年俸は見込みが 0 未満なら最低年俸のため)。"""
    total = sum(max(0.0, float(w)) for w in expected_wars)
    pool = settings.base_budget * n_teams - settings.minimum * n_players
    if total <= 0:
        return 0.0
    return max(0.0, pool / total)


# ---- 予算 ----

def assign_tiers(team_ids: list[str], seed: int, settings: ContractSettings) -> dict[str, str]:
    """きびしいの格差(大・中・小)を、新規開始のシードから割り当てる(別の乱数系列。D-231)。"""
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


def budget_of(rule: str, tier: str | None, settings: ContractSettings) -> int | None:
    """球団の予算(なしのときは None)。きびしいは格差を掛ける。"""
    if rule == "none":
        return None
    factor = settings.tier_factor(tier or "medium") if rule == "strict" else 1.0
    return int(round(settings.base_budget * factor))


def cap_of(rule: str, tier: str | None, settings: ContractSettings) -> int | None:
    """予算の上限(標準・きびしい)か目安(ゆるい)。なしは None。"""
    b = budget_of(rule, tier, settings)
    return None if b is None else int(round(b * settings.cap_factor))


def is_hard(rule: str) -> bool:
    return rule in ("standard", "strict")


def team_salary(team: Team) -> int:
    return sum(int(p.contract["salary"]) for p in team.players if p.contract)


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
