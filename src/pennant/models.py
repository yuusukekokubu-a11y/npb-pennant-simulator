"""選手・球団・リーグのデータの形。

選手の正本は見せる能力(Player.abilities)。試合の計算は、そこから換算した内部の能力(Player.ratings)を使う(D-349)。
隠し情報(成長タイプ・伸びやすさ・弾道の傾き・生成時の型)は Player.hidden にまとめる(D-350)。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class HiddenInfo:
    """画面に出さない情報(作り直し①。D-337、D-350)。潜在能力(ピーク時の能力)は持たない。"""

    growth_type: str  # 成長タイプ:early / normal / late(年齢のピークの早い・遅い。D-033)
    archetype: str  # 生成時の型。年齢で変わらない。計算には使わない(D-043)
    ease: float = 1.0  # 伸びやすさ(伸びる年の増え方に掛ける。D-350)
    trajectory_bias: float = 0.0  # 弾道の傾き(打者。パワーに足して弾道を決める。D-347)


@dataclass(frozen=True)
class ParkFactors:
    """球場の真の倍率(整数の千分率。1000 = 1.0。隠し情報。D-136〜D-138)。"""

    home_run: int = 1000  # 本塁打の倍率
    babip: int = 1000  # インプレーの安打(BABIP)の倍率

    @property
    def home_run_multiplier(self) -> float:
        return self.home_run / 1000

    @property
    def babip_multiplier(self) -> float:
        return self.babip / 1000


NEUTRAL_PARK = ParkFactors()


@dataclass
class PlayerState:
    """試合や年ごとに変わる「状態」(D-024)。"""

    form: float = 0.0  # 好調・不調(その年だけの上下。点数。D-033)
    fatigue: float = 0.0  # 投手の疲労(投げた打者数から増え、日ごとに回復力で減る。D-060)


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
    ratings: dict[str, float]  # 試合の計算に使う内部の能力(見せる能力から換算。ability_model.refresh。D-349)
    hidden: HiddenInfo
    state: PlayerState = field(default_factory=PlayerState)
    team_id: str | None = None
    origin: str | None = None  # 新人の出身区分(high_school など)。初期選手は None
    scouting: dict | None = None  # 入団時のスカウト評価(獲得した球団の評価。F3-1。D-199)。初期選手・自動補充の前の選手は None
    contract: dict | None = None  # 契約({salary: 年俸(万円), until: 満了シーズン, history: [{year, salary, years, reason, offers?}]}。F3-2a。D-232)。所属がなければ None でもよい
    abilities: dict[str, float] = field(default_factory=dict)  # 見せる能力(正本。内部と同じ目盛りの小数。D-336、D-349)
    pitches: list[dict] | None = None  # 投手の球種 [{type, weight}](D-351)。打者は None
    skills: list[str] = field(default_factory=list)  # 特殊能力のキー(data/skills.json。D-374)
    fa_seasons: int | None = 0  # 一軍に登録されたシーズンの通算(FA 権の年数。宣言したら 0。F3-2c。D-258)。None は旧版で、読み込みのときに補う

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
    display_name: str | None = None  # 利用者が入力した球団名(セーブデータにだけ入る。D-104)。なければ架空の初期名
    park: ParkFactors = field(default_factory=ParkFactors)  # 本拠地の球場の真の倍率(隠し情報。D-138)

    @property
    def name(self) -> str:
        return self.display_name or f"{self.place}{self.nickname}"

    @property
    def default_name(self) -> str:
        """架空の初期名(地名 + 愛称)。"""
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
