"""選手名・球団名・球場名の生成(D-041)。

一般的な部品を組み合わせて作る。実在の名簿は使わない。
選手名はリーグ全体(全球団)で重複しないようにする(同じ球団・同じリーグの条件も満たす)。
"""

from __future__ import annotations

import random

from .config import ConfigError, NameParts

MAX_TRIES = 10_000


class NameGenerator:
    def __init__(self, parts: NameParts, rng: random.Random, used: set[tuple[str, str]] | None = None):
        self.parts = parts
        self.rng = rng
        self.used: set[tuple[str, str]] = set(used or ())

    @property
    def capacity(self) -> int:
        return len(self.parts.surnames) * len(self.parts.given_names)

    def person(self) -> tuple[str, str]:
        """まだ使われていない (姓, 名) を1つ返す。"""
        for _ in range(MAX_TRIES):
            name = (self.rng.choice(self.parts.surnames), self.rng.choice(self.parts.given_names))
            if name not in self.used:
                self.used.add(name)
                return name
        raise ConfigError(
            self.parts.source,
            [f"重複しない名前を作れませんでした(使用済み {len(self.used)} 件 / 組み合わせ {self.capacity} 通り)。姓・名の部品を増やしてください"],
        )


def team_identities(parts: NameParts, rng: random.Random, count: int) -> list[tuple[str, str, str]]:
    """(地名, 愛称, 球場名) を count 球団分作る。地名・愛称は重複させない。"""
    problems = []
    if len(parts.places) < count:
        problems.append(f"places が {len(parts.places)} 件しかありません(球団数 {count} 以上が必要)")
    if len(parts.team_nicknames) < count:
        problems.append(f"team_nicknames が {len(parts.team_nicknames)} 件しかありません(球団数 {count} 以上が必要)")
    if problems:
        raise ConfigError(parts.source, problems)
    places = rng.sample(parts.places, count)
    nicknames = rng.sample(parts.team_nicknames, count)
    return [(p, n, f"{p}{rng.choice(parts.stadium_suffixes)}") for p, n in zip(places, nicknames)]
