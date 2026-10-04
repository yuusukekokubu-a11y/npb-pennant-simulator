"""年度の確定の処理:加齢・能力の更新・引退・簡易な補充(F2。D-184〜D-187)。

シーズンの間に、年に1回だけ行う。試合の計算とは別の処理の流れで、乱数は年度ごとのシードから作る(D-186)。
  1. 加齢:年齢を 1 上げる
  2. 能力の更新:能力の揺れ(generation.json の variation)を足し、年齢カーブ(aging.py)で現在の能力を出し直す。
     好調・不調は引き直す。潜在能力・成長タイプ・型は変えない。疲労は 0 に戻す
  3. 引退:年齢と能力から確率で決める(data/offseason.json。D-187)
  4. 補充:引退した人数だけ、同じ球団・同じポジションに新人を入れる(既存の新人の生成。D-184)。
     F3-1 のドラフトは、この関数(replenish)を差し替える
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import aging
from .abilities import items_for
from .config import ConfigError, GenerationConfig, NameParts, _Checker, _read_json
from .generate import make_rookie, shift_potential
from .models import League, Player, Team
from .names import NameGenerator
from .season import derive_seed
from .stats import overall

SUPPORTED_FORMAT_VERSION = 1


# ---- 設定(data/offseason.json) ----

@dataclass(frozen=True)
class OffseasonSettings:
    data: dict
    source: str

    @property
    def log_seasons(self) -> int:
        return int(self.data["log_seasons"])

    @property
    def prerun_years(self) -> int:
        return int(self.data["prerun_years"])

    def calibration(self, level: str) -> dict[str, float]:
        """校正の定数(役割 → 潜在能力に足す値)。ずれの段階(small / medium / large)ごと(D-209)。"""
        return {role: float(v) for role, v in self.data["calibration"][level].items()}

    @property
    def levels(self) -> tuple[str, ...]:
        return tuple(self.data["calibration"])

    def retirement(self, key: str) -> float:
        return float(self.data["retirement"][key])


def load_offseason_settings(path: str | Path | None = None) -> OffseasonSettings:
    data, source = _read_json(path, "offseason.json")
    return validate_offseason_settings(data, source)


def validate_offseason_settings(data, source: str = "(辞書)") -> OffseasonSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    c.integer(c.get(root, "log_seasons", ""), "log_seasons", 1, 100)
    c.integer(c.get(root, "prerun_years", ""), "prerun_years", 0, 200)
    cal = c.section(c.get(root, "calibration", ""), "calibration")
    if cal is not None:
        for level in ("small", "medium", "large"):
            sec = c.section(c.get(cal, level, "calibration"), f"calibration.{level}")
            for role in ("batter", "pitcher"):
                c.number(c.get(sec, role, f"calibration.{level}"), f"calibration.{level}.{role}", -30, 30)
    r = c.section(c.get(root, "retirement", ""), "retirement")
    for key, low, high in (
        ("start_age", 18, 60), ("per_year_over_start", 0, 1), ("overall_floor", 0, 100), ("per_point_below_floor", 0, 1),
        ("young_floor_age", 18, 60), ("max_age", 18, 70), ("cap", 0, 1),
    ):
        c.number(c.get(r, key, "retirement"), f"retirement.{key}", low, high)
    if c.problems:
        raise ConfigError(source, c.problems)
    return OffseasonSettings(copy.deepcopy(root), source)


# ---- 結果 ----

@dataclass
class PlayerNote:
    """引退・入団した選手の、画面に出してよい情報。"""

    player_id: str
    name: str
    team_id: str
    role: str
    position: str
    age: int
    origin: str | None = None


@dataclass
class OffseasonResult:
    year: int  # 終わったシーズンの番号(1 から)
    seed: int
    retired: list[PlayerNote] = field(default_factory=list)
    rookies: list[PlayerNote] = field(default_factory=list)
    ability_changes: dict[str, dict[str, float]] = field(default_factory=dict)  # 選手 ID → 項目 → 増減(隠し情報。答え合わせ用)
    ages: dict[str, int] = field(default_factory=dict)  # 選手 ID → 更新後の年齢(残った選手)

    def to_dict(self) -> dict:
        return {
            "year": self.year,
            "seed": self.seed,
            "retired": [vars(n) for n in self.retired],
            "rookies": [vars(n) for n in self.rookies],
            "ability_changes": {pid: {k: round(v, 6) for k, v in d.items()} for pid, d in self.ability_changes.items()},
            "ages": dict(self.ages),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OffseasonResult":
        return cls(
            int(d["year"]), int(d["seed"]),
            [PlayerNote(**n) for n in d.get("retired", [])], [PlayerNote(**n) for n in d.get("rookies", [])],
            {pid: {k: float(v) for k, v in x.items()} for pid, x in d.get("ability_changes", {}).items()},
            {pid: int(v) for pid, v in d.get("ages", {}).items()},
        )


# ---- 処理 ----

def retirement_probability(settings: OffseasonSettings, age: int, overall_value: float) -> float:
    """引退の確率(D-187):年齢の分 + 能力の分。上限年齢で 1.0。"""
    r = settings.retirement
    if age >= r("max_age"):
        return 1.0
    p = 0.0
    if age > r("start_age"):
        p += (age - r("start_age")) * r("per_year_over_start")
    if age >= r("young_floor_age") and overall_value < r("overall_floor"):
        p += (r("overall_floor") - overall_value) * r("per_point_below_floor")
    return min(max(p, 0.0), r("cap"))


def age_and_update(player: Player, config: GenerationConfig, rng: random.Random) -> dict[str, float]:
    """1人分の加齢と能力の更新(D-186)。戻り値は項目ごとの増減。"""
    var = config["variation"]
    before = dict(player.ratings)
    player.age += 1
    for item in items_for(player.role):
        if aging.group_of(config, item) == aging.FIXED_GROUP:
            continue
        if var["ability_drift_sd_per_year"] > 0:
            player.hidden.ability_drift[item] += rng.gauss(0, var["ability_drift_sd_per_year"])
        player.ratings[item] = aging.current_rating(config, item, player.hidden.potential[item], player.age, player.hidden.growth_type, player.hidden.ability_drift[item])
    player.state.form = rng.gauss(0, var["form_sd"]) if var["form_sd"] > 0 else 0.0
    player.state.fatigue = 0.0
    return {item: player.ratings[item] - before[item] for item in items_for(player.role)}


def replenish(team: Team, slots: list[tuple[str, str]], config: GenerationConfig, names: NameGenerator, rng: random.Random, year: int, counter: list[int], calibration: dict[str, float] | None = None, id_prefix: str = "Y") -> list[Player]:
    """空いた枠(役割, ポジション)の分だけ新人を作って球団に入れる(簡易な補充。D-184)。F3-1 ではドラフトに差し替える。
    calibration は校正の定数(役割 → 潜在能力に足す値。D-197)。id_prefix は新人の ID の頭(事前運転は B)。"""
    rookies = []
    for role, position in slots:
        counter[0] += 1
        p = make_rookie(config, names, rng, role, position, f"{id_prefix}{year + 1:02d}R{counter[0]:03d}", (calibration or {}).get(role, 0.0))
        p.team_id = team.id
        team.players.append(p)
        rookies.append(p)
    return rookies


def age_update_retire(league: League, seed: int, config: GenerationConfig, settings: OffseasonSettings, year: int, calibration: dict[str, float] | None = None) -> tuple[OffseasonResult, dict[str, list[tuple[str, str]]]]:
    """年度の確定の前半(加齢 → 能力の更新 → 引退)。league を書き換える。戻り値は結果と、球団ごとに空いた枠(役割, ポジション)。
    引退の判定は校正前の目盛りで行う(校正の定数を引く。事前運転と同じ基準にするため。D-197)。"""
    rng = random.Random(seed)
    result = OffseasonResult(year, seed)
    vacated_by_team: dict[str, list[tuple[str, str]]] = {}
    for team in league.teams:  # 球団の順・選手の順に決める(再現性のため)
        kept: list[Player] = []
        vacated: list[tuple[str, str]] = []
        for p in list(team.players):
            changes = age_and_update(p, config, rng)
            retire = rng.random() < retirement_probability(settings, p.age, overall(p) - (calibration or {}).get(p.role, 0.0))
            if retire:
                result.retired.append(PlayerNote(p.id, p.name, team.id, p.role, p.position, p.age, p.origin))
                vacated.append((p.role, p.position))
            else:
                kept.append(p)
                result.ability_changes[p.id] = changes
                result.ages[p.id] = p.age
        team.players = kept
        vacated_by_team[team.id] = vacated
    return result, vacated_by_team


def run_offseason(league: League, seed: int, config: GenerationConfig, parts: NameParts, settings: OffseasonSettings, year: int, calibration: dict[str, float] | None = None, id_prefix: str = "Y") -> OffseasonResult:
    """F2 の簡易な年度の確定(加齢 → 能力の更新 → 引退 → 同じ枠に補充)。F3-1 の手続き(draft.py)の後も、確認用に残す。"""
    result, vacated_by_team = age_update_retire(league, seed, config, settings, year, calibration)
    rng = random.Random(derive_seed(seed, "replenish"))
    names = NameGenerator(parts, rng, {(p.family_name, p.given_name) for p in league.all_players()})
    counter = [0]
    for team in league.teams:
        for p in replenish(team, vacated_by_team[team.id], config, names, rng, year, counter, calibration, id_prefix):
            result.rookies.append(PlayerNote(p.id, p.name, team.id, p.role, p.position, p.age, p.origin))
    return result


# ---- 事前運転と校正(D-034 の完成形。D-190、D-197) ----

PRERUN_NEGOTIATION = "none"  # 事前運転で志望の判定を扱わない(全ルール。新規開始の時間が 4 割ほど増えるため。D-254)


def prerun(league: League, seed: int, config: GenerationConfig, parts: NameParts, settings: OffseasonSettings, years: int | None = None, progress=None, draft_settings=None, scout_sd: dict | None = None, money_rule: str = "none", tiers: dict | None = None, contract_settings=None, negotiation_settings=None, negotiate: str = PRERUN_NEGOTIATION) -> int:
    """新規開始のとき、試合をせずに年度の確定の処理だけを years 回回す(事前運転)。履歴は作らない。
    F3-1 からは、オフの手続き(自由契約・ドラフト・市場・自動補充。全球団 AI)を使う(D-210)。
    乱数は derive_seed(seed, "prerun:<年>")。戻り値は回した年数。progress には (終わった年数, 全年数) を知らせる。"""
    from .contracts import is_hard, load_contract_settings
    from .draft import ContractContext, load_draft_settings, run_ai_offseason
    from .negotiation import depth_slots, load_negotiation_settings

    draft_settings = draft_settings or load_draft_settings()
    if scout_sd is None:
        scout_sd = draft_settings.level_sd(draft_settings.default_level)
    contract_settings = contract_settings or load_contract_settings()
    n = settings.prerun_years if years is None else int(years)
    order = [t.id for t in league.teams]
    sd_map = {t.id: scout_sd for t in league.teams}
    for k in range(1, n + 1):
        year_seed = derive_seed(seed, f"prerun:{k}")
        age_update_retire(league, year_seed, config, settings, k, None)
        # 標準以上だけ、事前運転でも契約を扱う(予算が AI の判断に効くため)。なし・ゆるいでは契約は判断に効かないので省き、開始時に作る(時間のため。D-237)
        # 志望の判定(更改で断る選手)を事前運転で扱うかは negotiate("none" / "hard":契約を扱うときだけ / "all":全ルール)。既定は D-254
        use_contracts = is_hard(money_rule) or negotiate == "all"
        neg = (negotiation_settings or load_negotiation_settings()) if (negotiate == "all" or (negotiate == "hard" and is_hard(money_rule))) else None
        ctx = ContractContext(contract_settings, money_rule, tiers or {}, lambda pid, role: [], sd_map.__getitem__, negotiation=neg, league_seed=league.seed, slots=depth_slots(config)) if use_contracts else None
        run_ai_offseason(league, year_seed, config, parts, settings, draft_settings, k, None, sd_map, order, id_prefix="B", ctx=ctx)
        if progress is not None:
            progress(k, n)
    return n


def apply_calibration(league: League, calibration: dict[str, float], config: GenerationConfig) -> None:
    """校正(D-197):全選手の強弱の項目の潜在能力に役割ごとの定数を足し、現在の能力を出し直す。"""
    for p in league.all_players():
        shift = float(calibration.get(p.role, 0.0))
        if shift:
            shift_potential(config, p, shift)
