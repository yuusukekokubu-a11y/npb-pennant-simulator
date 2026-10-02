"""投手の疲労と回復(D-060)。

疲労は「状態」(Player.state.fatigue。D-024)として持つ。
  - 試合で投げると、対戦した打者の数 × fatigue_per_batter だけ増える
  - 1日進めるごとに、recovery_per_day × (回復力の倍率) だけ減る(0 より下にはならない)
  - 疲労が上限を超えている投手は、先発・救援として登板できない
球数は使わない。
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .abilities import PITCHER
from .game_config import GameConfig
from .models import Player
from .plate_appearance import ratio_multiplier


def recovery_per_day(pitcher: Player, config: GameConfig) -> float:
    p = config["pitching"]
    return p["recovery_per_day"] * ratio_multiplier(pitcher.ratings["recovery"], p["recovery_ratio_per_10"])


def advance_day(players: Iterable[Player], config: GameConfig) -> None:
    """1日進める:投手の疲労を回復力に応じて減らす。"""
    for p in players:
        if p.role == PITCHER and p.state.fatigue > 0:
            p.state.fatigue = max(0.0, p.state.fatigue - recovery_per_day(p, config))


def add_fatigue(pitcher: Player, batters_faced: int, config: GameConfig) -> None:
    pitcher.state.fatigue += batters_faced * config["pitching"]["fatigue_per_batter"]


def apply_game_fatigue(batters_faced: Mapping[str, int], players: Mapping[str, Player], config: GameConfig) -> None:
    """試合で投げた投手(ID → 対戦打者数)に疲労を足す。"""
    for pid, bf in batters_faced.items():
        add_fatigue(players[pid], bf, config)


def can_start(pitcher: Player, config: GameConfig) -> bool:
    return pitcher.state.fatigue <= config["pitching"]["max_fatigue_to_start"]


def can_relieve(pitcher: Player, config: GameConfig) -> bool:
    return pitcher.state.fatigue <= config["pitching"]["max_fatigue_to_relieve"]


def starter_batters_limit(pitcher: Player, config: GameConfig) -> float:
    """先発が対戦できる打者数の上限(スタミナが高いほど多く、疲労が残っているほど少ない)。"""
    p = config["pitching"]
    limit = (
        p["starter_bf_base"]
        + p["starter_bf_per_10_stamina"] * (pitcher.ratings["stamina"] - 50) / 10
        - p["starter_bf_fatigue_penalty"] * pitcher.state.fatigue
    )
    return max(p["starter_bf_min"], limit)
