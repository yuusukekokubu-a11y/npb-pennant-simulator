"""シーズンの設定ファイル(data/season.json)の読み込みと検証(実装④。D-066、D-087)。

数値は調整前提の仮置き値(DESIGN.md 9章)。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SUPPORTED_FORMAT_VERSION, ConfigError, _Checker, _read_json


@dataclass(frozen=True)
class SeasonConfig:
    data: dict
    source: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def default_season_data() -> dict:
    data, _ = _read_json(None, "season.json")
    return copy.deepcopy(data)


def load_season_config(path: str | Path | None = None) -> SeasonConfig:
    data, source = _read_json(path, "season.json")
    return validate_season_config(data, source)


def validate_season_config(data: Any, source: str = "(辞書)") -> SeasonConfig:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    sch = c.section(c.get(root, "schedule", ""), "schedule")
    gpo = c.integer(c.get(sch, "games_per_opponent", "schedule"), "schedule.games_per_opponent", 1, 100)
    if isinstance(sch, dict) and "series" in sch:  # 連戦の日程(D-435)。書いていなければ今までのラウンド制
        series = sch["series"]
        if not isinstance(series, list) or not series or not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= 10 for x in series):
            c.add("schedule.series", "1〜10 の整数の並びで書いてください")
        elif gpo is not None and sum(series) != gpo:
            c.add("schedule.series", f"合計が games_per_opponent({gpo})と同じになるようにしてください(今は {sum(series)})")
        il = c.section(c.get(sch, "interleague", "schedule"), "schedule.interleague")
        c.integer(c.get(il, "games", "schedule.interleague"), "schedule.interleague.games", 0, 10)
        c.integer(c.get(il, "after_games", "schedule.interleague"), "schedule.interleague.after_games", 0, 200)
        c.integer(c.get(sch, "max_consecutive_days", "schedule"), "schedule.max_consecutive_days", 3, 30)
        al = sch.get("allstar")
        if al is not None:
            al = c.section(al, "schedule.allstar")
            c.integer(c.get(al, "after_games", "schedule.allstar"), "schedule.allstar.after_games", 0, 200)
            c.integer(c.get(al, "games", "schedule.allstar"), "schedule.allstar.games", 1, 5)
    ps = root.get("postseason")
    if ps is not None:  # ポストシーズン(D-439)。書いていなければ行わない(版 27 の続き)
        ps = c.section(ps, "postseason")
        for key in ("first_stage", "final_stage", "japan_series"):
            sec = c.section(c.get(ps, key, "postseason"), f"postseason.{key}")
            games = c.integer(c.get(sec, "games", f"postseason.{key}"), f"postseason.{key}.games", 1, 15)
            wins = c.integer(c.get(sec, "wins", f"postseason.{key}"), f"postseason.{key}.wins", 1, 15)
            adv = c.integer(sec.get("advantage", 0) if sec else 0, f"postseason.{key}.advantage", 0, 5)
            if games is not None and wins is not None and adv is not None and not (wins - adv <= games):
                c.add(f"postseason.{key}", "勝ち抜けに必要な勝利数(アドバンテージを除く)は、試合数以下にしてください")
            travel = sec.get("travel_after", []) if sec else []
            if not (isinstance(travel, list) and all(isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in travel)):
                c.add(f"postseason.{key}.travel_after", "1 以上の整数の並びで書いてください")
        rest = c.get(ps, "rest_days", "postseason")
        if rest is not None and not (isinstance(rest, list) and len(rest) == 3 and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 10 for x in rest)):
            c.add("postseason.rest_days", "0〜10 の整数を 3 つ(シーズンの後・CS の間・日本シリーズの前)で書いてください")
    st = c.section(c.get(root, "standings", ""), "standings")
    shared = c.get(st, "allow_shared_rank", "standings")
    if shared is not None and not isinstance(shared, bool):
        c.add("standings.allow_shared_rank", "true か false で書いてください")
    if c.problems:
        raise ConfigError(source, c.problems)
    return SeasonConfig(data=copy.deepcopy(root), source=source)
