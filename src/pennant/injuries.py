"""けが(D-445)。

試合(一軍・二軍・ポストシーズン)のあとに、出た選手が小さな確率でけがをする。
  - 野手はスタメンで出た試合ごと、投手は対戦打者ごとの確率(data/injuries.json の rate)。
  - 掛けるもの:年齢(age.from 歳から 1 歳ごとに +per_year)、投手の試合前の疲労(1 点ごとに +per_point、上限 max)、
    特殊能力「ケガしにくさ◯/×」(skills の倍率)、球団のトレーナー・医療の設備(年齢による下がり方と同じ倍率)。
  - 重さ(severity)ごとの日数に、医療の設備の倍率を掛ける。シーズン全休は、そのシーズンの間ずっと。
  - 重い・全休のけがは、aftereffect.probability で能力が少し下がる(年度の確定のときに当てる。内部の目盛り)。
乱数は試合とは別の系列なので、けがの有無で試合の結果は変わらない。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, _Checker, _read_json

SEASON_OUT = 10**6  # シーズン全休の「戻れる日」
AFTER_ITEMS = {"batter": ("meet", "power", "speed", "arm", "defense"), "pitcher": ("velocity", "control", "stamina", "breaking")}


@dataclass(frozen=True)
class InjurySettings:
    data: dict
    source: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def load_injury_settings(path: str | Path | None = None) -> InjurySettings:
    data, source = _read_json(path, "injuries.json")
    return validate_injury_settings(data, source)


def validate_injury_settings(data: Any, source: str = "(辞書)") -> InjurySettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    rate = c.section(c.get(root, "rate", ""), "rate")
    c.number(c.get(rate, "batter_per_start", "rate"), "rate.batter_per_start", 0, 0.1)
    c.number(c.get(rate, "pitcher_per_batter", "rate"), "rate.pitcher_per_batter", 0, 0.01)
    age = c.section(c.get(root, "age", ""), "age")
    c.integer(c.get(age, "from", "age"), "age.from", 15, 60)
    c.number(c.get(age, "per_year", "age"), "age.per_year", 0, 1)
    fat = c.section(c.get(root, "fatigue", ""), "fatigue")
    c.number(c.get(fat, "per_point", "fatigue"), "fatigue.per_point", 0, 1)
    c.number(c.get(fat, "max", "fatigue"), "fatigue.max", 0, 5)
    skills = c.section(c.get(root, "skills", ""), "skills")
    for k, v in (skills or {}).items():
        c.number(v, f"skills.{k}", 0, 10)
    sev = c.get(root, "severity", "")
    if not isinstance(sev, list) or not sev:
        c.add("severity", "重さの並びを書いてください")
    else:
        total = 0.0
        for i, s in enumerate(sev):
            s = c.section(s, f"severity[{i}]")
            if s is None:
                continue
            for k in ("key", "label"):
                if not isinstance(s.get(k), str) or not s[k]:
                    c.add(f"severity[{i}].{k}", "名前を書いてください")
            share = c.number(c.get(s, "share", f"severity[{i}]"), f"severity[{i}].share", 0, 1)
            total += share or 0.0
            d = s.get("days")
            if not (isinstance(d, list) and len(d) == 2 and all(isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in d) and d[0] <= d[1]):
                c.add(f"severity[{i}].days", "[最短, 最長] の日数(1 以上の整数)で書いてください")
        if abs(total - 1.0) > 1e-6:
            c.add("severity", f"割合(share)の合計が 1 になっていません(合計: {total:.4f})")
    after = c.section(c.get(root, "aftereffect", ""), "aftereffect")
    c.number(c.get(after, "probability", "aftereffect"), "aftereffect.probability", 0, 1)
    for k in ("items", "points"):
        v = c.get(after, k, "aftereffect")
        if v is not None and not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and x >= 0 for x in v) and v[0] <= v[1]):
            c.add(f"aftereffect.{k}", "[最小, 最大] の整数で書いてください")
    if c.problems:
        raise ConfigError(source, c.problems)
    return InjurySettings(copy.deepcopy(root), source)


_DEFAULT: list[InjurySettings] = []


def default_settings() -> InjurySettings:
    if not _DEFAULT:
        _DEFAULT.append(load_injury_settings())
    return _DEFAULT[0]


def multiplier(player, settings: InjurySettings, medical: float, fatigue: float = 0.0) -> float:
    """けがの起こりやすさの倍率(年齢・疲労・特殊能力・医療の設備)。"""
    a = settings["age"]
    m = 1.0 + max(0, int(player.age) - int(a["from"])) * float(a["per_year"])
    f = settings["fatigue"]
    m *= 1.0 + min(float(f["max"]), max(0.0, fatigue) * float(f["per_point"]))
    for key in player.skills:
        m *= float(settings["skills"].get(key, 1.0))
    return m * medical


def roll(rng: random.Random, settings: InjurySettings, medical: float, day: int) -> dict:
    """けがの重さと戻れる日、後遺症を決める。"""
    x = rng.random()
    acc = 0.0
    chosen = settings["severity"][-1]
    for s in settings["severity"]:
        acc += float(s["share"])
        if x < acc:
            chosen = s
            break
    lo, hi = chosen["days"]
    if chosen["key"] == "season":
        until = SEASON_OUT
        days = None
    else:
        days = max(1, round(rng.randint(int(lo), int(hi)) * medical))
        until = day + 1 + days
    after = None
    if chosen["key"] in ("heavy", "season"):
        a = settings["aftereffect"]
        if rng.random() < float(a["probability"]):
            after = {"items": rng.randint(*a["items"]), "points": [rng.randint(*a["points"]) for _ in range(a["items"][1])]}
    return {"severity": chosen["key"], "label": chosen["label"], "days": days, "until": until, "after": after}


def check_game(result, context_fatigue: dict, players: dict, team_medical: dict, settings: InjurySettings, rng: random.Random, day: int) -> list[dict]:
    """1 試合のけが(出た選手ごとに)。戻り値は [{player_id, team_id, ...roll}](打順・投手の登板順の決まった順)。"""
    rate = settings["rate"]
    out = []
    for tid in (result.away_team_id, result.home_team_id):
        for _, pid, _, _ in result.lineups.get(tid, []):
            p = players.get(pid)
            if p is not None and rng.random() < float(rate["batter_per_start"]) * multiplier(p, settings, team_medical.get(tid, 1.0)):
                out.append({"player_id": pid, "team_id": tid, **roll(rng, settings, team_medical.get(tid, 1.0), day)})
    seen: set[str] = {x["player_id"] for x in out}
    for line in result.pitchers:
        p = players.get(line.pitcher_id)
        if p is None or line.pitcher_id in seen:
            continue
        prob = 1.0 - (1.0 - float(rate["pitcher_per_batter"]) * multiplier(p, settings, team_medical.get(line.team_id, 1.0), context_fatigue.get(line.pitcher_id, 0.0))) ** max(0, line.batters_faced)
        if rng.random() < prob:
            out.append({"player_id": line.pitcher_id, "team_id": line.team_id, **roll(rng, settings, team_medical.get(line.team_id, 1.0), day)})
            seen.add(line.pitcher_id)
    return out


def apply_aftereffect(player, after: dict, rng_seed: int) -> dict[str, int]:
    """後遺症:見せる能力を少し下げる(年度の確定のとき。戻り値は 項目 → 下げた点)。"""
    rng = random.Random(rng_seed)
    items = list(AFTER_ITEMS.get(player.role, ()))
    rng.shuffle(items)
    out = {}
    for item, pts in zip(items[: int(after["items"])], after["points"]):
        if item in player.abilities and pts > 0:
            player.abilities[item] = float(player.abilities[item]) - pts
            out[item] = pts
    return out
