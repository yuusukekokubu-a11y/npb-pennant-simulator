"""調子(D-424):シーズン中に変わる 5 段階(絶不調 −2 〜 絶好調 +2)。

- シーズンの初め(回 0)に割合で決まり、period_days 日ごと(回 1, 2, …)に確率で 1 段階上下する。普通に戻りやすい
  (上がる確率 = up − pull × 段階、下がる確率 = down + pull × 段階)。
- 乱数は derive_seed(シーズンのシード, "cond:<選手 ID>:<回>")。選手ごとに独立なので、トレードや入れ替えに左右されず、
  保存して読み込んでも、試合を再現しても同じ段階になる。
- 試合には、段階 × step を全能力に足す(選手の状態の form。plate_appearance の rating が使う)。
- 設定は data/game.json の condition(なければ既定値)。
"""

from __future__ import annotations

import random


LEVELS = (-2, -1, 0, 1, 2)
LABELS = {2: "絶好調", 1: "好調", 0: "普通", -1: "不調", -2: "絶不調"}
MARKS = {2: "↑", 1: "↗", 0: "→", -1: "↘", -2: "↓"}
DEFAULTS = {"period_days": 5, "step": 3.0, "initial": [0.1, 0.2, 0.4, 0.2, 0.1], "up": 0.25, "down": 0.25, "pull": 0.08}


def settings(game_config) -> dict:
    """調子の設定(data/game.json の condition。なければ既定値)。"""
    raw = game_config.get("condition") if game_config is not None else None
    return {**DEFAULTS, **(raw or {})}


def _rng(seed: int, player_id: str, k: int) -> random.Random:
    from .season import derive_seed  # season が condition を使うので、ここで読む

    return random.Random(derive_seed(seed, f"cond:{player_id}:{k}"))


def _initial(seed: int, player_id: str, cfg: dict) -> int:
    r = _rng(seed, player_id, 0).random()
    acc = 0.0
    for level, share in zip(LEVELS, cfg["initial"]):
        acc += float(share)
        if r < acc:
            return level
    return LEVELS[-1]


def _step(level: int, seed: int, player_id: str, k: int, cfg: dict) -> int:
    r = _rng(seed, player_id, k).random()
    up = max(0.0, float(cfg["up"]) - float(cfg["pull"]) * level)
    down = max(0.0, float(cfg["down"]) + float(cfg["pull"]) * level)
    if r < up and level < LEVELS[-1]:
        return level + 1
    if up <= r < up + down and level > LEVELS[0]:
        return level - 1
    return level


def steps_at(day: int, start: int, cfg: dict) -> int:
    """その日(0 から数えた日数。その日の試合の前)までに、調子が変わった回数。start は調子を始めた日。"""
    return max(0, (int(day) - int(start)) // int(cfg["period_days"]))


def level_at(seed: int, player_id: str, day: int, start: int, cfg: dict) -> int:
    """その日の試合の段階(初めから数え直す。試合の再現用)。"""
    level = _initial(seed, player_id, cfg)
    for k in range(1, steps_at(day, start, cfg) + 1):
        level = _step(level, seed, player_id, k, cfg)
    return level


class Conditions:
    """シーズンの選手の調子を、日を進めるたびに少しずつ計算して覚えておく(毎回数え直さない)。"""

    def __init__(self, seed: int, cfg: dict, start: int = 0):
        self.seed = seed
        self.cfg = cfg
        self.start = int(start)
        self._cache: dict[str, tuple[int, int]] = {}  # 選手 ID → (回, 段階)

    def level(self, player_id: str, day: int) -> int:
        target = steps_at(day, self.start, self.cfg)
        k, level = self._cache.get(player_id, (0, None))
        if level is None or k > target:
            k, level = 0, _initial(self.seed, player_id, self.cfg)
        while k < target:
            k += 1
            level = _step(level, self.seed, player_id, k, self.cfg)
        self._cache[player_id] = (k, level)
        return level

    def form(self, player_id: str, day: int) -> float:
        return self.level(player_id, day) * float(self.cfg["step"])


def public(level: int) -> dict:
    """画面に出す形(段階・名前・矢印)。"""
    return {"level": int(level), "label": LABELS[int(level)], "mark": MARKS[int(level)]}
