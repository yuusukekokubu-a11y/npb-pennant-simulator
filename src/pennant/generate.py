"""架空リーグの生成(段階1・第1弾の実装 ①、作り直し①)。

見せる能力の作り方(D-028、D-030、D-350):
  型を選ぶ(打者は守備位置ごとの割合。D-157)→ 型ごとの補正を基準値に足す → 正規分布の乱数を足して「ピーク時の水準」を引く
  (初期選手だけ、年齢が高いほど高めにずらす:生き残りバイアス。D-034)
  → 年齢カーブの差を引き、これまでの揺れを足して今の能力にする。ピーク時の水準は保存しない(潜在能力は持たない。D-350)。
試合の計算に使う内部の能力は、見せる能力から換算する(ability_model.py。D-349)。

乱数はすべて random.Random(seed) から取る。同じシードなら同じ結果になる(D-006)。
"""

from __future__ import annotations

import math
import random

from . import ability_model, aging
from .skills import initial_skills
from .abilities import (
    BATTER,
    FIELDER_POSITIONS,
    PITCHER,
    PITCHER_POSITIONS,
    abilities_for,
)
from .config import ConfigError, GenerationConfig, NameParts, archetype_shares, load_generation_config, load_name_parts
from .models import HiddenInfo, League, Player, PlayerState, Team
from .names import NameGenerator, team_identities
from .parks import assign_parks

MAX_RESAMPLE = 1000


def _weighted_key(rng: random.Random, table: dict[str, dict]) -> str:
    keys = list(table)
    return rng.choices(keys, weights=[table[k]["share"] for k in keys])[0]


def _truncated_normal_int(rng: random.Random, mean: float, sd: float, low: int, high: int) -> int:
    """正規分布から取り、整数に丸めて low〜high に入るまで取り直す。"""
    if sd == 0:
        return min(max(round(mean), low), high)
    for _ in range(MAX_RESAMPLE):
        value = round(rng.gauss(mean, sd))
        if low <= value <= high:
            return value
    return min(max(round(mean), low), high)


class _PlayerFactory:
    def __init__(self, config: GenerationConfig, names: NameGenerator, rng: random.Random, id_prefix: str):
        self.config = config
        self.names = names
        self.rng = rng
        self.id_prefix = id_prefix
        self.count = 0

    def _archetype(self, role: str, position: str) -> tuple[str, dict[str, float]]:
        cfg = self.config
        if role == BATTER:
            # ポジションごとに型の割合を変える(第3弾①。D-157)。型の補正値は変えない
            shares = archetype_shares(cfg, position)
            key = _weighted_key(self.rng, {k: {"share": v} for k, v in shares.items()})
            return key, dict(cfg["batter_archetypes"][key]["corrections"])
        quality = _weighted_key(self.rng, cfg["pitcher_qualities"])
        role_corr = cfg["pitcher_roles"][position]["corrections"]
        q_corr = cfg["pitcher_qualities"][quality]["corrections"]
        return f"{position}/{quality}", {i: role_corr[i] + q_corr[i] for i in role_corr}

    def make(self, role: str, position: str, age: int, initial: bool, origin: str | None = None) -> Player:
        cfg = self.config
        rng = self.rng
        pot_cfg = cfg["potential"]
        base = pot_cfg["pitcher_base_mean"] if role == PITCHER else pot_cfg["batter_base_mean"]
        bias = 0.0
        if initial:
            sb = cfg["survival_bias"]
            bias = sb["per_year"] * max(0.0, age - sb["start_age"])

        archetype, corrections = self._archetype(role, position)
        peak: dict[str, float] = {}
        for item in abilities_for(role):
            peak[item] = base + corrections[item] + rng.gauss(0, pot_cfg["noise_sd"]) + bias

        growth_type = _weighted_key(rng, cfg["aging"]["growth_types"])
        ease = ability_model.draw_ease(cfg, rng)
        trajectory_bias = rng.gauss(0, cfg["abilities"]["trajectory"]["bias_sd"]) if role == BATTER else 0.0
        pitches = ability_model.draw_pitches(cfg, rng) if role == PITCHER else None

        var = cfg["variation"]
        years = max(0.0, age - var["drift_start_age"]) if initial else 0.0
        drift_sd = var["ability_drift_sd_per_year"] * math.sqrt(years)
        abilities: dict[str, float] = {}
        for item in abilities_for(role):
            fixed = aging.group_of(cfg, item) == aging.FIXED_GROUP
            drift = 0.0 if fixed or drift_sd == 0 else rng.gauss(0, drift_sd)
            # 今の能力 = ピーク時の水準 − 年齢による差 + これまでの揺れ。これからの伸びは毎年の増減で決まる(伸びやすさ次第。D-350)
            abilities[item] = peak[item] - aging.age_gap(cfg, item, age, growth_type) + drift

        form = rng.gauss(0, var["form_sd"]) if var["form_sd"] > 0 else 0.0
        # 投手は投げ手だけ、打者は打席の左右だけを持つ(D-046、D-048)
        hand = cfg["handedness"]
        throws = bats = None
        if role == PITCHER:
            throws = "L" if rng.random() < hand["pitcher_throws_left"] else "R"
        else:
            bats = _weighted_key(rng, {k: {"share": v} for k, v in hand["bats"].items()})
        family, given = self.names.person()

        self.count += 1
        player = Player(
            id=f"{self.id_prefix}{self.count:04d}",
            family_name=family,
            given_name=given,
            age=age,
            role=role,
            position=position,
            bats=bats,
            throws=throws,
            ratings={},
            hidden=HiddenInfo(growth_type=growth_type, archetype=archetype, ease=round(ease, 4), trajectory_bias=round(trajectory_bias, 4)),
            state=PlayerState(form=form),
            origin=origin,
            abilities=abilities,
            pitches=pitches,
        )
        player.skills = initial_skills(player)  # 特殊能力(別の乱数の系列。D-374)
        ability_model.refresh(cfg, player)
        return player


