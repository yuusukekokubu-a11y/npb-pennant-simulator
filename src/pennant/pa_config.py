"""打席の計算の設定ファイル(data/plate_appearance.json)の読み込みと検証。

数値はすべて架空の設定値(実在の成績表を写したものではない。D-003 補足、D-036)。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .abilities import BATTER_ITEMS, DEFENSIVE_POSITIONS, FIELDING_ITEMS, PITCHER_ITEMS
from .config import SUPPORTED_FORMAT_VERSION, ConfigError, _Checker, _read_json

STAGE1_EVENTS = ("strikeout", "walk", "hit_by_pitch", "home_run")
PLATOON_KEYS = STAGE1_EVENTS + ("in_play_hit",)
BATTED_BALL_TYPES = ("ground", "line", "fly")
HIT_TYPES = ("single", "double", "triple")

# 「10点あたりの倍率」の許される範囲。1 より大きいと能力が高いほど起きやすく、小さいと起きにくい
RATIO_LOW, RATIO_HIGH = 0.2, 5.0


@dataclass(frozen=True)
class PlateAppearanceConfig:
    """検証済みの打席の計算の設定。中身は JSON と同じ形の辞書(data)。"""

    data: dict
    source: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def default_pa_data() -> dict:
    data, _ = _read_json(None, "plate_appearance.json")
    return copy.deepcopy(data)


def load_pa_config(path: str | Path | None = None) -> PlateAppearanceConfig:
    data, source = _read_json(path, "plate_appearance.json")
    return validate_pa_config(data, source)


def _effects(c: _Checker, data: Any, path: str, events: tuple[str, ...], items: tuple[str, ...]) -> None:
    """{結果: {能力: 10点あたりの倍率}} の形を確かめる。"""
    sec = c.section(data, path)
    if sec is None:
        return
    for event, table in sec.items():
        if event not in events:
            c.add(f"{path}.{event}", f"知らない結果です(使えるもの: {', '.join(events)})")
            continue
        t = c.section(table, f"{path}.{event}")
        for item, ratio in (t or {}).items():
            if item not in items:
                c.add(f"{path}.{event}.{item}", f"この役割では使えない能力です(使えるもの: {', '.join(items)})")
                continue
            c.number(ratio, f"{path}.{event}.{item}", RATIO_LOW, RATIO_HIGH)


def _shares(c: _Checker, data: Any, path: str, keys: tuple[str, ...], require_all: bool) -> dict[str, float]:
    sec = c.section(data, path)
    result: dict[str, float] = {}
    if sec is None:
        return result
    for key, value in sec.items():
        if key not in keys:
            c.add(f"{path}.{key}", f"知らない区分です(使えるもの: {', '.join(keys)})")
            continue
        v = c.number(value, f"{path}.{key}", 0, 1)
        if v is not None:
            result[key] = v
    if require_all:
        for key in keys:
            if key not in sec:
                c.add(f"{path}.{key}", "値がありません(必須項目です)")
    c.shares(result, path)
    return result


def validate_pa_config(data: Any, source: str = "(辞書)") -> PlateAppearanceConfig:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)

    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")

    # 第1段階:三振・四球・死球・本塁打(残りがインプレー)
    s1 = c.section(c.get(root, "stage1", ""), "stage1")
    rates = c.section(c.get(s1, "league_rates", "stage1"), "stage1.league_rates")
    total = 0.0
    if rates is not None:
        for key in rates:
            if key not in STAGE1_EVENTS:
                c.add(f"stage1.league_rates.{key}", f"知らない結果です(使えるもの: {', '.join(STAGE1_EVENTS)})")
        for event in STAGE1_EVENTS:
            v = c.number(c.get(rates, event, "stage1.league_rates"), f"stage1.league_rates.{event}", 0.0001, 0.6)
            total += v or 0.0
        if total >= 0.8:
            c.add("stage1.league_rates", f"合計が {total:.3f} です。残り(インプレーの率)が 0.2 未満になるので、小さくしてください")
    _effects(c, c.get(s1, "batter_effects", "stage1"), "stage1.batter_effects", STAGE1_EVENTS, BATTER_ITEMS)
    _effects(c, c.get(s1, "pitcher_effects", "stage1"), "stage1.pitcher_effects", STAGE1_EVENTS, PITCHER_ITEMS)

    # 左右の相性(D-037)。1.0 で効果なし
    platoon = c.section(c.get(root, "platoon", ""), "platoon")
    for side in ("same_side", "opposite_side"):
        sec = c.section(c.get(platoon, side, "platoon"), f"platoon.{side}")
        for key, value in (sec or {}).items():
            if key not in PLATOON_KEYS:
                c.add(f"platoon.{side}.{key}", f"知らない結果です(使えるもの: {', '.join(PLATOON_KEYS)})")
                continue
            c.number(value, f"platoon.{side}.{key}", RATIO_LOW, RATIO_HIGH)

    # 打球の種類(D-035)
    bb = c.section(c.get(root, "batted_ball", ""), "batted_ball")
    _shares(c, c.get(bb, "league_shares", "batted_ball"), "batted_ball.league_shares", BATTED_BALL_TYPES, True)
    _effects(c, c.get(bb, "batter_effects", "batted_ball"), "batted_ball.batter_effects", BATTED_BALL_TYPES, BATTER_ITEMS)
    _effects(c, c.get(bb, "pitcher_effects", "batted_ball"), "batted_ball.pitcher_effects", BATTED_BALL_TYPES, PITCHER_ITEMS)

    # 担当ポジションの割合表(D-038)
    fs = c.section(c.get(root, "fielder_shares", ""), "fielder_shares")
    for t in BATTED_BALL_TYPES:
        _shares(c, c.get(fs, t, "fielder_shares"), f"fielder_shares.{t}", DEFENSIVE_POSITIONS, False)

    # インプレーの結果(打球の種類ごと)
    ip = c.section(c.get(root, "in_play", ""), "in_play")
    for t in BATTED_BALL_TYPES:
        path = f"in_play.{t}"
        sec = c.section(c.get(ip, t, "in_play"), path)
        if sec is None:
            continue
        hit = c.number(c.get(sec, "hit_rate", path), f"{path}.hit_rate", 0.001, 0.99)
        unf = c.number(c.get(sec, "unfieldable_share", path), f"{path}.unfieldable_share", 0, 0.99)
        err = c.number(c.get(sec, "error_rate", path), f"{path}.error_rate", 0, 0.2)
        if hit is not None and unf is not None:
            if unf >= hit:
                c.add(path, f"unfieldable_share({unf})は hit_rate({hit})より小さくしてください(野手が処理できない打球は安打の一部のため)")
            elif err is not None:
                fieldable_hit = (hit - unf) / (1 - unf)
                if fieldable_hit + err >= 1:
                    c.add(path, "野手が処理できる打球の、安打率と失策率の合計が 1 以上になっています")
        he = c.section(c.get(sec, "hit_effects", path), f"{path}.hit_effects")
        if he is not None:
            _effects(c, {k: v for k, v in he.items() if k == "batter"}, f"{path}.hit_effects", ("batter",), BATTER_ITEMS)
            _effects(c, {k: v for k, v in he.items() if k == "pitcher"}, f"{path}.hit_effects", ("pitcher",), PITCHER_ITEMS)
            _effects(c, {k: v for k, v in he.items() if k == "fielder"}, f"{path}.hit_effects", ("fielder",), FIELDING_ITEMS)
            for k in he:
                if k not in ("batter", "pitcher", "fielder"):
                    c.add(f"{path}.hit_effects.{k}", "batter / pitcher / fielder のどれかにしてください")
        _effects(c, c.get(sec, "error_effects", path), f"{path}.error_effects", ("fielder",), FIELDING_ITEMS)
        xb = c.section(c.get(sec, "extra_base", path), f"{path}.extra_base")
        _shares(c, c.get(xb, "shares", f"{path}.extra_base"), f"{path}.extra_base.shares", HIT_TYPES, True)
        _effects(c, c.get(xb, "effects", f"{path}.extra_base"), f"{path}.extra_base.effects", ("double", "triple"), BATTER_ITEMS)

    # 投手の守備(能力を持たないので固定値)
    pf = c.section(c.get(root, "pitcher_fielding", ""), "pitcher_fielding")
    for item in FIELDING_ITEMS:
        c.number(c.get(pf, item, "pitcher_fielding"), f"pitcher_fielding.{item}", 0, 100)

    # 好調・不調の影響の大きさ(D-033)。0 で影響なし
    c.number(c.get(root, "form_weight", ""), "form_weight", 0, 3)

    if c.problems:
        raise ConfigError(source, c.problems)
    return PlateAppearanceConfig(data=copy.deepcopy(root), source=source)
