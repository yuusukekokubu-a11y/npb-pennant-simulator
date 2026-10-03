"""球場補正の推定(第2弾②b。D-064、D-140〜D-145)。

結果から、球場ごとの「得点・本塁打・BABIP の出やすさ」を推し量る。真の倍率(parks.py)は使わない。
  - 集計:球場 × シーズンごとに、本拠地の試合とアウェイの試合(同じチームが相手の球場で行った試合)の、
    両チーム合計の 打席(PA)・本塁打(HR)・インプレーの打球(BIP)・インプレーの安打(HIT)・得点(R)。整数。
  - 生の比:本拠地の率 ÷ アウェイの率(得点は 1打席あたりの得点、本塁打は 1打席あたり、BABIP は HIT ÷ BIP)。
  - 縮める(本塁打・BABIP):複数シーズンを合算し、比重 = 本拠地の打席数 ÷(本拠地の打席数 + 定数)で 1.0 に向けて縮める。
    推定 = 1 +(生の比 − 1)× 比重。定数は設定ファイル(data/park_factors.json)。
  - そろえる:各リーグの6球場の推定の平均が 1.0 になるよう割る。
  - 得点(D-147):縮めてそろえた本塁打と BABIP の推定値を千分率の倍率にして、「1打席あたりの得点の出やすさ」に
    換算する(parks.RunConverter。真の値側と同じ式)。千分率に丸めて分数に戻し、各リーグの平均を 1.0 にそろえる。
    直接推定した得点(生の比)は検証用に残すだけで、補正には使わない。
縮める計算は分数(Fraction)。換算は小数だが、千分率の整数に丸めるので、指紋 (j) に使える。

今シーズンの指標(wRC+・OPS+)に使う球場補正は、前のシーズンまでの履歴から推定した「得点」の値。
1シーズン目(履歴なし)は 1.0。選手ごとの球場補正は、その選手が球場ごとに立った打席数で重みづけした平均。
"""

from __future__ import annotations

import copy
import math
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .config import ConfigError, _Checker, _read_json
from .game import GameResult

SUPPORTED_FORMAT_VERSION = 1
COUNT_KEYS = ("PA", "HR", "BIP", "HIT", "R")
FACTOR_KEYS = ("runs", "home_run", "babip")
SHRINK_KEYS = ("home_run", "babip")  # 縮める定数を持つ項目(得点は組み立てる。D-148)
FACTOR_LABELS = {"runs": "得点", "home_run": "本塁打", "babip": "BABIP"}
_HITS = ("single", "double", "triple", "home_run")


# ---- 集計(整数) ----

@dataclass
class ParkTally:
    """1球場(= 本拠地のチーム)の集計。home は本拠地の試合、away は同じチームがアウェイで行った試合(両チーム合計)。"""

    home: Counter = field(default_factory=Counter)
    away: Counter = field(default_factory=Counter)

    def add(self, other: "ParkTally") -> None:
        self.home.update(other.home)
        self.away.update(other.away)

    def to_dict(self) -> dict:
        return {"home": {k: int(self.home[k]) for k in COUNT_KEYS}, "away": {k: int(self.away[k]) for k in COUNT_KEYS}}

    @classmethod
    def from_dict(cls, d: Mapping) -> "ParkTally":
        return cls(Counter({k: int(d["home"][k]) for k in COUNT_KEYS}), Counter({k: int(d["away"][k]) for k in COUNT_KEYS}))


def game_counts(result: GameResult) -> Counter:
    """1試合の、両チーム合計の数。"""
    c = Counter()
    for x in result.log:
        r = x.pa.result
        c["PA"] += 1
        c["R"] += x.runs
        if r == "home_run":
            c["HR"] += 1
        at_bat = r not in ("walk", "hit_by_pitch") and not x.sac_fly
        if at_bat and r not in ("strikeout", "home_run"):
            c["BIP"] += 1  # 打数 − 三振 − 本塁打 + 犠牲フライ と同じ数え方(犠牲フライもインプレー)
        if x.sac_fly:
            c["BIP"] += 1
        if r in _HITS and r != "home_run":
            c["HIT"] += 1
    return c


