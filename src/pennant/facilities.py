"""設備(D-383):練習設備・トレーナーと医療・育成施設。段階は 1〜5(3 が標準)。

- 効き(effects):練習設備は能力の伸び(年齢による増える分)、トレーナー・医療は年齢による下がり方、育成施設は育成選手の伸びを、
  標準からの段階の差 × 1 段階の割合だけ大きく(小さく)する。揺れ(毎年の偶然の増減)は変えない。
- 維持費(upkeep):段階ごとの毎年の費用。3 つの合計の分だけ、年俸に使える額(資金の上限)が減る(ContractContext.cap)。
- 新規開始は規模に応じた段階(initial)。AI 球団は資金に応じて段階を決める(ai_level:資金が基準以上の最も高い段階)。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json
from .models import League, Team

SUPPORTED_FORMAT_VERSION = 1
KINDS = ("practice", "medical", "development")


@dataclass(frozen=True)
class FacilitySettings:
    data: dict
    source: str

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self.data["kinds"])

    @property
    def levels(self) -> int:
        return int(self.data["levels"])

    @property
    def standard(self) -> int:
        return int(self.data["standard"])

    def label(self, kind: str) -> str:
        return str(self.data["labels"][kind])

    def upkeep(self, level: int) -> int:
        return int(self.data["upkeep"][int(level) - 1])

    def effect(self, kind: str, level: int) -> float:
        """段階の効きの倍率(標準で 1.0)。トレーナー・医療は下がり方に掛ける(段階が高いほど小さく)。"""
        step = float(self.data["effect_per_level"][kind]) * (int(level) - self.standard)
        return max(0.0, 1.0 - step) if kind == "medical" else max(0.0, 1.0 + step)

    def initial(self, tier: str | None) -> int:
        return int(self.data["initial"].get(tier or "medium", self.standard))

    def ai_level(self, funds: int | None) -> int:
        if funds is None:
            return self.standard
        level = 1
        for i, need in enumerate(self.data["ai_funds_for_level"], start=1):
            if int(funds) >= int(need):
                level = i
        return level


def load_facility_settings(path: str | Path | None = None) -> FacilitySettings:
    data, source = _read_json(path, "facilities.json")
    return validate_facility_settings(data, source)


def validate_facility_settings(data, source: str = "(辞書)") -> FacilitySettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    kinds = c.get(root, "kinds", "")
    if kinds is not None and (not isinstance(kinds, list) or sorted(kinds) != sorted(KINDS)):
        c.add("kinds", f"設備は {', '.join(KINDS)} の 3 つにしてください")
    levels = c.integer(c.get(root, "levels", ""), "levels", 2, 10)
    c.integer(c.get(root, "standard", ""), "standard", 1, 10)
    n = levels if isinstance(levels, int) else None
    for key in ("upkeep", "ai_funds_for_level"):
        v = c.get(root, key, "")
        if v is not None and (not isinstance(v, list) or (n is not None and len(v) != n) or not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v) or v != sorted(v)):
            c.add(key, "段階の数だけ、0 以上の整数を小さい順に並べてください")
    labels = c.section(c.get(root, "labels", ""), "labels")
    eff = c.section(c.get(root, "effect_per_level", ""), "effect_per_level")
    for k in KINDS:
        if labels is not None and not isinstance(labels.get(k), str):
            c.add(f"labels.{k}", "名前(文字)が必要です")
        c.number(c.get(eff, k, "effect_per_level"), f"effect_per_level.{k}", 0, 1)
    init = c.section(c.get(root, "initial", ""), "initial")
    for k in ("large", "medium", "small"):
        c.integer(c.get(init, k, "initial"), f"initial.{k}", 1, n or 10)
    if c.problems:
        raise ConfigError(source, c.problems)
    return FacilitySettings(copy.deepcopy(root), source)


_DEFAULT: list[FacilitySettings] = []


def default_settings() -> FacilitySettings:
    if not _DEFAULT:
        _DEFAULT.append(load_facility_settings())
    return _DEFAULT[0]


def levels_of(team: Team, settings: FacilitySettings | None = None) -> dict[str, int]:
    """球団の設備の段階(ない設備は標準)。"""
    s = settings or default_settings()
    return {k: int(team.facilities.get(k, s.standard)) for k in s.kinds}


def team_upkeep(team: Team, settings: FacilitySettings | None = None) -> int:
    """球団の設備の毎年の維持費の合計(万円)。"""
    s = settings or default_settings()
    return sum(s.upkeep(v) for v in levels_of(team, s).values())


def league_upkeep(league: League, settings: FacilitySettings | None = None) -> dict[str, int]:
    return {t.id: team_upkeep(t, settings) for t in league.teams}


def set_initial(league: League, tiers: dict, settings: FacilitySettings | None = None) -> None:
    """新規開始の段階(規模に応じて)。"""
    s = settings or default_settings()
    for t in league.teams:
        t.facilities = {k: s.initial(tiers.get(t.id)) for k in s.kinds}


def ai_decide(league: League, funds: dict, my_team_id: str | None, settings: FacilitySettings | None = None) -> None:
    """AI 球団の段階を、資金に応じて決める(全部の設備を同じ段階に)。あなたの球団は変えない。"""
    s = settings or default_settings()
    for t in league.teams:
        if t.id == my_team_id:
            continue
        level = s.ai_level(funds.get(t.id))
        t.facilities = {k: level for k in s.kinds}


def growth_factors(team: Team, settings: FacilitySettings | None = None) -> tuple[float, float, float]:
    """(支配下の伸びの倍率, 年齢による下がり方の倍率, 育成選手の伸びの倍率)。"""
    s = settings or default_settings()
    lv = levels_of(team, s)
    return s.effect("practice", lv["practice"]), s.effect("medical", lv["medical"]), s.effect("development", lv["development"])
