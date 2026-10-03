"""設定ファイルの読み込みと検証(バリデーション)。

設定ファイルは JSON 形式(セーブデータと同じ形式。D-006)。
既定の設定は src/pennant/data/ にあり、別のファイルを渡して差し替えることもできる。
値が欠けている・範囲外・合計が合わない、などの場合は ConfigError を出し、
どこが・なぜおかしいかを日本語で示す。
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import read_package_text, read_text_file
from .abilities import (
    ALL_ITEMS,
    BATTER_ITEMS,
    FIELDER_POSITIONS,
    PITCHER_ITEMS,
    PITCHER_POSITIONS,
    STYLE_ITEMS,
)

SUPPORTED_FORMAT_VERSION = 1
SHARE_SUM_TOLERANCE = 1e-6


class ConfigError(ValueError):
    """設定ファイルの内容に問題があるときのエラー。"""

    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = problems
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"設定ファイル「{source}」に問題があります({len(problems)}件):\n{lines}")


class _Checker:
    """問題をまとめて集めるための小さな道具。最初の1件で止めず、全部を一度に知らせる。"""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.problems.append(f"{path}: {message}")

    def section(self, data: Any, path: str) -> dict | None:
        if not isinstance(data, dict):
            self.add(path, "項目のまとまり({ } で囲んだもの)が必要です")
            return None
        return data

    def get(self, data: dict | None, key: str, path: str) -> Any:
        if data is None:
            return None
        if key not in data:
            self.add(f"{path}.{key}" if path else key, "値がありません(必須項目です)")
            return None
        return data[key]

    def number(self, value: Any, path: str, low: float | None = None, high: float | None = None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self.add(path, f"数値が必要です(値: {value!r})")
            return None
        if low is not None and value < low:
            self.add(path, f"{low} 以上である必要があります(値: {value})")
            return None
        if high is not None and value > high:
            self.add(path, f"{high} 以下である必要があります(値: {value})")
            return None
        return float(value)

    def integer(self, value: Any, path: str, low: int | None = None, high: int | None = None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self.add(path, f"整数が必要です(値: {value!r})")
            return None
        if low is not None and value < low:
            self.add(path, f"{low} 以上である必要があります(値: {value})")
            return None
        if high is not None and value > high:
            self.add(path, f"{high} 以下である必要があります(値: {value})")
            return None
        return value

    def shares(self, values: dict[str, float], path: str) -> None:
        total = sum(values.values())
        if values and abs(total - 1.0) > SHARE_SUM_TOLERANCE:
            self.add(path, f"割合(share)の合計が 1 になっていません(合計: {total:.4f})")


@dataclass(frozen=True)
class GenerationConfig:
    """検証済みの生成設定。中身は JSON と同じ形の辞書(data)で持つ。"""

    data: dict
    source: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


@dataclass(frozen=True)
class NameParts:
    """名前の部品(D-041)。"""

    surnames: tuple[str, ...]
    given_names: tuple[str, ...]
    places: tuple[str, ...]
    team_nicknames: tuple[str, ...]
    stadium_suffixes: tuple[str, ...]
    league_names: tuple[str, ...]
    source: str


def _read_json(path: str | Path | None, default_name: str) -> tuple[Any, str]:
    if path is None:
        text = read_package_text("data", default_name)
        source = f"(既定) data/{default_name}"
    else:
        source = str(path)
        try:
            text = read_text_file(path)
        except OSError as exc:
            raise ConfigError(source, [f"ファイルを読めません({exc})"]) from exc
    try:
        return json.loads(text), source
    except json.JSONDecodeError as exc:
        raise ConfigError(source, [f"JSON として読めません({exc.lineno} 行目 {exc.colno} 文字目: {exc.msg})"]) from exc


def default_generation_data() -> dict:
    """既定の生成設定を、検証前の辞書として返す(テストで一部を書き換えて使う)。"""
    data, _ = _read_json(None, "generation.json")
    return copy.deepcopy(data)


def load_generation_config(path: str | Path | None = None) -> GenerationConfig:
    """生成設定を読み込んで検証する。path を省略すると既定の設定を使う。"""
    data, source = _read_json(path, "generation.json")
    return validate_generation_config(data, source)


def load_name_parts(path: str | Path | None = None) -> NameParts:
    """名前の部品を読み込んで検証する。path を省略すると既定の部品を使う。"""
    data, source = _read_json(path, "names.json")
    return validate_name_parts(data, source)


def _check_corrections(c: _Checker, corrections: Any, items: tuple[str, ...], path: str) -> dict[str, float]:
    result: dict[str, float] = {}
    corr = c.section(corrections, path)
    if corr is None:
        return result
    for key in corr:
        if key not in items:
            c.add(f"{path}.{key}", f"この役割にない能力項目です(使える項目: {', '.join(items)})")
    for item in items:
        value = c.number(c.get(corr, item, path), f"{path}.{item}", -40, 40)
        if value is not None:
            result[item] = value
    return result


def _check_balance(
    c: _Checker,
    weighted: list[tuple[float, dict[str, float]]],
    items: tuple[str, ...],
    tolerance: float,
    path: str,
) -> None:
    """型の補正を出現割合で重み付けして合計したとき、0 付近に収まるか(D-028)。"""
    for item in items:
        if not all(item in corr for _, corr in weighted):
            continue
        total = sum(share * corr[item] for share, corr in weighted)
        if abs(total) > tolerance:
            c.add(
                path,
                f"「{item}」の補正を割合で重み付けした合計が {total:+.2f} です。"
                f"±{tolerance} 以内にしないと、リーグ平均が基準値から動いてしまいます",
            )


def validate_generation_config(data: Any, source: str = "(辞書)") -> GenerationConfig:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)

    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")

    league = c.section(c.get(root, "league", ""), "league")
    c.integer(c.get(league, "num_leagues", "league"), "league.num_leagues", 1, 4)
    c.integer(c.get(league, "teams_per_league", "league"), "league.teams_per_league", 2, 16)

    # 球団の選手構成(D-029)
    roster = c.section(c.get(root, "roster", ""), "roster")
    role_counts: dict[str, int] = {}
    for group, positions in (("pitchers", PITCHER_POSITIONS), ("fielders", FIELDER_POSITIONS)):
        sec = c.section(c.get(roster, group, "roster"), f"roster.{group}")
        if sec is None:
            continue
        for key in sec:
            if key not in positions:
                c.add(f"roster.{group}.{key}", f"知らないポジションです(使えるもの: {', '.join(positions)})")
        for pos in positions:
            n = c.integer(c.get(sec, pos, f"roster.{group}"), f"roster.{group}.{pos}", 1, 70)
            if n is not None and group == "pitchers":
                role_counts[pos] = n

    # 潜在能力の基準(D-024、D-027、D-030)
    pot = c.section(c.get(root, "potential", ""), "potential")
    c.number(c.get(pot, "batter_base_mean", "potential"), "potential.batter_base_mean", 0, 100)
    c.number(c.get(pot, "pitcher_base_mean", "potential"), "potential.pitcher_base_mean", 0, 100)
    c.number(c.get(pot, "style_mean", "potential"), "potential.style_mean", 0, 100)
    c.number(c.get(pot, "noise_sd", "potential"), "potential.noise_sd", 0, 30)
    tolerance = c.number(c.get(pot, "balance_tolerance", "potential"), "potential.balance_tolerance", 0, 5)
    # 省略可(第3弾①より前の設定・セーブデータにはない)。省略時は 0.005
    share_tolerance = c.number(pot.get("share_balance_tolerance", 0.005), "potential.share_balance_tolerance", 0, 0.1) if pot is not None else None

    # 打者の型(D-028)
    batter_types = c.section(c.get(root, "batter_archetypes", ""), "batter_archetypes")
    weighted_batter: list[tuple[float, dict[str, float]]] = []
    shares: dict[str, float] = {}
    for key, entry in (batter_types or {}).items():
        path = f"batter_archetypes.{key}"
        e = c.section(entry, path)
        share = c.number(c.get(e, "share", path), f"{path}.share", 0, 1)
        corr = _check_corrections(c, c.get(e, "corrections", path), BATTER_ITEMS, f"{path}.corrections")
        if share is not None:
            shares[key] = share
            weighted_batter.append((share, corr))
    if batter_types is not None and not batter_types:
        c.add("batter_archetypes", "型が1つもありません")
    c.shares(shares, "batter_archetypes")
    if tolerance is not None:
        _check_balance(c, weighted_batter, BATTER_ITEMS, tolerance, "batter_archetypes")

    # ポジション別の型の足す量(第3弾①。D-157)
    shifts = c.section(root["position_archetype_shifts"], "position_archetype_shifts") if "position_archetype_shifts" in root else None  # 省略可(なければ全体の割合)
    fielder_counts = {pos: (roster or {}).get("fielders", {}).get(pos) for pos in FIELDER_POSITIONS}
    if shifts is not None:
        for pos in shifts:
            if pos not in FIELDER_POSITIONS:
                c.add(f"position_archetype_shifts.{pos}", f"知らないポジションです(使えるもの: {', '.join(FIELDER_POSITIONS)})")
        for pos in FIELDER_POSITIONS:
            path = f"position_archetype_shifts.{pos}"
            sec = c.section(c.get(shifts, pos, "position_archetype_shifts"), path)
            if sec is None:
                continue
            for key in sec:
                if key not in shares:
                    c.add(f"{path}.{key}", f"知らない型です(使えるもの: {', '.join(shares)})")
            total = 0.0
            for key, share in shares.items():
                v = c.number(c.get(sec, key, path), f"{path}.{key}", -1, 1)
                if v is None:
                    continue
                total += v
                if not 0 <= share + v <= 1:
                    c.add(f"{path}.{key}", f"全体の割合 {share} に足すと 0〜1 の外に出ます(足す量: {v})")
            if abs(total) > 1e-6:
                c.add(path, f"足す量の合計が 0 になっていません(合計: {total:+.4f})")
        if share_tolerance is not None and all(isinstance(n, int) for n in fielder_counts.values()):
            total_n = sum(fielder_counts.values())
            for key in shares:
                weighted = sum(fielder_counts[pos] * float((shifts.get(pos) or {}).get(key, 0) or 0) for pos in FIELDER_POSITIONS) / total_n
                if abs(weighted) > share_tolerance:
                    c.add(
                        "position_archetype_shifts",
                        f"「{key}」の足す量を球団の人数で重みづけした合計が {weighted:+.4f} です。"
                        f"±{share_tolerance} 以内にしないと、全体の割合(とリーグ平均)が動いてしまいます",
                    )

    # 投手の役割(割合は球団構成の先発・救援の人数から決まる)
    roles = c.section(c.get(root, "pitcher_roles", ""), "pitcher_roles")
    weighted_roles: list[tuple[float, dict[str, float]]] = []
    if roles is not None:
        for key in roles:
            if key not in PITCHER_POSITIONS:
                c.add(f"pitcher_roles.{key}", f"知らない役割です(使えるもの: {', '.join(PITCHER_POSITIONS)})")
        total_pitchers = sum(role_counts.values())
        for role in PITCHER_POSITIONS:
            path = f"pitcher_roles.{role}"
            e = c.section(c.get(roles, role, "pitcher_roles"), path)
            corr = _check_corrections(c, c.get(e, "corrections", path), PITCHER_ITEMS, f"{path}.corrections")
            if total_pitchers and role in role_counts:
                weighted_roles.append((role_counts[role] / total_pitchers, corr))
        if tolerance is not None and len(weighted_roles) == len(PITCHER_POSITIONS):
            _check_balance(c, weighted_roles, PITCHER_ITEMS, tolerance, "pitcher_roles(割合は roster.pitchers の人数から計算)")

    # 投手の球質(D-028)
    qualities = c.section(c.get(root, "pitcher_qualities", ""), "pitcher_qualities")
    weighted_q: list[tuple[float, dict[str, float]]] = []
    shares = {}
    for key, entry in (qualities or {}).items():
        path = f"pitcher_qualities.{key}"
        e = c.section(entry, path)
        share = c.number(c.get(e, "share", path), f"{path}.share", 0, 1)
        corr = _check_corrections(c, c.get(e, "corrections", path), PITCHER_ITEMS, f"{path}.corrections")
        if share is not None:
            shares[key] = share
            weighted_q.append((share, corr))
    if qualities is not None and not qualities:
        c.add("pitcher_qualities", "球質が1つもありません")
    c.shares(shares, "pitcher_qualities")
    if tolerance is not None:
        _check_balance(c, weighted_q, PITCHER_ITEMS, tolerance, "pitcher_qualities")

    # 年齢カーブ(D-030、D-031、D-033)
    aging = c.section(c.get(root, "aging", ""), "aging")
    groups = c.section(c.get(aging, "groups", "aging"), "aging.groups")
    seen: dict[str, str] = {}
    for gkey, entry in (groups or {}).items():
        path = f"aging.groups.{gkey}"
        g = c.section(entry, path)
        items = c.get(g, "items", path)
        if items is not None:
            if not isinstance(items, list) or not items:
                c.add(f"{path}.items", "能力項目の名前を並べたリストが必要です")
                items = []
            for item in items:
                if item not in ALL_ITEMS:
                    c.add(f"{path}.items", f"知らない能力項目です: {item!r}")
                elif item in seen:
                    c.add(f"{path}.items", f"「{item}」が「{seen[item]}」グループにも入っています(1項目は1グループだけ)")
                else:
                    seen[item] = gkey
        is_fixed = items is not None and all(i in STYLE_ITEMS for i in items) and gkey == "fixed"
        if g is not None and not is_fixed:
            c.number(c.get(g, "peak_age", path), f"{path}.peak_age", 18, 45)
            c.number(c.get(g, "gap_per_year_before_peak", path), f"{path}.gap_per_year_before_peak", 0, 10)
            c.number(c.get(g, "decline_per_year", path), f"{path}.decline_per_year", 0, 10)
            c.number(c.get(g, "decline_accel", path), f"{path}.decline_accel", 0, 2)
    if groups is not None:
        if "fixed" not in groups:
            c.add("aging.groups", "年齢で変わらない「fixed」グループが必要です")
        for item in ALL_ITEMS:
            if item not in seen:
                c.add("aging.groups", f"能力項目「{item}」がどのグループにも入っていません")
        for item in STYLE_ITEMS:
            if seen.get(item) not in (None, "fixed"):
                c.add("aging.groups", f"「型」の項目「{item}」は fixed グループに入れてください(D-031)")
    growth = c.section(c.get(aging, "growth_types", "aging"), "aging.growth_types")
    shares = {}
    for key, entry in (growth or {}).items():
        path = f"aging.growth_types.{key}"
        e = c.section(entry, path)
        share = c.number(c.get(e, "share", path), f"{path}.share", 0, 1)
        c.number(c.get(e, "peak_shift", path), f"{path}.peak_shift", -10, 10)
        if share is not None:
            shares[key] = share
    if growth is not None and not growth:
        c.add("aging.growth_types", "成長タイプが1つもありません")
    c.shares(shares, "aging.growth_types")

    # 能力の揺れ・好調不調(D-033)
    var = c.section(c.get(root, "variation", ""), "variation")
    c.number(c.get(var, "drift_start_age", "variation"), "variation.drift_start_age", 15, 30)
    c.number(c.get(var, "ability_drift_sd_per_year", "variation"), "variation.ability_drift_sd_per_year", 0, 10)
    c.number(c.get(var, "form_sd", "variation"), "variation.form_sd", 0, 20)

    # 初期選手の年齢(D-039)
    ages = c.section(c.get(root, "initial_ages", ""), "initial_ages")
    a_min = c.integer(c.get(ages, "min", "initial_ages"), "initial_ages.min", 15, 50)
    a_max = c.integer(c.get(ages, "max", "initial_ages"), "initial_ages.max", 15, 50)
    a_mean = c.number(c.get(ages, "mean", "initial_ages"), "initial_ages.mean", 15, 50)
    c.number(c.get(ages, "sd", "initial_ages"), "initial_ages.sd", 0, 15)
    if a_min is not None and a_max is not None:
        if a_min >= a_max:
            c.add("initial_ages", f"min({a_min})は max({a_max})より小さくしてください")
        elif a_mean is not None and not (a_min <= a_mean <= a_max):
            c.add("initial_ages.mean", f"min〜max({a_min}〜{a_max})の範囲に入れてください(値: {a_mean})")

    # 生き残りバイアス(D-034)
    bias = c.section(c.get(root, "survival_bias", ""), "survival_bias")
    c.number(c.get(bias, "start_age", "survival_bias"), "survival_bias.start_age", 15, 50)
    c.number(c.get(bias, "per_year", "survival_bias"), "survival_bias.per_year", 0, 5)

    # 新人(ドラフト)の入団年齢(D-039)
    draft = c.section(c.get(root, "draft", ""), "draft")
    origins = c.section(c.get(draft, "origins", "draft"), "draft.origins")
    shares = {}
    for key, entry in (origins or {}).items():
        path = f"draft.origins.{key}"
        e = c.section(entry, path)
        share = c.number(c.get(e, "share", path), f"{path}.share", 0, 1)
        lo = c.integer(c.get(e, "age_min", path), f"{path}.age_min", 15, 40)
        hi = c.integer(c.get(e, "age_max", path), f"{path}.age_max", 15, 40)
        mean = c.number(c.get(e, "age_mean", path), f"{path}.age_mean", 15, 40)
        c.number(c.get(e, "age_sd", path), f"{path}.age_sd", 0, 10)
        if lo is not None and hi is not None:
            if lo > hi:
                c.add(path, f"age_min({lo})は age_max({hi})以下にしてください")
            elif mean is not None and not (lo <= mean <= hi):
                c.add(f"{path}.age_mean", f"age_min〜age_max({lo}〜{hi})の範囲に入れてください(値: {mean})")
        if share is not None:
            shares[key] = share
    if origins is not None and not origins:
        c.add("draft.origins", "出身区分が1つもありません")
    c.shares(shares, "draft.origins")

    # 打席の左右(打者のみ)・投球の左右(投手のみ)(D-037、D-046、D-048)
    hand = c.section(c.get(root, "handedness", ""), "handedness")
    bats = c.section(c.get(hand, "bats", "handedness"), "handedness.bats")
    shares = {}
    if bats is not None:
        for key in bats:
            if key not in ("R", "L", "S"):
                c.add(f"handedness.bats.{key}", "R(右)・L(左)・S(両打ち)のどれかにしてください")
        for key in ("R", "L", "S"):
            v = c.number(c.get(bats, key, "handedness.bats"), f"handedness.bats.{key}", 0, 1)
            if v is not None:
                shares[key] = v
        c.shares(shares, "handedness.bats")
    c.number(c.get(hand, "pitcher_throws_left", "handedness"), "handedness.pitcher_throws_left", 0, 1)

    # 一軍相当の人数(確認用。一軍登録人数は未確認の仮置き)
    if "parks" in root:  # 球場の倍率の範囲(第2弾②a。D-136)。省略時は倍率なし(すべて 1.0)
        parks = c.section(root["parks"], "parks")
        for key in ("home_run_range", "babip_range"):
            rng_ = c.get(parks, key, "parks")
            if rng_ is None:
                continue
            if not isinstance(rng_, list) or len(rng_) != 2:
                c.add(f"parks.{key}", "[下限, 上限] の2つの数で書いてください")
                continue
            low = c.number(rng_[0], f"parks.{key}[0]", 0.5, 1.0)
            high = c.number(rng_[1], f"parks.{key}[1]", 1.0, 2.0)
            if low is not None and high is not None and low > high:
                c.add(f"parks.{key}", "下限が上限より大きくなっています")
        for key in parks or {}:
            if key not in ("home_run_range", "babip_range"):
                c.add(f"parks.{key}", "知らない名前です")

    first = c.section(c.get(root, "first_team", ""), "first_team")
    fp = c.integer(c.get(first, "pitchers", "first_team"), "first_team.pitchers", 1, 70)
    ff = c.integer(c.get(first, "fielders", "first_team"), "first_team.fielders", 1, 70)
    if fp is not None and role_counts and fp > sum(role_counts.values()):
        c.add("first_team.pitchers", "球団の投手の人数より多くなっています")
    fielder_total = sum(v for v in ((roster or {}).get("fielders") or {}).values() if isinstance(v, int))
    if ff is not None and fielder_total and ff > fielder_total:
        c.add("first_team.fielders", "球団の野手の人数より多くなっています")

    if c.problems:
        raise ConfigError(source, c.problems)
    return GenerationConfig(data=copy.deepcopy(root), source=source)


def validate_name_parts(data: Any, source: str = "(辞書)") -> NameParts:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r})")
    lists: dict[str, tuple[str, ...]] = {}
    for key in ("surnames", "given_names", "places", "team_nicknames", "stadium_suffixes", "league_names"):
        value = c.get(root, key, "")
        if value is None:
            continue
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
            c.add(key, "空でない文字列を並べたリストが必要です")
            continue
        dups = sorted({v for v in value if value.count(v) > 1})
        if dups:
            c.add(key, f"同じものが重複しています: {', '.join(dups)}")
        lists[key] = tuple(value)
    if c.problems:
        raise ConfigError(source, c.problems)
    return NameParts(source=source, **lists)


def archetype_shares(config: GenerationConfig, position: str | None = None) -> dict[str, float]:
    """打者の型の割合。position(守備位置)を渡すと、ポジション別の足す量(D-157)を足した割合。"""
    base = {k: float(v["share"]) for k, v in config["batter_archetypes"].items()}
    shifts = config.data.get("position_archetype_shifts", {}) if hasattr(config, "data") else {}
    if position is None or position not in shifts:
        return base
    return {k: base[k] + float(shifts[position].get(k, 0)) for k in base}

