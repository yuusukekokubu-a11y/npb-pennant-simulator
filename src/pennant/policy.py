"""チーム方針(D-454〜D-457):練習の重点・起用の方針・試合の作戦(盗塁・バント)。

- 12 球団すべてが方針を持つ(Team.policy)。普段は AI が球団の状況から決める(新規開始と年度の確定のとき。refresh_ai)。
  決め方は同じ形(PolicyMaker)の別のクラスに差し替えられる。既定は SimplePolicyMaker。
- あなたが変えた球団は manual になり、「AI に戻す」まで AI は決め直さない。
- 練習の重点は、年度の確定の伸び(年齢による増える分)を選んだ項目に寄せる倍率(practice_weights)。
- 起用の方針は、AI が一軍・二軍の顔ぶれを選ぶときの点(usage_bonus)。試合の作戦は試合の編成(TeamSetup)に渡す。
- 値は data/policy.json(仮置き。DESIGN 9 章)。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .abilities import BATTER, BATTER_ABILITIES, PITCHER, PITCHER_ABILITIES
from .config import ConfigError, _Checker, _read_json
from .models import League, Player, Team

SUPPORTED_FORMAT_VERSION = 1

PRACTICE_BATTER = {"balance": "バランス", "meet": "ミート", "power": "長打", "defense_running": "守備と走塁"}
PRACTICE_PITCHER = {"balance": "バランス", "velocity": "球威", "control_breaking": "制球と変化球", "stamina": "スタミナ"}
USAGE = {"youth": "若手を使う", "standard": "標準", "win": "勝ちにいく"}
STEAL = {"aggressive": "積極的", "normal": "普通", "cautious": "慎重"}
BUNT = {"many": "多い", "normal": "普通", "few": "少ない"}

# 方針の項目 → (名前, 選べる値)。画面の選ぶ欄の順
FIELDS = {
    "practice_batter": ("練習の重点(野手)", PRACTICE_BATTER),
    "practice_pitcher": ("練習の重点(投手)", PRACTICE_PITCHER),
    "usage": ("起用の方針", USAGE),
    "steal": ("盗塁の作戦", STEAL),
    "bunt": ("バントの作戦", BUNT),
}
DEFAULT = {"practice_batter": "balance", "practice_pitcher": "balance", "usage": "standard", "steal": "normal", "bunt": "normal"}


@dataclass(frozen=True)
class PolicySettings:
    data: dict
    source: str

    def practice_items(self, role: str, key: str) -> list[str]:
        return list(self.data["practice"]["batter" if role == BATTER else "pitcher"].get(key, []))


def load_policy_settings(path: str | Path | None = None) -> PolicySettings:
    data, source = _read_json(path, "policy.json")
    return validate_policy_settings(data, source)


def validate_policy_settings(data, source: str = "(辞書)") -> PolicySettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    pr = c.section(c.get(root, "practice", ""), "practice")
    c.number(c.get(pr, "boost", "practice"), "practice.boost", 1, 3)
    c.number(c.get(pr, "min_other", "practice"), "practice.min_other", 0, 1)
    for role, keys, items in (("batter", PRACTICE_BATTER, BATTER_ABILITIES), ("pitcher", PRACTICE_PITCHER, PITCHER_ABILITIES)):
        table = c.section(c.get(pr, role, "practice"), f"practice.{role}") or {}
        for key in keys:
            if key == "balance":
                continue
            v = c.get(table, key, f"practice.{role}")
            if v is not None and (not isinstance(v, list) or not v or any(x not in items for x in v)):
                c.add(f"practice.{role}.{key}", f"見せる能力の名前を 1 つ以上並べてください(使えるもの: {', '.join(items)})")
    us = c.section(c.get(root, "usage", ""), "usage")
    c.integer(c.get(us, "youth_age", "usage"), "usage.youth_age", 18, 45)
    c.number(c.get(us, "youth_bonus", "usage"), "usage.youth_bonus", 0, 50)
    c.number(c.get(us, "farm_youth_bonus", "usage"), "usage.farm_youth_bonus", 0, 50)
    ai = c.section(c.get(root, "ai", ""), "ai")
    for key, low, high in (("strength_top", 1, 70), ("win_teams", 0, 12), ("youth_teams", 0, 12), ("batters", 1, 40), ("pitchers", 1, 40), ("style_teams", 0, 6)):
        c.integer(c.get(ai, key, "ai"), f"ai.{key}", low, high)
    c.number(c.get(ai, "young_share", "ai"), "ai.young_share", 0, 1)
    c.number(c.get(ai, "practice_gap", "ai"), "ai.practice_gap", 0, 30)
    if c.problems:
        raise ConfigError(source, c.problems)
    return PolicySettings(copy.deepcopy(root), source)


_DEFAULT: list[PolicySettings] = []


def default_settings() -> PolicySettings:
    if not _DEFAULT:
        _DEFAULT.append(load_policy_settings())
    return _DEFAULT[0]


# ---- 方針の値 ----

def policy_of(team: Team) -> dict:
    """球団の方針(足りない項目は既定で補う)。manual はあなたが決めたか。"""
    raw = team.policy or {}
    out = {k: raw.get(k, v) for k, v in DEFAULT.items()}
    out["manual"] = bool(raw.get("manual", False))
    return out


def check_policy(d) -> tuple[dict | None, str | None]:
    """方針の点検(保存と画面の入力)。戻り値は(正しい形の方針, 問題の文)。"""
    if not isinstance(d, dict):
        return None, "チーム方針の形が違います"
    out = {}
    for key, (label, values) in FIELDS.items():
        v = d.get(key, DEFAULT[key])
        if v not in values:
            return None, f"{label}は {' / '.join(values.values())} のどれかにしてください(値: {v!r})"
        out[key] = v
    manual = d.get("manual", False)
    if not isinstance(manual, bool):
        return None, "あなたが決めたかは true / false にしてください"
    out["manual"] = manual
    return out, None


def plain(policy: dict) -> dict:
    """保存する形(既定の項目は書かない)。"""
    out = {k: policy[k] for k in DEFAULT if policy.get(k, DEFAULT[k]) != DEFAULT[k]}
    if policy.get("manual"):
        out["manual"] = True
    return out


# ---- 練習の重点(D-455) ----

def practice_weights(policy: dict, role: str, gains: Mapping[str, float], settings: PolicySettings | None = None) -> dict[str, float]:
    """伸び(年齢による増える分。項目 → 0 より大きい値)に掛ける倍率。選んだ項目は boost 倍、ほかは伸びの合計が変わらない倍率(下限 min_other)。
    バランス・伸びがない・選んだ項目しか伸びないときは空(倍率なし)。"""
    s = settings or default_settings()
    key = policy.get("practice_batter" if role == BATTER else "practice_pitcher", "balance")
    chosen = set(s.practice_items(role, key)) if key != "balance" else set()
    if not chosen:
        return {}
    total = sum(gains.values())
    picked = sum(v for k, v in gains.items() if k in chosen)
    others = total - picked
    if total <= 0 or picked <= 0 or others <= 0:
        return {}
    boost = float(s.data["practice"]["boost"])
    rest = max(float(s.data["practice"]["min_other"]), (total - boost * picked) / others)
    return {k: boost if k in chosen else rest for k in gains}


# ---- 起用の方針(D-456) ----

def youth_bonus(policy: dict, player: Player, farm: bool = False, settings: PolicySettings | None = None) -> float:
    """若手を使う方針で、25 歳以下の選手に足す点(二軍は farm)。"""
    if policy.get("usage") != "youth":
        return 0.0
    s = settings or default_settings()
    us = s.data["usage"]
    return float(us["farm_youth_bonus" if farm else "youth_bonus"]) if player.age <= int(us["youth_age"]) else 0.0


def keeps_current(policy: dict) -> bool:
    """今の一軍の有利を足すか(勝ちにいくは足さず、今の能力と調子だけで選ぶ)。"""
    return policy.get("usage") != "win"


# ---- AI の決め方(差し替えられる形) ----

class PolicyMaker(Protocol):
    def decide(self, league: League) -> dict[str, dict]:
        """球団 ID → 方針(manual を除く 5 項目)。"""
        ...


class SimplePolicyMaker:
    """簡易な決め方(D-454):戦力・若手の割合・能力のまとまりを 12 球団で比べる。使うのは見せる能力と年齢だけ。
    最後に監督の方針の好み(D-470)を当てる。"""

    def __init__(self, settings: PolicySettings | None = None):
        self.settings = settings or default_settings()

    def decide(self, league: League) -> dict[str, dict]:
        from .ability_model import overall
        from .staff import manager_pref

        ai = self.settings.data["ai"]
        teams = list(league.teams)
        n = len(teams)

        def ranked(value) -> list[str]:  # 高い順(同じなら ID の順)
            return [t.id for t in sorted(teams, key=lambda t: (-value(t), t.id))]

        def top(t: Team, role: str, k: int) -> list[Player]:
            return sorted((p for p in t.players if p.role == role), key=lambda p: (-overall(p), p.id))[:k]

        def mean(players: list[Player], items) -> float:
            vals = [float(p.abilities.get(i, 50.0)) for p in players for i in items]
            return sum(vals) / len(vals) if vals else 50.0

        strength = ranked(lambda t: sum(sorted((overall(p) for p in t.players), reverse=True)[: int(ai["strength_top"])]))
        win = set(strength[: int(ai["win_teams"])])
        weak = set(strength[n - int(ai["youth_teams"]):]) if int(ai["youth_teams"]) else set()
        batters = {t.id: top(t, BATTER, int(ai["batters"])) for t in teams}
        pitchers = {t.id: top(t, PITCHER, int(ai["pitchers"])) for t in teams}

        def practice(role: str, keys) -> dict[str, str]:
            groups = {k: self.settings.practice_items(role, k) for k in keys if k != "balance"}
            pool = batters if role == BATTER else pitchers
            means = {k: {t.id: mean(pool[t.id], items) for t in teams} for k, items in groups.items()}
            league_mean = {k: sum(v.values()) / n for k, v in means.items()}
            out = {}
            for t in teams:
                gaps = {k: means[k][t.id] - league_mean[k] for k in groups}
                worst = min(groups, key=lambda k: (gaps[k], list(groups).index(k)))
                out[t.id] = worst if gaps[worst] <= -float(ai["practice_gap"]) else "balance"
            return out

        pb = practice(BATTER, PRACTICE_BATTER)
        pp = practice(PITCHER, PRACTICE_PITCHER)
        k = int(ai["style_teams"])
        speed = ranked(lambda t: mean(batters[t.id], ["speed"]))
        power = ranked(lambda t: mean(batters[t.id], ["power"]))
        out = {}
        for t in teams:
            young = sum(1 for p in t.players if p.age <= int(self.settings.data["usage"]["youth_age"])) / max(1, len(t.players))
            usage = "win" if t.id in win else "youth" if t.id in weak or young >= float(ai["young_share"]) else "standard"
            steal = "aggressive" if t.id in speed[:k] else "cautious" if k and t.id in speed[n - k:] else "normal"
            bunt = "many" if k and t.id in power[n - k:] else "few" if t.id in power[:k] else "normal"
            out[t.id] = {"practice_batter": pb[t.id], "practice_pitcher": pp[t.id], "usage": usage, "steal": steal, "bunt": bunt}
            pref = manager_pref(t)  # 監督の方針の好み(D-470):その項目は好みの値にする
            if pref is not None:
                out[t.id][pref[0]] = pref[1]
        return out


def refresh_ai(league: League, maker: PolicyMaker | None = None) -> None:
    """あなたが決めていない球団の方針を、AI が決め直す。"""
    decided = (maker or SimplePolicyMaker()).decide(league)
    for t in league.teams:
        if not policy_of(t)["manual"]:
            t.policy = {**decided[t.id], "manual": False}