def add_game(tallies: dict[str, ParkTally], result: GameResult) -> None:
    """試合を、ホームチームの球場の本拠地の集計と、アウェイチームのアウェイの集計に足す。"""
    c = game_counts(result)
    tallies.setdefault(result.home_team_id, ParkTally()).home.update(c)
    tallies.setdefault(result.away_team_id, ParkTally()).away.update(c)


def season_tallies(results: Iterable[GameResult]) -> dict[str, ParkTally]:
    tallies: dict[str, ParkTally] = {}
    for r in results:
        add_game(tallies, r)
    return tallies


def history_to_dict(history: list[dict[str, ParkTally]]) -> list[dict]:
    return [{"season": i + 1, "parks": {tid: t.to_dict() for tid, t in sorted(season.items())}} for i, season in enumerate(history)]


def history_from_dict(data: list) -> list[dict[str, ParkTally]]:
    return [{tid: ParkTally.from_dict(t) for tid, t in season["parks"].items()} for season in data]


# ---- 推定 ----

@dataclass
class ParkEstimate:
    raw: dict[str, Fraction | None]  # 生の比(求められないときは None。得点の生の比は検証用)
    estimate: dict[str, Fraction]  # 本塁打・BABIP:縮めてリーグでそろえた推定値。得点:本塁打と BABIP から組み立てた値
    home_pa: int  # 合算した本拠地の打席数
    seasons: int  # 合算したシーズン数

    def to_dict(self) -> dict:
        return {
            "raw": {k: (None if v is None else str(v)) for k, v in self.raw.items()},
            "estimate": {k: str(v) for k, v in self.estimate.items()},
            "home_pa": self.home_pa,
            "seasons": self.seasons,
        }


def _rate(c: Counter, num: str, den: str) -> Fraction | None:
    return Fraction(c[num], c[den]) if c[den] else None


def raw_ratio(tally: ParkTally, key: str) -> Fraction | None:
    """生の比(本拠地の率 ÷ アウェイの率)。"""
    num, den = {"runs": ("R", "PA"), "home_run": ("HR", "PA"), "babip": ("HIT", "BIP")}[key]
    h, a = _rate(tally.home, num, den), _rate(tally.away, num, den)
    if h is None or a is None or a == 0:
        return None
    return h / a


def shrink_weight(home_pa: int, constant: int) -> Fraction:
    """1.0 に向けて縮めるときの、生の比の比重 = 本拠地の打席数 ÷(本拠地の打席数 + 定数)。"""
    return Fraction(home_pa, home_pa + constant) if home_pa + constant else Fraction(0)


def thousandths(value: Fraction) -> int:
    """分数を千分率の整数に丸める(四捨五入)。"""
    return int(math.floor(value * 1000 + Fraction(1, 2)))


def _normalize(out: dict[str, ParkEstimate], groups: dict[int, list[str]], key: str) -> None:
    for ids in groups.values():
        mean = sum((out[tid].estimate[key] for tid in ids), Fraction(0)) / len(ids)
        if mean:
            for tid in ids:
                out[tid].estimate[key] /= mean


def estimate_parks(
    history: Iterable[dict[str, ParkTally]],
    league_of: Mapping[str, int],
    settings: "ParkSettings",
    converter=None,
) -> dict[str, ParkEstimate]:
    """履歴(シーズンごとの集計)から、球場ごとの推定値を求める。各リーグの平均を 1.0 にそろえる。

    converter は本塁打と BABIP の倍率を得点の出やすさに換算するもの(parks.RunConverter)。None なら既定の設定で作る。
    """
    from .models import ParkFactors
    from .parks import RunConverter

    total: dict[str, ParkTally] = {tid: ParkTally() for tid in league_of}
    seasons = 0
    for season in history:
        seasons += 1
        for tid, t in season.items():
            total.setdefault(tid, ParkTally()).add(t)
    out: dict[str, ParkEstimate] = {}
    for tid, t in total.items():
        raw = {k: raw_ratio(t, k) for k in FACTOR_KEYS}
        est = {}
        for k in SHRINK_KEYS:
            w = shrink_weight(t.home["PA"], settings.shrink_pa(k))
            est[k] = Fraction(1) if raw[k] is None else 1 + (raw[k] - 1) * w
        out[tid] = ParkEstimate(raw, est, t.home["PA"], seasons)
    groups: dict[int, list[str]] = {}
    for tid in out:
        groups.setdefault(league_of.get(tid, 0), []).append(tid)
    for k in SHRINK_KEYS:
        _normalize(out, groups, k)
    # 得点:本塁打と BABIP の推定から組み立てる(D-147)
    if converter is None:
        converter = RunConverter()
    for e in out.values():
        park = ParkFactors(thousandths(e.estimate["home_run"]), thousandths(e.estimate["babip"]))
        e.estimate["runs"] = Fraction(int(round(converter.run_factor(park) * 1000)), 1000)
    _normalize(out, groups, "runs")
    return out