def _roster_slots(config: GenerationConfig) -> list[tuple[str, str]]:
    """1球団分の (役割, ポジション) の並び。"""
    slots: list[tuple[str, str]] = []
    for pos in PITCHER_POSITIONS:
        slots += [(PITCHER, pos)] * config["roster"]["pitchers"][pos]
    for pos in FIELDER_POSITIONS:
        slots += [(BATTER, pos)] * config["roster"]["fielders"][pos]
    return slots


def generate_league(
    seed: int,
    config: GenerationConfig | None = None,
    names: NameParts | None = None,
    foreign: dict | None = None,
    with_foreign: bool = True,
) -> League:
    """ゲーム開始時のリーグ(全球団・全選手)を作る。foreign は外国人選手の設定(draft.json の foreign。省略すると既定。D-376)。
    with_foreign=False(仕組みの選択肢で外国人・育成がなし。D-381)なら外国人選手を入れない。"""
    config = config or load_generation_config()
    names = names or load_name_parts()
    rng = random.Random(seed)

    n_leagues = config["league"]["num_leagues"]
    per_league = config["league"]["teams_per_league"]
    if len(names.league_names) < n_leagues:
        raise ConfigError(names.source, [f"league_names が {len(names.league_names)} 件しかありません(リーグ数 {n_leagues} 以上が必要)"])

    identities = team_identities(names, rng, n_leagues * per_league)
    name_gen = NameGenerator(names, rng)
    factory = _PlayerFactory(config, name_gen, rng, id_prefix="P")
    ages = config["initial_ages"]

    teams: list[Team] = []
    for index, (place, nickname, stadium) in enumerate(identities):
        team = Team(id=f"T{index + 1:02d}", league_index=index // per_league, place=place, nickname=nickname, stadium=stadium)
        for role, position in _roster_slots(config):
            age = _truncated_normal_int(rng, ages["mean"], ages["sd"], ages["min"], ages["max"])
            player = factory.make(role, position, age, initial=True)
            player.team_id = team.id
            team.players.append(player)
        teams.append(team)
    league = League(seed=seed, league_names=list(names.league_names[:n_leagues]), teams=teams)
    if names.foreign_surnames and with_foreign:
        add_initial_foreign(league, seed, config, names, foreign)
    assign_parks(league, config)  # 球場の倍率(別の乱数。既存の生成の乱数は変えない。D-137)
    return league


def generate_draft_class(
    seed: int,
    count: int,
    config: GenerationConfig | None = None,
    names: NameParts | None = None,
    existing: League | None = None,
) -> list[Player]:
    """ドラフト候補(新人)を count 人作る。

    基準分布は全員共通で、生き残りバイアスは入れない(D-030 補足、D-034)。
    existing を渡すと、そのリーグの選手と名前が重ならないようにする。
    """
    config = config or load_generation_config()
    names = names or load_name_parts()
    rng = random.Random(seed)
    used = {(p.family_name, p.given_name) for p in existing.all_players()} if existing else set()
    factory = _PlayerFactory(config, NameGenerator(names, rng, used), rng, id_prefix="R")

    slots = _roster_slots(config)
    origins = config["draft"]["origins"]
    players = []
    for _ in range(count):
        role, position = rng.choice(slots)  # 球団の構成と同じ比率で役割・ポジションを選ぶ
        origin = _weighted_key(rng, origins)
        o = origins[origin]
        age = _truncated_normal_int(rng, o["age_mean"], o["age_sd"], o["age_min"], o["age_max"])
        players.append(factory.make(role, position, age, initial=False, origin=origin))
    return players


def make_rookie(config: GenerationConfig, names: NameGenerator, rng: random.Random, role: str, position: str, player_id: str, potential_shift: float = 0.0) -> Player:
    """新人を1人作る(年度の確定の補充用。D-184)。出身と年齢は draft の設定から。ID は呼び出し側が決める。
    potential_shift は校正の定数(D-197):強弱の項目の潜在能力に足し、現在の能力を出し直す(生成そのものは変えない)。"""
    factory = _PlayerFactory(config, names, rng, id_prefix="")
    origins = config["draft"]["origins"]
    origin = _weighted_key(rng, origins)
    o = origins[origin]
    age = _truncated_normal_int(rng, o["age_mean"], o["age_sd"], o["age_min"], o["age_max"])
    player = factory.make(role, position, age, initial=False, origin=origin)
    player.id = player_id
    if potential_shift:
        shift_potential(config, player, potential_shift)
    return player


def add_initial_foreign(league: League, seed: int, config: GenerationConfig, names: NameParts, foreign: dict | None = None) -> None:
    """新規開始の外国人選手(D-376):各球団 initial_min〜initial_max 人を、同じ役割・ポジションの選手と入れ替えて作る。
    別の乱数の系列(ほかの選手の生成は変えない)。"""
    from .config import _read_json
    from .names import ForeignNameGenerator
    from .season import derive_seed

    s = foreign if foreign is not None else _read_json(None, "draft.json")[0]["foreign"]
    rng = random.Random(derive_seed(seed, "foreign:initial"))
    gen = ForeignNameGenerator(names, rng, {(p.family_name, p.given_name) for p in league.all_players()})
    for team in league.teams:  # 外国人の姓はリーグ全体で重ねない(同じ球団の中で重ならないように。D-376)
        k = rng.randint(int(s["initial_min"]), int(s["initial_max"]))
        for i in sorted(rng.sample(range(len(team.players)), k)):
            old = team.players[i]
            p = make_foreign(config, gen, rng, old.role, old.position, old.id, s)
            gen.taken.add(p.family_name)
            p.team_id = team.id
            team.players[i] = p


def make_foreign(config: GenerationConfig, names, rng: random.Random, role: str, position: str, player_id: str, settings: dict, potential_shift: float = 0.0) -> Player:
    """外国人選手を1人作る(D-376)。年齢は settings の age_min〜age_max、初期選手と同じ作り方(年齢に合わせた今の能力)に、
    水準の上げ幅(boost)と、1 人ごとのふれ(spread_sd の正規分布。当たり外れ)と校正の定数を足す。names は ForeignNameGenerator。"""
    factory = _PlayerFactory(config, names, rng, id_prefix="")
    age = rng.randint(int(settings["age_min"]), int(settings["age_max"]))
    player = factory.make(role, position, age, initial=True, origin="foreign")
    player.id = player_id
    player.foreign = True
    shift_potential(config, player, potential_shift + float(settings["boost"]) + rng.gauss(0, float(settings["spread_sd"])))
    return player


def shift_potential(config: GenerationConfig, player: Player, shift: float) -> None:
    """校正(D-197):見せる能力に定数を足し、内部の能力を作り直す(潜在能力はないので、今の能力をそのまま動かす。D-350)。"""
    for item in abilities_for(player.role):
        player.abilities[item] += shift
    ability_model.refresh(config, player)
