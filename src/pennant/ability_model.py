"""見せる能力(作り直し①。D-336、D-347〜D-351)。

選手の正本は Player.abilities(見せる能力。内部と同じ目盛りの小数:50 が一軍の平均、10 点が標準偏差 1 つ分)。
試合の計算は今までの内部の能力(Player.ratings)を使うので、ここで換算して入れる(D-349)。
換算・見せ方・球種の設定は generation.json の abilities にある(セーブデータにも写しが入る)。

画面に出す値:
  1〜100 の能力 = 中心 + 1 点あたりの幅 × (値 − 50)(丸めて 1〜100)とランク(S〜G。D-347)
  球速 = km/h、弾道 = パワーと弾道の傾き(隠し情報)から 1〜4、変化球 = 球種ごとの変化量 1〜7(D-351)
"""

from __future__ import annotations

import random

from .abilities import ABILITY_LABELS, BATTER, PITCHER, abilities_for, items_for
from .config import GenerationConfig
from .models import Player


def settings(config: GenerationConfig) -> dict:
    return config["abilities"]


# ---- 内部の能力への換算(D-349) ----

def trajectory(config: GenerationConfig, player: Player) -> int:
    """弾道(1〜4)。パワーに弾道の傾き(隠し情報)を足し、区切りで決める。"""
    t = settings(config)["trajectory"]
    value = float(player.abilities.get("power", 50.0)) + float(player.hidden.trajectory_bias)
    return 1 + sum(1 for cut in t["cuts"] if value >= cut)


def engine_ratings(config: GenerationConfig, player: Player) -> dict[str, float]:
    """見せる能力から、試合の計算に使う内部の能力を作る。表にない項目は固定の値(D-348)。"""
    table = settings(config)["engine"][player.role]
    out: dict[str, float] = {}
    for item in items_for(player.role):
        rule = table[item]
        if rule == "trajectory":
            out[item] = float(settings(config)["trajectory"]["gb_fb"][trajectory(config, player) - 1])
        elif isinstance(rule, (int, float)):
            out[item] = float(rule)
        else:
            out[item] = 50.0 + sum(float(w) * (float(player.abilities[src]) - 50.0) for src, w in rule.items())
    if player.skills:  # いつも効く特殊能力を足す(D-374)
        from .skills import always_effects

        for item, v in always_effects(player).items():
            if item in out:
                out[item] += v
    return out


def refresh(config: GenerationConfig, player: Player) -> None:
    """見せる能力が変わったら呼ぶ:内部の能力を作り直す。"""
    player.ratings = engine_ratings(config, player)


# ---- 総合値 ----

def overall(player: Player) -> float:
    """総合値:見せる能力の平均(内部の目盛り)。一軍の選抜・引退・一軍相当の計算に使う。"""
    items = abilities_for(player.role)
    return sum(float(player.abilities[i]) for i in items) / len(items)


# ---- 見せ方(D-347) ----

def display(config: GenerationConfig, value: float) -> int:
    d = settings(config)["display"]
    return int(min(max(round(d["center"] + d["per_point"] * (float(value) - 50.0)), d["min"]), d["max"]))


def rank(config: GenerationConfig, shown: int) -> str:
    for letter, low in settings(config)["ranks"]:
        if shown >= low:
            return letter
    return settings(config)["ranks"][-1][0]


def velocity_kmh(config: GenerationConfig, value: float) -> int:
    v = settings(config)["velocity"]
    return int(min(max(round(v["center"] + v["per_point"] * (float(value) - 50.0)), v["min"]), v["max"]))


