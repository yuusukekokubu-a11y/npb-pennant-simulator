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
    c.integer(c.get(sch, "games_per_opponent", "schedule"), "schedule.games_per_opponent", 1, 100)
    st = c.section(c.get(root, "standings", ""), "standings")
    shared = c.get(st, "allow_shared_rank", "standings")
    if shared is not None and not isinstance(shared, bool):
        c.add("standings.allow_shared_rank", "true か false で書いてください")
    if c.problems:
        raise ConfigError(source, c.problems)
    return SeasonConfig(data=copy.deepcopy(root), source=source)
