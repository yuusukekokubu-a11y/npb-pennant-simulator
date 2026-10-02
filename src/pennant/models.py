"""選手・球団・リーグのデータの形。

隠し情報(潜在能力・成長タイプ・生成時の型・能力の揺れ)は Player.hidden にまとめ、
画面に出してよい情報と区別できるようにする(D-017、D-030、D-033、D-043)。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class HiddenInfo:
    """選手本人も画面も知らない前提の情報。計算や表示の前提にしない。"""

    potential: dict[str, float]  # 潜在能力(ピーク時の能力。D-030)
    growth_type: str  # 成長タイプ:early / normal / late(D-033)
    archetype: str  # 生成時の型。年齢で変わらない。計算には使わない(D-043)
    ability_drift: dict[str, float]  # 能力の揺れ(翌年以降に残る。D-033)


@dataclass
class PlayerState:
    """試合や年ごとに変わる「状態」(D-024)。"""

    form: float = 0.0  # 好調・不調(その年だけの上下。点数。D-033)


@dataclass
class Player:
    id: str
    family_name: str
    given_name: str
    age: int
    role: str  # "pitcher" / "batter"
    position: str  # SP, RP, C, 1B, ...(abilities.POSITION_LABELS)
    bats: str | None  # 打者のみ:R / L / S(両打ち)。投手は打席に立たないので None(D-046)
    throws: str | None  # 投手のみ:R / L。野手の投げ手は持たないので None(D-048)
    ratings: dict[str, float]  # 現在の能力(内部は小数。20〜80 の外も許す。D-024)
    hidden: HiddenInfo
    state: PlayerState = field(default_factory=PlayerState)
    team_id: str | None = None
    origin: str | None = None  # 新人の出身区分(high_school など)。初期選手は None

    @property
    def name(self) -> str:
        return f"{self.family_name} {self.given_name}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Team:
    id: str
    league_index: int
    place: str
    nickname: str
    stadium: str
    players: list[Player] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.place}{self.nickname}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class League:
    seed: int
    league_names: list[str]
    teams: list[Team]

    def all_players(self) -> list[Player]:
        return [p for t in self.teams for p in t.players]

    def to_dict(self) -> dict:
        return asdict(self)