def pitch_amounts(config: GenerationConfig, player: Player) -> list[dict]:
    """球種ごとの変化量(1〜7)。変化球の能力から合計を決め、球種の重みで分ける(D-351)。"""
    pitches = player.pitches or []
    if not pitches:
        return []
    b = settings(config)["breaking"]
    labels = settings(config)["pitch_types"]
    n = len(pitches)
    top = int(b["max_per_pitch"])
    total = round(b["total_at_50"] + b["per_point"] * (float(player.abilities.get("breaking", 50.0)) - 50.0))
    total = min(max(total, n), top * n)
    weights = [float(p["weight"]) for p in pitches]
    raw = [total * w / sum(weights) for w in weights]
    amounts = [min(max(int(x), 1), top) for x in raw]
    # 残りを、端数の大きい順に 1 ずつ足す(上限 7)。足しすぎたら少ない順に引く(下限 1)
    order = sorted(range(n), key=lambda i: (-(raw[i] - int(raw[i])), i))
    k = 0
    while sum(amounts) < total and k < 10 * n:
        i = order[k % n]
        if amounts[i] < top:
            amounts[i] += 1
        k += 1
    k = 0
    while sum(amounts) > total and k < 10 * n:
        i = order[-1 - (k % n)]
        if amounts[i] > 1:
            amounts[i] -= 1
        k += 1
    return [{"type": p["type"], "label": labels[p["type"]]["label"], "amount": a} for p, a in zip(pitches, amounts)]


def public_abilities(config: GenerationConfig, player: Player) -> list[dict]:
    """画面に出す能力の並び(野手:弾道・ミート・パワー・走力・肩力・守備力・捕球、投手:球速・コントロール・スタミナ・変化球)。
    各項目は {key, label, value(数), text(表示の文字), rank(ランク。なければ None)}。"""
    out: list[dict] = []
    if player.role == BATTER:
        t = trajectory(config, player)
        out.append({"key": "trajectory", "label": ABILITY_LABELS["trajectory"], "value": t, "text": str(t), "rank": None})
    for item in abilities_for(player.role):
        x = float(player.abilities[item])
        if item == "velocity":
            kmh = velocity_kmh(config, x)
            out.append({"key": item, "label": ABILITY_LABELS[item], "value": kmh, "text": f"{kmh}km/h", "rank": None})
        elif item == "breaking":
            amounts = pitch_amounts(config, player)
            text = "・".join(f"{a['label']}{a['amount']}" for a in amounts) or "-"
            out.append({"key": item, "label": ABILITY_LABELS[item], "value": sum(a["amount"] for a in amounts), "text": text, "rank": None, "pitches": amounts})
        else:
            v = display(config, x)
            r = rank(config, v)
            out.append({"key": item, "label": ABILITY_LABELS[item], "value": v, "text": f"{r} {v}", "rank": r})
    return out


# ---- 生成のための部品 ----

def draw_ease(config: GenerationConfig, rng: random.Random) -> float:
    """伸びやすさ(隠し情報。D-350)。伸びる年の増え方に掛ける。"""
    g = settings(config)["growth"]
    return min(max(rng.gauss(g["ease_mean"], g["ease_sd"]), g["ease_min"]), g["ease_max"])


def draw_pitches(config: GenerationConfig, rng: random.Random) -> list[dict]:
    """球種(2〜4 個。D-351)。種類は割合で重複なく選び、それぞれに重み(変化量の配分)を付ける。"""
    b = settings(config)["breaking"]
    counts = b["count_shares"]
    n = int(rng.choices(list(counts), weights=list(counts.values()))[0])
    types = dict(settings(config)["pitch_types"])
    chosen: list[str] = []
    for _ in range(min(n, len(types))):
        keys = [k for k in types if k not in chosen]
        chosen.append(rng.choices(keys, weights=[types[k]["share"] for k in keys])[0])
    lo, hi = b["weight_range"]
    return [{"type": k, "weight": round(rng.uniform(lo, hi), 3)} for k in chosen]


__all__ = [
    "PITCHER", "engine_ratings", "refresh", "overall", "display", "rank", "velocity_kmh", "trajectory",
    "pitch_amounts", "public_abilities", "draw_ease", "draw_pitches",
]