def player_park_factor(estimates: Mapping[str, ParkEstimate] | None, park_pa: Mapping[str, int], key: str = "runs") -> Fraction:
    """選手の球場補正:球場ごとに立った打席数で重みづけした平均。推定がなければ 1.0。"""
    if not estimates:
        return Fraction(1)
    total = sum(park_pa.values())
    if not total:
        return Fraction(1)
    return sum((estimates[tid].estimate[key] * n for tid, n in park_pa.items() if tid in estimates), Fraction(0)) / total


# ---- 設定(data/park_factors.json) ----

@dataclass(frozen=True)
class ParkSettings:
    data: dict
    source: str

    def shrink_pa(self, key: str) -> int:
        return int(self.data["shrink_pa"][key])

    def assumed_sd(self, key: str) -> Fraction:
        return Fraction(str(self.data["assumed_sd"][key]))


def load_park_settings(path: str | Path | None = None) -> ParkSettings:
    data, source = _read_json(path, "park_factors.json")
    return validate_park_settings(data, source)


def validate_park_settings(data, source: str = "(辞書)") -> ParkSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    for section, low, high in (("shrink_pa", 1, 10_000_000), ("assumed_sd", 0.001, 1.0)):
        sec = c.section(c.get(root, section, ""), section)
        for k in SHRINK_KEYS:
            v = c.get(sec, k, section)
            if section == "shrink_pa":
                c.integer(v, f"{section}.{k}", low, high)
            else:
                c.number(v, f"{section}.{k}", low, high)
        for k in sec or {}:
            if k not in SHRINK_KEYS:
                c.add(f"{section}.{k}", "知らない名前です(得点用の定数は ②c で廃止。D-148)")
    if c.problems:
        raise ConfigError(source, c.problems)
    return ParkSettings(copy.deepcopy(root), source)


# ---- 複数シーズンの確認用(内部用。画面には出さない) ----

def run_seasons(
    league_seed: int,
    n: int,
    settings: ParkSettings,
    on_season: Callable[[int, dict[str, ParkEstimate], object], None] | None = None,
    pa_config=None,
    parks: bool = True,
    on_results: Callable[[int, list, object, dict[str, ParkEstimate] | None], None] | None = None,
) -> tuple[list[dict[str, ParkTally]], dict[str, ParkEstimate], object]:
    """同じリーグを n シーズン繰り返して回し、履歴と推定を返す(F2 の「年度の確定」の最小の形。加齢などは含めない)。

    各シーズンは、リーグを作り直して(選手は同じ)、リーグのシードから導いたシーズンのシードで回す。
    on_season には、シーズンごとに (シーズン番号, そこまでの推定, リーグ) を知らせる。
    on_results には、シーズンごとに (シーズン番号, 試合の結果の一覧, リーグ, 前のシーズンまでの推定(1シーズン目は None)) を知らせる
    (第3弾②の得点の計算と指紋 (k) 用)。
    """
    from .newgame import new_league
    from .parks import RunConverter, neutralize_parks
    from .plate_appearance import OddsRatioModel
    from .season import Season, derive_seed

    converter = RunConverter()
    history: list[dict[str, ParkTally]] = []
    estimates: dict[str, ParkEstimate] = {}
    league = None
    for k in range(1, n + 1):
        league = new_league(league_seed)
        if not parks:
            neutralize_parks(league)
        model = OddsRatioModel(pa_config) if pa_config is not None else None
        season = Season(league, derive_seed(league_seed, f"season:{k}"), model=model)
        results = [p.result for p in season.play_to_end().games]
        if on_results:
            on_results(k, results, league, estimates if history else None)
        history.append(season_tallies(results))
        estimates = estimate_parks(history, {t.id: t.league_index for t in league.teams}, settings, converter)
        if on_season:
            on_season(k, estimates, league)
    return history, estimates, league
