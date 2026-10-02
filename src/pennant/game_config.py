"""1試合の進行の設定ファイル(data/game.json)の読み込みと検証。

数値はすべて調整前提の仮置き値(DESIGN.md 9章)。実在の成績表は写していない。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .abilities import BATTER_ITEMS, FIELDER_POSITIONS, FIELDING_ITEMS
from .config import SUPPORTED_FORMAT_VERSION, ConfigError, _Checker, _read_json
from .pa_config import RATIO_HIGH, RATIO_LOW

ADVANCEMENT_KEYS = (
    "single_runner_from_2b_scores",
    "single_runner_from_1b_to_3b",
    "double_runner_from_1b_scores",
    "ground_out_runner_from_3b_scores",
    "ground_out_runner_from_2b_to_3b",
    "fly_out_runner_from_3b_scores",
    "fly_out_runner_from_2b_to_3b",
    "error_extra_base",
    "double_play",
)
# 補正に使える能力:走者・打者は走塁の能力、野手は守備の能力
EFFECT_GROUPS = {"runner": ("speed", "baserunning"), "batter": ("speed", "baserunning"), "fielder": FIELDING_ITEMS}


@dataclass(frozen=True)
class GameConfig:
    data: dict
    source: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def default_game_data() -> dict:
    data, _ = _read_json(None, "game.json")
    return copy.deepcopy(data)


def load_game_config(path: str | Path | None = None) -> GameConfig:
    data, source = _read_json(path, "game.json")
    return validate_game_config(data, source)


def _weights(c: _Checker, data: Any, path: str) -> None:
    sec = c.section(data, path)
    if sec is None:
        return
    if not sec:
        c.add(path, "能力と重みを1つ以上書いてください")
    for item, w in sec.items():
        if item not in BATTER_ITEMS:
            c.add(f"{path}.{item}", f"打者の能力ではありません(使えるもの: {', '.join(BATTER_ITEMS)})")
            continue
        c.number(w, f"{path}.{item}", 0, 10)


def validate_game_config(data: Any, source: str = "(辞書)") -> GameConfig:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)

    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")

    # 試合の形式(D-062)
    rules = c.section(c.get(root, "rules", ""), "rules")
    innings = c.integer(c.get(rules, "innings", "rules"), "rules.innings", 1, 15)
    max_inn = c.integer(c.get(rules, "max_innings", "rules"), "rules.max_innings", 1, 30)
    cap = c.integer(c.get(rules, "max_innings_without_tie", "rules"), "rules.max_innings_without_tie", 1, 60)
    tie = c.get(rules, "allow_tie", "rules")
    if tie is not None and not isinstance(tie, bool):
        c.add("rules.allow_tie", f"true か false にしてください(値: {tie!r})")
    if innings is not None and max_inn is not None and max_inn < innings:
        c.add("rules.max_innings", f"延長の上限({max_inn})は、通常の回数({innings})以上にしてください")
    if max_inn is not None and cap is not None and cap < max_inn:
        c.add("rules.max_innings_without_tie", "引き分けなしのときの安全上の上限は、max_innings 以上にしてください")

    # 一軍の構成(D-029 補足:一軍登録枠29人)
    ar = c.section(c.get(root, "active_roster", ""), "active_roster")
    starters = c.integer(c.get(ar, "starters", "active_roster"), "active_roster.starters", 1, 14)
    relievers = c.integer(c.get(ar, "relievers", "active_roster"), "active_roster.relievers", 1, 20)
    catchers = c.integer(c.get(ar, "catchers", "active_roster"), "active_roster.catchers", 1, 4)
    fielders = c.integer(c.get(ar, "fielders", "active_roster"), "active_roster.fielders", 9, 20)
    if catchers is not None and fielders is not None and fielders < catchers + len(FIELDER_POSITIONS):
        c.add("active_roster.fielders", "捕手の人数と、捕手以外の7ポジションを1人ずつ入れられる人数にしてください")

    # 打順の重み
    lineup = c.section(c.get(root, "lineup", ""), "lineup")
    for key in ("on_base", "slugging", "offense"):
        _weights(c, c.get(lineup, key, "lineup"), f"lineup.{key}")

    # 休養(D-061)
    rest = c.section(c.get(root, "rest", ""), "rest")
    c.number(c.get(rest, "probability", "rest"), "rest.probability", 0, 1)
    c.number(c.get(rest, "catcher_probability", "rest"), "rest.catcher_probability", 0, 1)
    c.number(c.get(rest, "near_position_penalty", "rest"), "rest.near_position_penalty", 0, 40)
    c.number(c.get(rest, "far_position_penalty", "rest"), "rest.far_position_penalty", 0, 40)
    near = c.section(c.get(rest, "near_positions", "rest"), "rest.near_positions")
    if near is not None:
        for pos in FIELDER_POSITIONS:
            lst = c.get(near, pos, "rest.near_positions")
            if lst is None:
                continue
            if not isinstance(lst, list) or any(p not in FIELDER_POSITIONS or p == pos for p in lst):
                c.add(f"rest.near_positions.{pos}", f"自分以外の野手のポジション({', '.join(FIELDER_POSITIONS)})のリストにしてください")

    # 投手の起用と疲労(D-060)
    p = c.section(c.get(root, "pitching", ""), "pitching")
    for key, lo, hi in (
        ("starter_bf_base", 3, 60),
        ("starter_bf_per_10_stamina", 0, 20),
        ("starter_bf_fatigue_penalty", 0, 10),
        ("starter_bf_min", 1, 40),
        ("starter_run_limit", 1, 30),
        ("fatigue_per_batter", 0, 10),
        ("recovery_per_day", 0, 100),
        ("recovery_ratio_per_10", RATIO_LOW, RATIO_HIGH),
        ("max_fatigue_to_start", 0, 200),
        ("max_fatigue_to_relieve", 0, 200),
    ):
        c.number(c.get(p, key, "pitching"), f"pitching.{key}", lo, hi)
    counts = {}
    for key in ("closer_count", "setup_count", "mopup_count"):
        counts[key] = c.integer(c.get(p, key, "pitching"), f"pitching.{key}", 0, 10)
    if relievers is not None and all(v is not None for v in counts.values()) and sum(counts.values()) > relievers:
        c.add("pitching", f"抑え・中継ぎ(勝ち)・敗戦処理の人数の合計が、救援の人数({relievers})を超えています")
    c.integer(c.get(p, "close_game_max_lead", "pitching"), "pitching.close_game_max_lead", 0, 10)
    c.integer(c.get(p, "setup_from_inning", "pitching"), "pitching.setup_from_inning", 1, 15)
    c.integer(c.get(p, "mopup_deficit", "pitching"), "pitching.mopup_deficit", 1, 30)

    # 走者の進塁(D-059)
    adv = c.section(c.get(root, "advancement", ""), "advancement")
    if adv is not None:
        for key in adv:
            if key not in ADVANCEMENT_KEYS:
                c.add(f"advancement.{key}", f"知らない項目です(使えるもの: {', '.join(ADVANCEMENT_KEYS)})")
        for key in ADVANCEMENT_KEYS:
            path = f"advancement.{key}"
            e = c.section(c.get(adv, key, "advancement"), path)
            if e is None:
                continue
            c.number(c.get(e, "base", path), f"{path}.base", 0.001, 0.999)
            for group, table in e.items():
                if group == "base":
                    continue
                if group not in EFFECT_GROUPS:
                    c.add(f"{path}.{group}", "runner / batter / fielder のどれかにしてください")
                    continue
                t = c.section(table, f"{path}.{group}")
                for item, ratio in (t or {}).items():
                    if item not in EFFECT_GROUPS[group]:
                        c.add(f"{path}.{group}.{item}", f"使えない能力です(使えるもの: {', '.join(EFFECT_GROUPS[group])})")
                        continue
                    c.number(ratio, f"{path}.{group}.{item}", RATIO_LOW, RATIO_HIGH)

    if c.problems:
        raise ConfigError(source, c.problems)
    return GameConfig(data=copy.deepcopy(root), source=source)
