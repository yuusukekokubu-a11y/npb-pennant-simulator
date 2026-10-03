"""架空リーグの生成(段階1・第1弾の実装 ①)。

潜在能力の作り方(D-028、D-030):
  型を選ぶ(打者は守備位置ごとの割合。D-157)→ 型ごとの補正を基準値に足す → 正規分布の乱数を足す
  (初期選手だけ、年齢が高いほど高めにずらす:生き残りバイアス。D-034)
現在の能力は、潜在能力から年齢カーブで出す(D-031、aging.py)。

乱数はすべて random.Random(seed) から取る。同じシードなら同じ結果になる(D-006)。
"""

from __future__ import annotations

import math
import random

from . import aging
from .abilities import (
    BATTER,
    FIELDER_POSITIONS,
    PITCHER,
    PITCHER_POSITIONS,
    STYLE_ITEMS,
    items_for,
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
        potential: dict[str, float] = {}
        for item in items_for(role):
            if item in STYLE_ITEMS:
                potential[item] = pot_cfg["style_mean"] + corrections[item] + rng.gauss(0, pot_cfg["noise_sd"])
            else:
                potential[item] = base + corrections[item] + rng.gauss(0, pot_cfg["noise_sd"]) + bias

        growth_type = _weighted_key(rng, cfg["aging"]["growth_types"])

        var = cfg["variation"]
        years = max(0.0, age - var["drift_start_age"]) if initial else 0.0
        drift_sd = var["ability_drift_sd_per_year"] * math.sqrt(years)
        drift: dict[str, float] = {}
        for item in items_for(role):
            fixed = aging.group_of(cfg, item) == aging.FIXED_GROUP
            drift[item] = 0.0 if fixed or drift_sd == 0 else rng.gauss(0, drift_sd)

        ratings = {
            item: aging.current_rating(cfg, item, potential[item], age, growth_type, drift[item])
            for item in items_for(role)
        }
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
        return Player(
            id=f"{self.id_prefix}{self.count:04d}",
            family_name=family,
            given_name=given,
            age=age,
            role=role,
            position=position,
            bats=bats,
            throws=throws,
            ratings=ratings,
            hidden=HiddenInfo(potential=potential, growth_type=growth_type, archetype=archetype, ability_drift=drift),
            state=PlayerState(form=form),
            origin=origin,
        )


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
) -> League:
    """ゲーム開始時のリーグ(全球団・全選手)を作る。"""
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


def shift_potential(config: GenerationConfig, player: Player, shift: float) -> None:
    """校正(D-197):強弱の項目(型の項目を除く)の潜在能力に定数を足し、現在の能力を年齢カーブで出し直す。"""
    for item in items_for(player.role):
        if item in STYLE_ITEMS:
            continue
        player.hidden.potential[item] += shift
        player.ratings[item] = aging.current_rating(config, item, player.hidden.potential[item], player.age, player.hidden.growth_type, player.hidden.ability_drift[item])

