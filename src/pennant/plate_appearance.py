"""1回の打席の計算(段階1・第1弾の実装 ②)。

決め方(D-035〜D-038):
  第1段階:三振・四球・死球・本塁打・インプレー(打球がグラウンドに飛んだ)のどれか
  第2段階(インプレーのとき):
    (a) 打球の種類(ゴロ・ライナー・フライ)
    (b) 担当ポジション(どの野手が処理するか)
    (c) 結果(アウト・単打・二塁打・三塁打・失策)
合成はオッズ比法(D-036):リーグ平均の率を「オッズ(起きる:起きない の比)」に直し、
打者側・投手側・左右の相性の倍率を掛け、確率に戻してから、合計が 1 になるよう調整(正規化)する。

使うのは能力値(好調・不調を足したもの)だけ。生成時の型・成長タイプ・潜在能力・
年齢・名前は使わない(D-043)。計算方法は、同じ形のクラスを作れば差し替えられる。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping, Protocol

from .abilities import BATTER, FIELDER_POSITIONS, PITCHER
from .models import Player
from .pa_config import BATTED_BALL_TYPES, HIT_TYPES, STAGE1_EVENTS, PlateAppearanceConfig, load_pa_config

IN_PLAY = "in_play"

# 打席の結果の種類(今回の範囲)
RESULTS = (
    "strikeout",
    "walk",
    "hit_by_pitch",
    "home_run",
    "ground_out",
    "line_out",
    "fly_out",
    "single",
    "double",
    "triple",
    "error",
)
RESULT_LABELS = {
    "strikeout": "三振",
    "walk": "四球",
    "hit_by_pitch": "死球",
    "home_run": "本塁打",
    "ground_out": "ゴロアウト",
    "line_out": "ライナーアウト",
    "fly_out": "フライアウト",
    "single": "単打",
    "double": "二塁打",
    "triple": "三塁打",
    "error": "失策",
}
BATTED_BALL_LABELS = {"ground": "ゴロ", "line": "ライナー", "fly": "フライ"}

Defense = Mapping[str, Player]  # 守備側の野手8人(ポジション → 選手)。投手と指名打者は含まない


@dataclass(frozen=True)
class BaseOutState:
    """走者・アウト状況(8通りの走者 × 3通りのアウト数 = 24通り。D-026)。"""

    outs: int = 0
    first: bool = False
    second: bool = False
    third: bool = False

    def __post_init__(self) -> None:
        if self.outs not in (0, 1, 2):
            raise ValueError(f"アウト数は 0〜2 にしてください(値: {self.outs})")

    @property
    def index(self) -> int:
        """0〜23 の通し番号。"""
        return self.outs * 8 + int(self.first) + 2 * int(self.second) + 4 * int(self.third)

    @classmethod
    def from_index(cls, index: int) -> "BaseOutState":
        if not 0 <= index < 24:
            raise ValueError(f"走者・アウト状況の番号は 0〜23 にしてください(値: {index})")
        outs, runners = divmod(index, 8)
        return cls(outs, bool(runners & 1), bool(runners & 2), bool(runners & 4))


@dataclass(frozen=True)
class PlateAppearance:
    """1回の打席の記録(打席ログの1行)。"""

    result: str  # RESULTS のどれか
    batter_id: str
    pitcher_id: str
    batter_side: str  # 実際に立った打席(R / L)。両打ちは投手と逆側
    base_out: BaseOutState  # 入力をそのまま記録(今回の計算には使わない。D-026)
    batted_ball: str | None = None  # インプレーのとき:ground / line / fly
    fielder: str | None = None  # インプレーのとき:担当ポジション(P, C, 1B, ...)
    unfieldable: bool = False  # 野手が処理できない打球だったか(D-038)
    pitch_type: str | None = None  # 決着球の球種(予約欄。今は空欄。D-025)


def ratio_multiplier(rating: float, ratio_per_10: float) -> float:
    """能力値から倍率へ:50 で 1 倍、10 点上がるごとに ratio_per_10 倍。"""
    return ratio_per_10 ** ((rating - 50.0) / 10.0)


def combine(base: Mapping[str, float], multipliers: Mapping[str, float]) -> dict[str, float]:
    """オッズ比法で倍率を掛け、合計が 1 になるよう正規化した確率を返す。

    base は合計 1 の確率。multipliers にない結果は倍率 1(そのまま)。
    """
    adjusted: dict[str, float] = {}
    for key, p in base.items():
        m = multipliers.get(key, 1.0)
        if p <= 0:
            adjusted[key] = 0.0
        elif p >= 1:
            adjusted[key] = 1.0
        else:
            odds = p / (1 - p) * m
            adjusted[key] = odds / (1 + odds)
    total = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()}


def _choose(rng: random.Random, probs: Mapping[str, float]) -> str:
    r = rng.random()
    cumulative = 0.0
    last = None
    for key, p in probs.items():
        if p <= 0:
            continue
        cumulative += p
        last = key
        if r < cumulative:
            return key
    return last  # 丸め誤差で最後まで届いたとき


def batter_side(batter: Player, pitcher: Player) -> str:
    """実際に立つ打席。両打ちは、投手の投げ手と逆の側(有利な側)を選ぶ(D-037)。"""
    if batter.bats == "S":
        return "L" if pitcher.throws == "R" else "R"
    return batter.bats


class PlateAppearanceModel(Protocol):
    """打席の計算の形(差し替え用)。この形を満たすクラスなら、別の計算方法に替えられる。"""

    def probabilities(self, batter: Player, pitcher: Player, defense: Defense) -> dict[str, float]: ...

    def rating(self, player: Player, item: str) -> float:
        """計算に使う能力値(好調・不調を含む)。走者の進塁など、試合の計算でも使う。"""
        ...

    def fielding(self, position: str, pitcher: Player, defense: Defense) -> dict[str, float]:
        """担当ポジションの守備の能力(range / arm / fielding)。"""
        ...

    def resolve(
        self, batter: Player, pitcher: Player, defense: Defense, base_out: BaseOutState, rng: random.Random
    ) -> PlateAppearance: ...


def check_participants(batter: Player, pitcher: Player, defense: Defense) -> None:
    """打席に立てる組み合わせかを確かめる(指名打者制。D-046)。"""
    if batter.role != BATTER:
        raise ValueError(f"{batter.id} は投手です。投手は打席に立ちません(指名打者制。D-046)")
    if pitcher.role != PITCHER:
        raise ValueError(f"{pitcher.id} は投手ではありません")
    if set(defense) != set(FIELDER_POSITIONS):
        raise ValueError(f"守備は {', '.join(FIELDER_POSITIONS)} の8人を1人ずつ指定してください(投手と指名打者は含めない)")
    for pos, p in defense.items():
        if p.role != BATTER:
            raise ValueError(f"{pos} の {p.id} は投手です。野手を指定してください")


class OddsRatioModel:
    """オッズ比法による打席の計算(D-035〜D-038)。"""

    def __init__(self, config: PlateAppearanceConfig | None = None):
        self.config = config or load_pa_config()

    # ---- 能力の取り出し ----

    def rating(self, player: Player, item: str) -> float:
        """能力値に好調・不調を足したもの(D-033)。"""
        return player.ratings[item] + self.config["form_weight"] * player.state.form

    def fielding(self, position: str, pitcher: Player, defense: Defense) -> dict[str, float]:
        if position == "P":
            return dict(self.config["pitcher_fielding"])  # 投手は守備の能力を持たないので固定値
        fielder = defense[position]
        return {item: self.rating(fielder, item) for item in ("range", "arm", "fielding")}

    def _multiplier(self, player: Player | None, effects: Mapping[str, float], ratings: Mapping[str, float] | None = None) -> float:
        m = 1.0
        for item, ratio in effects.items():
            value = ratings[item] if ratings is not None else self.rating(player, item)
            m *= ratio_multiplier(value, ratio)
        return m

    def _platoon(self, batter: Player, pitcher: Player) -> dict[str, float]:
        side = "same_side" if batter_side(batter, pitcher) == pitcher.throws else "opposite_side"
        return self.config["platoon"][side]

    # ---- 第1段階 ----

    def stage1(self, batter: Player, pitcher: Player) -> dict[str, float]:
        s1 = self.config["stage1"]
        base = dict(s1["league_rates"])
        base[IN_PLAY] = 1.0 - sum(base.values())
        platoon = self._platoon(batter, pitcher)
        mult = {}
        for event in STAGE1_EVENTS:
            m = self._multiplier(batter, s1["batter_effects"].get(event, {}))
            m *= self._multiplier(pitcher, s1["pitcher_effects"].get(event, {}))
            m *= platoon.get(event, 1.0)
            mult[event] = m
        return combine(base, mult)

    # ---- 第2段階 ----

    def batted_ball(self, batter: Player, pitcher: Player) -> dict[str, float]:
        bb = self.config["batted_ball"]
        mult = {}
        for t in BATTED_BALL_TYPES:
            m = self._multiplier(batter, bb["batter_effects"].get(t, {}))
            m *= self._multiplier(pitcher, bb["pitcher_effects"].get(t, {}))
            mult[t] = m
        return combine(bb["league_shares"], mult)

    def fielder_shares(self, batted_ball: str) -> dict[str, float]:
        return dict(self.config["fielder_shares"][batted_ball])

    def extra_base(self, batted_ball: str, fielder: str | None) -> dict:
        """安打の内訳の設定。担当ポジションごとの設定があればそちらを使う(D-072)。"""
        cfg = self.config["in_play"][batted_ball]
        return cfg.get("extra_base_by_fielder", {}).get(fielder, cfg["extra_base"])

    def in_play(
        self,
        batter: Player,
        pitcher: Player,
        batted_ball: str,
        fielding: Mapping[str, float],
        fielder: str | None = None,
    ) -> dict[str, float]:
        """担当野手が決まったあとの結果の確率。fielder は担当ポジション(安打の内訳に使う)。

        返す辞書:unfieldable_<単打など>(野手が処理できない安打)、single/double/triple(処理できた打球の安打)、error、out
        """
        cfg = self.config["in_play"][batted_ball]
        unf = cfg["unfieldable_share"]
        fieldable_hit = (cfg["hit_rate"] - unf) / (1 - unf)
        base = {"hit": fieldable_hit, "error": cfg["error_rate"]}
        base["out"] = 1.0 - base["hit"] - base["error"]
        he = cfg["hit_effects"]
        hit_m = self._multiplier(batter, he.get("batter", {}))
        hit_m *= self._multiplier(pitcher, he.get("pitcher", {}))
        hit_m *= self._multiplier(None, he.get("fielder", {}), fielding)
        hit_m *= self._platoon(batter, pitcher).get("in_play_hit", 1.0)
        err_m = self._multiplier(None, cfg["error_effects"].get("fielder", {}), fielding)
        fieldable = combine(base, {"hit": hit_m, "error": err_m})

        xb = self.extra_base(batted_ball, fielder)
        split = combine(
            xb["shares"],
            {h: self._multiplier(batter, xb["effects"].get(h, {})) for h in HIT_TYPES},
        )
        result = {}
        for h in HIT_TYPES:
            result[f"unfieldable_{h}"] = unf * split[h]
            result[h] = (1 - unf) * fieldable["hit"] * split[h]
        result["error"] = (1 - unf) * fieldable["error"]
        result["out"] = (1 - unf) * fieldable["out"]
        return result

    # ---- まとめ ----

    def probabilities(self, batter: Player, pitcher: Player, defense: Defense) -> dict[str, float]:
        """打席の結果の確率(すべての場合を足し合わせた正確な値)。合計は 1。"""
        check_participants(batter, pitcher, defense)
        s1 = self.stage1(batter, pitcher)
        probs = {r: 0.0 for r in RESULTS}
        for event in STAGE1_EVENTS:
            probs[event] += s1[event]
        for t, pt in self.batted_ball(batter, pitcher).items():
            for pos, pf in self.fielder_shares(t).items():
                w = s1[IN_PLAY] * pt * pf
                if w == 0:
                    continue
                ip = self.in_play(batter, pitcher, t, self.fielding(pos, pitcher, defense), pos)
                for h in HIT_TYPES:
                    probs[h] += w * (ip[h] + ip[f"unfieldable_{h}"])
                probs["error"] += w * ip["error"]
                probs[f"{t}_out"] += w * ip["out"]
        return probs

    def resolve(
        self, batter: Player, pitcher: Player, defense: Defense, base_out: BaseOutState, rng: random.Random
    ) -> PlateAppearance:
        """乱数で1回の打席の結果を決め、記録を返す。"""
        check_participants(batter, pitcher, defense)
        common = {
            "batter_id": batter.id,
            "pitcher_id": pitcher.id,
            "batter_side": batter_side(batter, pitcher),
            "base_out": base_out,
        }
        event = _choose(rng, self.stage1(batter, pitcher))
        if event != IN_PLAY:
            return PlateAppearance(result=event, **common)
        t = _choose(rng, self.batted_ball(batter, pitcher))
        pos = _choose(rng, self.fielder_shares(t))
        outcome = _choose(rng, self.in_play(batter, pitcher, t, self.fielding(pos, pitcher, defense), pos))
        unfieldable = outcome.startswith("unfieldable_")
        result = outcome.removeprefix("unfieldable_")
        if result == "out":
            result = f"{t}_out"
        return PlateAppearance(result=result, batted_ball=t, fielder=pos, unfieldable=unfieldable, **common)


_default_model: OddsRatioModel | None = None


def default_model() -> OddsRatioModel:
    global _default_model
    if _default_model is None:
        _default_model = OddsRatioModel()
    return _default_model


def resolve_plate_appearance(
    batter: Player,
    pitcher: Player,
    defense: Defense,
    base_out: BaseOutState,
    rng: random.Random,
    model: PlateAppearanceModel | None = None,
) -> PlateAppearance:
    """1回の打席の結果を決める。rng は random.Random(シード) を渡す(同じシードなら同じ結果)。"""
    return (model or default_model()).resolve(batter, pitcher, defense, base_out, rng)
