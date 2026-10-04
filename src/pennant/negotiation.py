"""契約更改と志望の判定(F3-2b。D-243〜D-252)。

- 志望:選手ごとに、軸(年俸・出場機会・勝利。設定ファイルで定義)の重みを持つ(隠し情報。D-245)。
  重みは軸ごとにガンマ分布から引いて合計 1 に正規化する。乱数は derive_seed(リーグのシード, "preference:<選手 ID>") で、
  選手 ID だけで決まる(付ける時期によらず同じ値。選手の生成の乱数とは別の系列なので、生成は変わらない)。
- 満足度(−1〜1。軸の種類 kind ごとの関数。軸を足すときは、設定に軸を書き、ここに種類の関数を足す):
  - salary_ratio(年俸):傾き ×(提示年俸 ÷ 算定年俸 − 1)+ 基準のずれ。お金のルール「なし」では判定に効かない(D-246)
  - depth(出場機会):1 − r ÷ k。r は自球団の同じポジションで自分より評価の高い選手の数、k はそのポジションの一軍の枠の目安
  - standing(勝利):前年のリーグ内の順位(1 位 +1 〜 最下位 −1。順位がなければ 0)
- 受ける条件:Σ 重み × 軸の強さ × 満足度 − しきい値 + 乱数 + 複数年の加点 ≧ 0(D-250)。軸の強さは設定値(勝利軸を弱めにして、球団の順位で断る人数が偏りすぎないようにする)。乱数は選手ごと・オフごとに 1 つで、同じ提示には同じ答え。
- 断った理由は、重み × 満足度が最も低い軸の文。
- 年俸の算定(contracts.py)は志望に依存しない。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, _Checker, _read_json
from .models import Player
from .season import derive_seed

AXIS_KINDS = ("salary_ratio", "depth", "standing")


@dataclass(frozen=True)
class NegotiationSettings:
    data: dict
    source: str

    @property
    def max_offers(self) -> int:
        return int(self.data["max_offers"])

    @property
    def axes(self) -> dict[str, dict]:
        return self.data["axes"]

    def label(self, axis: str) -> str:
        return str(self.axes[axis]["label"])

    def reason(self, axis: str) -> str:
        return str(self.axes[axis]["reason"])

    @property
    def threshold(self) -> float:
        return float(self.data["threshold"])

    @property
    def noise_sd(self) -> float:
        return float(self.data["noise_sd"])

    @property
    def multi_year(self) -> dict:
        return self.data["multi_year"]

    @property
    def ai(self) -> dict:
        return self.data["ai"]


def load_negotiation_settings(path: str | Path | None = None) -> NegotiationSettings:
    data, source = _read_json(path, "negotiation.json")
    return validate_negotiation_settings(data, source)


def validate_negotiation_settings(root, source: str = "negotiation.json") -> NegotiationSettings:
    c = _Checker()
    if not isinstance(root, dict):
        raise ConfigError(source, ["全体がまとまり({ })ではありません"])
    c.integer(c.get(root, "max_offers", ""), "max_offers", 1, 10)
    axes = c.section(c.get(root, "axes", ""), "axes")
    if axes is not None:
        if not axes:
            c.add("axes", "軸を 1 つ以上書いてください")
        for key, ax in axes.items():
            a = c.section(ax, f"axes.{key}")
            if a is None:
                continue
            for k in ("label", "reason"):
                v = c.get(a, k, f"axes.{key}")
                if v is not None and (not isinstance(v, str) or not v):
                    c.add(f"axes.{key}.{k}", "文字で書いてください")
            kind = c.get(a, "kind", f"axes.{key}")
            if kind is not None and kind not in AXIS_KINDS:
                c.add(f"axes.{key}.kind", f"{' / '.join(AXIS_KINDS)} のどれかにしてください(値: {kind!r})")
            c.number(c.get(a, "weight_shape", f"axes.{key}"), f"axes.{key}.weight_shape", 0.05, 100)
            c.number(c.get(a, "strength", f"axes.{key}"), f"axes.{key}.strength", 0, 10)
            nm = c.get(a, "needs_money", f"axes.{key}")
            if nm is not None and not isinstance(nm, bool):
                c.add(f"axes.{key}.needs_money", "true か false にしてください")
            if kind == "salary_ratio":
                c.number(c.get(a, "slope", f"axes.{key}"), f"axes.{key}.slope", 0, 100)
                c.number(c.get(a, "offset", f"axes.{key}"), f"axes.{key}.offset", -1, 1)
    c.number(c.get(root, "threshold", ""), "threshold", -5, 5)
    c.number(c.get(root, "noise_sd", ""), "noise_sd", 0, 5)
    my = c.section(c.get(root, "multi_year", ""), "multi_year")
    c.number(c.get(my, "per_year", "multi_year"), "multi_year.per_year", 0, 1)
    c.integer(c.get(my, "age_start", "multi_year"), "multi_year.age_start", 18, 60)
    c.number(c.get(my, "per_age_year", "multi_year"), "multi_year.per_age_year", 0, 1)
    ai = c.section(c.get(root, "ai", ""), "ai")
    amy = c.section(c.get(ai, "multi_year", "ai"), "ai.multi_year")
    c.integer(c.get(amy, "max_age", "ai.multi_year"), "ai.multi_year.max_age", 18, 60)
    table = c.get(amy, "table", "ai.multi_year")
    if table is not None and not (isinstance(table, list) and all(isinstance(x, list) and len(x) == 2 and isinstance(x[1], int) for x in table)):
        c.add("ai.multi_year.table", "[[見込みの WAR の下限, 年数], ...] の一覧にしてください")
    c.number(c.get(ai, "retry_min_expected", "ai"), "ai.retry_min_expected", -10, 20)
    max_offers = root.get("max_offers") if isinstance(root.get("max_offers"), int) else None
    for key in ("raises", "extra_years"):
        v = c.get(ai, key, "ai")
        if v is not None and not (isinstance(v, list) and v and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 for x in v)):
            c.add(f"ai.{key}", "0 以上の数の一覧(提示の回ごと)にしてください")
        elif v is not None and max_offers is not None and len(v) < max_offers:
            c.add(f"ai.{key}", f"提示の回数({max_offers})以上の長さにしてください")
    if c.problems:
        raise ConfigError(source, c.problems)
    return NegotiationSettings(copy.deepcopy(root), source)


# ---- 志望 ----

def draw_preference(player_id: str, league_seed: int, settings: NegotiationSettings) -> dict[str, float]:
    """選手の志望の重み(軸 → 重み。合計 1)。選手 ID とリーグのシードだけで決まる。"""
    rng = random.Random(derive_seed(league_seed, f"preference:{player_id}"))
    raw = {axis: rng.gammavariate(float(a["weight_shape"]), 1.0) for axis, a in settings.axes.items()}
    total = sum(raw.values()) or 1.0
    return {axis: round(v / total, 4) for axis, v in raw.items()}


def ensure_preferences(players, league_seed: int, settings: NegotiationSettings) -> None:
    """志望のない選手(生成した直後・旧版のセーブデータ)に付ける。軸が増えたときは、足りない軸を含めて引き直す。"""
    axes = set(settings.axes)
    for p in players:
        if p.preference is None or set(p.preference) != axes:
            p.preference = draw_preference(p.id, league_seed, settings)


# ---- 満足度 ----

def depth_slots(config) -> dict[str, float]:
    """ポジションごとの一軍の枠の目安 = 一軍の人数 × ポジションの人数の目安の比率(投手・野手それぞれ)。"""
    out = {}
    for group, ft_key in (("pitchers", "pitchers"), ("fielders", "fielders")):
        roster = {pos: float(n) for pos, n in config["roster"][group].items()}
        total = sum(roster.values()) or 1.0
        n = float(config["first_team"][ft_key])
        for pos, v in roster.items():
            out[pos] = max(0.5, n * v / total)
    return out


def depth_ranks(players: list[Player], estimate) -> dict[str, int]:
    """選手 ID → 同じポジションで自分より評価(estimate(選手) の値)が高い選手の数。"""
    by_pos: dict[str, list[tuple[float, str]]] = {}
    for p in players:
        by_pos.setdefault(p.position, []).append((float(estimate(p)), p.id))
    out = {}
    for pos, items in by_pos.items():
        for v, pid in items:
            out[pid] = sum(1 for w, _ in items if w > v)
    return out


def standing_ranks(teams, records: dict[str, tuple[int, int]] | None) -> dict[str, int]:
    """リーグ内の順位(勝率の高い順。同率は ID の順)。成績がなければ空。"""
    if not records:
        return {}
    out = {}
    leagues: dict[int, list] = {}
    for t in teams:
        leagues.setdefault(t.league_index, []).append(t.id)

    def pct(tid):
        w, l = records.get(tid, (0, 0))
        return w / (w + l) if w + l else 0.5

    for ids in leagues.values():
        for i, tid in enumerate(sorted(ids, key=lambda t: (-pct(t), t)), start=1):
            out[tid] = i
    return out


def satisfaction(axis: str, settings: NegotiationSettings, offer_salary: int, auto_salary: int, context: dict) -> float:
    a = settings.axes[axis]
    kind = a["kind"]
    if kind == "salary_ratio":
        ratio = float(offer_salary) / float(auto_salary) if auto_salary > 0 else 1.0
        v = float(a["slope"]) * (ratio - 1.0) + float(a["offset"])
    elif kind == "depth":
        k = float(context.get("slots") or 1.0)
        v = 1.0 - float(context.get("rank", 0)) / k
    elif kind == "standing":
        rank = context.get("standing")
        n = int(context.get("league_size") or 0)
        v = 0.0 if not rank or n <= 1 else ((n + 1) / 2.0 - float(rank)) / ((n - 1) / 2.0)
    else:  # 設定の検証で弾くので来ない
        v = 0.0
    return max(-1.0, min(1.0, v))


def axis_enabled(axis: str, settings: NegotiationSettings, money_rule: str) -> bool:
    return not (settings.axes[axis].get("needs_money") and money_rule == "none")


def multi_year_bonus(years: int, age: int, settings: NegotiationSettings) -> float:
    m = settings.multi_year
    extra = max(0, int(years) - 1)
    return extra * (float(m["per_year"]) + float(m["per_age_year"]) * max(0, int(age) - int(m["age_start"])))


def noise_for(proc_seed: int, player_id: str, settings: NegotiationSettings) -> float:
    """選手ごと・オフごとに 1 つの乱数(同じ提示には同じ答え)。"""
    return random.Random(derive_seed(proc_seed, f"negotiation:{player_id}")).gauss(0.0, settings.noise_sd)


def judge(settings: NegotiationSettings, preference: dict[str, float], years: int, salary: int, auto_salary: int, age: int, context: dict, money_rule: str, noise: float) -> dict:
    """提示への答え:{accepted, reason(軸のキー), score, values(軸 → 満足度)}。score と values は隠し情報(画面には出さない)。"""
    values = {}
    weighted = {}
    for axis in settings.axes:
        if not axis_enabled(axis, settings, money_rule):
            continue
        s = satisfaction(axis, settings, salary, auto_salary, context)
        values[axis] = s
        weighted[axis] = float(preference.get(axis, 0.0)) * float(settings.axes[axis]["strength"]) * s
    score = sum(weighted.values()) - settings.threshold + noise + multi_year_bonus(years, age, settings)
    reason = min(weighted, key=lambda k: (weighted[k], list(settings.axes).index(k))) if weighted else None
    return {"accepted": score >= 0.0, "reason": reason, "score": score, "values": values}


# ---- AI の方針(D-251) ----

def ai_years(age: int, expected: float, settings: NegotiationSettings, default_years: int) -> int:
    """AI の最初の提示の年数:見込みの WAR が高い若手は複数年、ほかは既定(1 年)。新規開始の初期選手の契約にも使う。"""
    m = settings.ai["multi_year"]
    if int(age) <= int(m["max_age"]):
        for low, n in m["table"]:
            if float(expected) >= float(low):
                return int(n)
    return int(default_years)


def ai_offer(attempt: int, auto_salary: int, base_years: int, expected: float, settings: NegotiationSettings, money_rule: str, max_years: int, rounding: int) -> tuple[int, int] | None:
    """AI の attempt 回目(0 から)の提示 (年数, 年俸)。再提示しない(自由契約にする)ときは None。"""
    if attempt >= settings.max_offers:
        return None
    if attempt > 0 and float(expected) < float(settings.ai["retry_min_expected"]):
        return None
    raise_ = float(settings.ai["raises"][attempt]) if money_rule != "none" else 1.0
    salary = int(round(auto_salary * raise_ / rounding)) * rounding if raise_ != 1.0 else int(auto_salary)
    years = min(int(max_years), int(base_years) + int(settings.ai["extra_years"][attempt]))
    return years, max(int(auto_salary), salary)
