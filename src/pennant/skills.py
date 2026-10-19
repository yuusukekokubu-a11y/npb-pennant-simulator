"""特殊能力(D-374):良いもの・悪いものの対。内部の能力(試合の計算に使う能力)に足して効かせる。

- 種類は data/skills.json に書く(あとから足せる)。各特殊能力は 役割(野手・投手)・対(同じ対は 1 人に 1 つまで)・
  いつ効くか(always:いつも / risp:得点圏(2 塁か 3 塁に走者) / vs_left:相手が左(野手は左投手、投手は左打者))・
  内部の能力に足す値・作るときの確率・条件(requires:見せる能力の下限)を持つ。
- いつも効くものは ability_model.engine_ratings で内部の能力に足す。条件つきのものは打席ごとに plate_appearance で足す。
- 付け方は、ほかの乱数とは別の系列(derive_seed(0, "skills:<選手 ID>:<名前>"))。毎年のオフの変化は derive_seed(オフのシード, "skills:<選手 ID>")。
- 見せる能力の数値は変えない。隠し情報ではない(プロの選手は画面に出す。ドラフト候補は見せない)。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json
from .models import Player

WHEN = ("always", "risp", "vs_left", "steal", "catching", "injury")  # steal:盗塁のときだけ / catching:捕手として出ているとき(D-428) / injury:けがの起こりやすさ(試合の計算には効かない。D-445)


@dataclass(frozen=True)
class SkillSettings:
    data: dict
    source: str

    @property
    def skills(self) -> dict[str, dict]:
        return self.data["skills"]

    def label(self, key: str) -> str:
        return str(self.skills[key]["label"])

    @property
    def yearly(self) -> dict:
        return self.data["yearly"]


def load_skill_settings(path: str | Path | None = None) -> SkillSettings:
    data, source = _read_json(path, "skills.json")
    return validate_skill_settings(data, source)


def validate_skill_settings(root, source: str = "skills.json") -> SkillSettings:
    from .abilities import ALL_ABILITIES, BATTER_ITEMS, PITCHER_ITEMS

    c = _Checker()
    if not isinstance(root, dict):
        raise ConfigError(source, ["全体がまとまり({ })ではありません"])
    y = c.section(c.get(root, "yearly", ""), "yearly")
    c.number(c.get(y, "gain", "yearly"), "yearly.gain", 0, 1)
    c.number(c.get(y, "lose", "yearly"), "yearly.lose", 0, 1)
    skills = c.section(c.get(root, "skills", ""), "skills")
    for key, s in (skills or {}).items():
        w = f"skills.{key}"
        s = c.section(s, w)
        if s is None:
            continue
        if not isinstance(s.get("label"), str) or not s["label"].strip():
            c.add(f"{w}.label", "名前を書いてください")
        role = s.get("role")
        if role not in ("batter", "pitcher"):
            c.add(f"{w}.role", "batter か pitcher にしてください")
            continue
        if not isinstance(s.get("good"), bool):
            c.add(f"{w}.good", "true か false にしてください")
        if not isinstance(s.get("pair"), str) or not s["pair"]:
            c.add(f"{w}.pair", "対の名前を書いてください")
        if s.get("when") not in WHEN:
            c.add(f"{w}.when", f"{' / '.join(WHEN)} のどれかにしてください")
        items = BATTER_ITEMS if role == "batter" else PITCHER_ITEMS
        eff = c.section(c.get(s, "effects", w), f"{w}.effects")
        for item, v in (eff or {}).items():
            if item not in items:
                c.add(f"{w}.effects.{item}", "その役割の内部の能力ではありません")
            c.number(v, f"{w}.effects.{item}", -40, 40)
        c.number(c.get(s, "probability", w), f"{w}.probability", 0, 1)
        if "lead" in s:
            c.number(s["lead"], f"{w}.lead", -10, 10)
        if "positions" in s:
            from .abilities import FIELDER_POSITIONS

            if not isinstance(s["positions"], list) or not s["positions"] or any(x not in FIELDER_POSITIONS for x in s["positions"]):
                c.add(f"{w}.positions", f"ポジションを並べてください(使えるもの: {', '.join(FIELDER_POSITIONS)})")
        for item, v in (s.get("requires") or {}).items():
            if item not in ALL_ABILITIES:
                c.add(f"{w}.requires.{item}", "見せる能力の名前ではありません")
            c.number(v, f"{w}.requires.{item}", 0, 120)
    if c.problems:
        raise ConfigError(source, c.problems)
    return SkillSettings(copy.deepcopy(root), source)


_DEFAULT: list[SkillSettings] = []


def default_settings() -> SkillSettings:
    if not _DEFAULT:
        _DEFAULT.append(load_skill_settings())
    return _DEFAULT[0]


# ---- 効き方 ----

def always_effects(player: Player, settings: SkillSettings | None = None) -> dict[str, float]:
    """いつも効く特殊能力の、内部の能力に足す値の合計。"""
    settings = settings or default_settings()
    out: dict[str, float] = {}
    for key in player.skills or ():
        s = settings.skills.get(key)
        if s is not None and s["when"] == "always":
            for item, v in s["effects"].items():
                out[item] = out.get(item, 0.0) + float(v)
    return out


def situational(player: Player, risp: bool, vs_left: bool, settings: SkillSettings | None = None) -> dict[str, float]:
    """この打席で効く条件つきの特殊能力の、内部の能力に足す値の合計(なければ空)。"""
    if not player.skills:
        return {}
    settings = settings or default_settings()
    out: dict[str, float] = {}
    for key in player.skills:
        s = settings.skills.get(key)
        if s is None or not ((s["when"] == "risp" and risp) or (s["when"] == "vs_left" and vs_left)):
            continue
        for item, v in s["effects"].items():
            out[item] = out.get(item, 0.0) + float(v)
    return out


def _when_effects(player: Player, when: str, settings: SkillSettings | None) -> dict[str, float]:
    if not player.skills:
        return {}
    settings = settings or default_settings()
    out: dict[str, float] = {}
    for key in player.skills:
        s = settings.skills.get(key)
        if s is not None and s["when"] == when:
            for item, v in s["effects"].items():
                out[item] = out.get(item, 0.0) + float(v)
    return out


def steal_effects(player: Player, settings: SkillSettings | None = None) -> dict[str, float]:
    """盗塁を走るか・成功するかを決めるときだけ効く値(盗塁◯/×。D-428)。"""
    return _when_effects(player, "steal", settings)


def catching_effects(player: Player, settings: SkillSettings | None = None) -> dict[str, float]:
    """捕手として出ているときに、盗塁を刺す・捕逸を防ぐ能力に足す値(キャッチャー◯/×。D-428)。"""
    return _when_effects(player, "catching", settings)


def lead_bonus(player: Player, settings: SkillSettings | None = None) -> float:
    """捕手のリードの効き:組む投手の全能力に足す値(キャッチャー◯/×。D-428)。"""
    if not player.skills:
        return 0.0
    settings = settings or default_settings()
    return sum(float(settings.skills[k].get("lead", 0.0)) for k in player.skills if k in settings.skills)


def shifted(player: Player, deltas: dict[str, float]) -> Player:
    """内部の能力に足した写し(打席の計算だけに使う。元の選手は変えない)。"""
    p = copy.copy(player)
    p.ratings = {k: v + deltas.get(k, 0.0) for k, v in player.ratings.items()}
    return p


# ---- 付け方 ----

def _eligible(player: Player, s: dict) -> bool:
    if s.get("positions") and player.position not in s["positions"]:
        return False
    return s["role"] == player.role and all(float(player.abilities.get(item, 0.0)) >= float(v) for item, v in (s.get("requires") or {}).items())


def initial_skills(player: Player, settings: SkillSettings | None = None) -> list[str]:
    """作るときの特殊能力(対ごとに、良いもの・悪いものの確率で 1 つまで)。別の乱数の系列なので、ほかの生成は変わらない。"""
    from .season import derive_seed

    settings = settings or default_settings()
    rng = random.Random(derive_seed(0, f"skills:{player.id}:{player.family_name}{player.given_name}"))
    out: list[str] = []
    pairs: dict[str, list[str]] = {}
    for key, s in settings.skills.items():
        if s["role"] == player.role:
            pairs.setdefault(s["pair"], []).append(key)
    for pair, keys in pairs.items():
        r = rng.random()
        acc = 0.0
        for key in keys:
            s = settings.skills[key]
            acc += float(s["probability"]) if _eligible(player, s) else 0.0
            if r < acc:
                out.append(key)
                break
    return out


def yearly_change(player: Player, seed: int, settings: SkillSettings | None = None) -> dict[str, list[str]]:
    """毎年のオフの変化:確率で 1 つ付き、確率で 1 つ消える。戻り値は {gained, lost}。内部の能力は呼んだ側で作り直す。"""
    from .season import derive_seed

    settings = settings or default_settings()
    rng = random.Random(derive_seed(seed, f"skills:{player.id}"))
    gained: list[str] = []
    lost: list[str] = []
    have = list(player.skills or [])
    if have and rng.random() < float(settings.yearly["lose"]):
        key = have[int(rng.random() * len(have))]
        have.remove(key)
        lost.append(key)
    if rng.random() < float(settings.yearly["gain"]):
        used = {settings.skills[k]["pair"] for k in have if k in settings.skills}
        cands = [k for k, s in settings.skills.items() if s["pair"] not in used and _eligible(player, s) and k not in lost]
        if cands:
            key = cands[int(rng.random() * len(cands))]
            have.append(key)
            gained.append(key)
    order = list(settings.skills)
    player.skills = sorted(have, key=lambda k: order.index(k) if k in order else len(order))
    return {"gained": gained, "lost": lost}


def public_skills(player: Player, settings: SkillSettings | None = None) -> list[dict]:
    """画面に出す形(名前と、良いものか)。"""
    settings = settings or default_settings()
    return [{"key": k, "label": settings.label(k), "good": bool(settings.skills[k]["good"])} for k in (player.skills or []) if k in settings.skills]
