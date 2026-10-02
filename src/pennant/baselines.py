"""指標の基準値(第2弾①。D-063、D-091、D-121〜D-127)。

打席ログから、リーグ全体の基準値を求める。
  - RE24:打席の前の走者・アウトの状況(24通り)ごとの、イニングの終わりまでの得点期待値
  - 線形加重:結果の種類ごとの得点価値(打席の後の期待値 − 打席の前の期待値 + その打席の得点 の平均)
  - wOBA の重み(得点価値 − アウトの得点価値)と目盛り(リーグの wOBA = リーグの出塁率 になる倍率)
  - リーグ平均(出塁率・長打率・1打席あたりの得点・wOBA・防御率)と FIP 定数

計算はすべて分数(Fraction)。入力は整数の集計(RunTally)で、試合ごとに作って足していける。
サヨナラで打ち切られた半イニングは、RE24 と線形加重の集計から外す。失策による出塁は重みを付けない(0)。

シーズン中に使う値は、出発点の値(前のシーズン。初年度は試運転)と今シーズンの値を、
累積打席数に応じて混ぜる(blend。D-126)。
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .config import ConfigError, _Checker, _read_json
from .game import GameResult
from .models import League
from .records import Records, season_records

SUPPORTED_FORMAT_VERSION = 1

# 状況:アウト数(0〜2)× 走者(一塁・二塁・三塁の有無 8通り)。番号は アウト × 8 + 走者のビット
STATES = 24
END = 24  # イニングが終わった(打席の後の期待値は 0)
EVENTS = ("BB", "HBP", "B1", "B2", "B3", "HR", "OUT", "ROE")
WOBA_EVENTS = ("BB", "HBP", "B1", "B2", "B3", "HR")  # wOBA で重みを付ける結果(失策は付けない)
_RESULT_EVENT = {
    "walk": "BB",
    "hit_by_pitch": "HBP",
    "single": "B1",
    "double": "B2",
    "triple": "B3",
    "home_run": "HR",
    "error": "ROE",
}
RUNNERS_TEXT = ("走者なし", "一塁", "二塁", "一・二塁", "三塁", "一・三塁", "二・三塁", "満塁")
EVENT_LABELS = {"BB": "四球", "HBP": "死球", "B1": "単打", "B2": "二塁打", "B3": "三塁打", "HR": "本塁打", "OUT": "アウト", "ROE": "失策による出塁"}
# 混ぜる値(D-126)。FIP の係数も持つ(出どころを切り替えられるように。D-125)
VALUE_NAMES = (
    "w_bb", "w_hbp", "w_1b", "w_2b", "w_3b", "w_hr", "woba_scale",
    "lg_obp", "lg_slg", "lg_woba", "lg_r_pa", "lg_era",
    "fip_hr", "fip_bb", "fip_so", "fip_constant", "pf",
)
_WEIGHT_NAME = {"BB": "w_bb", "HBP": "w_hbp", "B1": "w_1b", "B2": "w_2b", "B3": "w_3b", "HR": "w_hr"}


def state_index(base_out) -> int:
    bits = (1 if base_out.first else 0) | (2 if base_out.second else 0) | (4 if base_out.third else 0)
    return base_out.outs * 8 + bits


def state_label(i: int) -> str:
    return f"{'無死一死二死'[i // 8 * 2 : i // 8 * 2 + 2]}{RUNNERS_TEXT[i % 8]}"


# ---- 整数の集計(試合ごとに作って足せる) ----

@dataclass
class RunTally:
    """RE24 と線形加重の元になる整数の集計。"""

    re_runs: list[int] = field(default_factory=lambda: [0] * STATES)  # 状況ごとの、イニングの終わりまでの得点の合計
    re_count: list[int] = field(default_factory=lambda: [0] * STATES)  # 状況ごとの打席数
    moves: dict[str, Counter] = field(default_factory=lambda: {e: Counter() for e in EVENTS})  # 結果 → (前, 後) → 回数
    move_runs: dict[str, Counter] = field(default_factory=lambda: {e: Counter() for e in EVENTS})  # 結果 → (前, 後) → その打席の得点の合計
    plate_appearances: int = 0  # 集計に使った打席数(サヨナラの半イニングを除く)

    def add(self, other: "RunTally") -> None:
        for i in range(STATES):
            self.re_runs[i] += other.re_runs[i]
            self.re_count[i] += other.re_count[i]
        for e in EVENTS:
            self.moves[e].update(other.moves[e])
            self.move_runs[e].update(other.move_runs[e])
        self.plate_appearances += other.plate_appearances


def event_of(x) -> str:
    return _RESULT_EVENT.get(x.pa.result, "OUT")


def tally_game(result: GameResult) -> RunTally:
    """1試合分の集計。半イニングごとに、打席から終わりまでの得点と、状況の移り変わりを数える。"""
    t = RunTally()
    halves: dict[tuple, list] = {}
    for x in result.log:
        halves.setdefault((x.inning, x.half), []).append(x)
    for pas in halves.values():
        if any(x.walkoff for x in pas):
            continue  # サヨナラで打ち切られた半イニングは外す(D-123)
        rest = sum(x.runs for x in pas)
        for k, x in enumerate(pas):
            s = state_index(x.base_out)
            t.re_runs[s] += rest
            t.re_count[s] += 1
            rest -= x.runs
            after = state_index(pas[k + 1].base_out) if k + 1 < len(pas) else END
            e = event_of(x)
            t.moves[e][(s, after)] += 1
            t.move_runs[e][(s, after)] += x.runs
            t.plate_appearances += 1
    return t


def tally_games(results: Iterable[GameResult]) -> RunTally:
    total = RunTally()
    for r in results:
        total.add(tally_game(r))
    return total


# ---- 基準値 ----

@dataclass
class Baselines:
    """基準値。values は指標の式で使う名前 → 分数。re24・linear_weights は報告・確認用。"""

    values: dict[str, Fraction]
    re24: list[Fraction | None] = field(default_factory=lambda: [None] * STATES)
    linear_weights: dict[str, Fraction | None] = field(default_factory=dict)
    plate_appearances: int = 0  # 求めるのに使った打席数
    source: str = "default"  # trial(試運転)/ default(設定ファイルの既定値)/ season(シーズンの記録)/ blend(混ぜた値)

    def to_dict(self) -> dict:
        def f(v):
            return None if v is None else str(v)

        return {
            "source": self.source,
            "plate_appearances": self.plate_appearances,
            "values": {k: f(v) for k, v in self.values.items()},
            "re24": [f(v) for v in self.re24],
            "linear_weights": {k: f(v) for k, v in self.linear_weights.items()},
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Baselines":
        def f(v):
            return None if v is None else Fraction(v)

        return cls(
            values={k: Fraction(v) for k, v in d["values"].items()},
            re24=[f(v) for v in d.get("re24") or [None] * STATES],
            linear_weights={k: f(v) for k, v in (d.get("linear_weights") or {}).items()},
            plate_appearances=int(d.get("plate_appearances", 0)),
            source=str(d.get("source", "default")),
        )


def _div(a, b) -> Fraction | None:
    return None if b == 0 else Fraction(a) / b


def compute_baselines(
    tally: RunTally,
    batting: Mapping[str, int],
    pitching: Mapping[str, int],
    settings: "BaselineSettings",
    fallback: Baselines | None = None,
    source: str = "season",
) -> Baselines:
    """整数の集計と、リーグ全体の元の数(打者・投手の合計)から基準値を求める(D-123〜D-125)。

    求められない値(まだ起きていない状況・結果など)は、fallback(出発点)の値を使う。
    """
    fb = fallback.values if fallback else {}
    re24: list[Fraction | None] = []
    for i in range(STATES):
        v = _div(tally.re_runs[i], tally.re_count[i])
        if v is None and fallback is not None:
            v = fallback.re24[i]
        re24.append(v)

    def re(i: int) -> Fraction | None:
        return Fraction(0) if i == END else re24[i]

    lw: dict[str, Fraction | None] = {}
    for e in EVENTS:
        total, n = Fraction(0), 0
        for (s, after), count in tally.moves[e].items():
            a, b = re(s), re(after)
            if a is None or b is None:
                continue  # 期待値が分からない状況は外す
            total += count * (b - a) + tally.move_runs[e][(s, after)]
            n += count
        lw[e] = total / n if n else None
    values: dict[str, Fraction] = {}
    raw: dict[str, Fraction] = {}
    out = lw.get("OUT")
    for e in WOBA_EVENTS:
        if lw.get(e) is not None and out is not None:
            raw[e] = lw[e] - out
    # リーグ平均(12球団の合算。D-124)
    ab, bb, hbp, sf = (batting.get(k, 0) for k in ("AB", "BB", "HBP", "SF"))
    denom = ab + bb + hbp + sf
    lg = {
        "lg_obp": _div(batting.get("H", 0) + bb + hbp, denom),
        "lg_slg": _div(batting.get("TB", 0), ab),
        "lg_r_pa": _div(batting.get("R", 0), batting.get("PA", 0)),
        "lg_era": _div(27 * pitching.get("ER", 0), pitching.get("OUTS", 0)),
    }
    counts = {"BB": bb, "HBP": hbp, "B1": batting.get("B1", 0), "B2": batting.get("B2", 0), "B3": batting.get("B3", 0), "HR": batting.get("HR", 0)}
    scale = None
    if len(raw) == len(WOBA_EVENTS) and denom and lg["lg_obp"]:
        raw_woba = sum((raw[e] * counts[e] for e in WOBA_EVENTS), Fraction(0)) / denom
        if raw_woba:
            scale = lg["lg_obp"] / raw_woba
    if scale is not None:
        values["woba_scale"] = scale
        for e in WOBA_EVENTS:
            values[_WEIGHT_NAME[e]] = raw[e] * scale
        lg["lg_woba"] = lg["lg_obp"]
    for k, v in lg.items():
        if v is not None:
            values[k] = v
    # FIP:係数(出どころは設定。D-125)と FIP 定数(リーグの FIP = リーグの防御率)
    coef = settings.fip_coefficients()
    values.update(coef)
    outs = pitching.get("OUTS", 0)
    if outs and lg["lg_era"] is not None:
        raw_fip = (coef["fip_hr"] * pitching.get("HR", 0) + coef["fip_bb"] * (pitching.get("BB", 0) + pitching.get("HBP", 0)) - coef["fip_so"] * pitching.get("SO", 0)) * 3 / Fraction(outs)
        values["fip_constant"] = lg["lg_era"] - raw_fip
    values["pf"] = Fraction(1)  # 球場補正は第2弾②まで 1.0(D-120)
    for k in VALUE_NAMES:
        if k not in values and k in fb:
            values[k] = fb[k]
    return Baselines(values, re24, lw, tally.plate_appearances, source)


def blend_weight(plate_appearances: int, constant: int) -> Fraction:
    """今シーズンの値の比重 = 累積打席数 ÷(累積打席数 + 定数)(D-126)。"""
    return Fraction(plate_appearances, plate_appearances + constant) if plate_appearances + constant else Fraction(0)


def blend(prior: Baselines, current: Baselines | None, plate_appearances: int, constant: int) -> tuple[Baselines, Fraction]:
    """出発点と今シーズンの値を混ぜる。今シーズンの値がない項目は、出発点の値を使う。"""
    w = blend_weight(plate_appearances, constant) if current is not None else Fraction(0)
    values = {}
    for k, p in prior.values.items():
        c = current.values.get(k) if current is not None else None
        values[k] = p if c is None else w * c + (1 - w) * p
    re24 = [
        (p if c is None else w * c + (1 - w) * p) if p is not None else c
        for p, c in zip(prior.re24, current.re24 if current is not None else [None] * STATES)
    ]
    lw = {}
    for e in EVENTS:
        p = prior.linear_weights.get(e)
        c = current.linear_weights.get(e) if current is not None else None
        lw[e] = p if c is None else c if p is None else w * c + (1 - w) * p
    return Baselines(values, re24, lw, plate_appearances, "blend"), w


# ---- 設定(data/baselines.json) ----

@dataclass(frozen=True)
class BaselineSettings:
    data: dict
    source: str

    @property
    def blend_constant(self) -> int:
        return int(self.data["blend_constant_pa"])

    def fip_coefficients(self) -> dict[str, Fraction]:
        """FIP の係数(出どころ:standard = 標準の 13・3・2。D-125)。"""
        mode = self.data["fip_coefficients"]
        if mode == "standard":
            c = self.data["fip_standard"]
            return {"fip_hr": Fraction(c["hr"]), "fip_bb": Fraction(c["bb"]), "fip_so": Fraction(c["so"])}
        raise ValueError(f"FIP の係数の出どころ {mode!r} は、まだ作っていません")

    def default_baselines(self) -> Baselines:
        """設定ファイルの既定値(小数の文字を分数にする。D-127)。"""
        values = {k: Fraction(str(v)) for k, v in self.data["defaults"].items()}
        values.update(self.fip_coefficients())
        values["pf"] = Fraction(1)
        return Baselines(values, source="default")


FIP_MODES = ("standard",)


def load_baseline_settings(path: str | Path | None = None) -> BaselineSettings:
    data, source = _read_json(path, "baselines.json")
    return validate_baseline_settings(data, source)


def validate_baseline_settings(data, source: str = "(辞書)") -> BaselineSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    c.integer(c.get(root, "blend_constant_pa", ""), "blend_constant_pa", 1, 10_000_000)
    mode = c.get(root, "fip_coefficients", "")
    if mode is not None and mode not in FIP_MODES:
        c.add("fip_coefficients", f"知らない出どころです(使えるもの: {', '.join(FIP_MODES)})")
    std = c.section(c.get(root, "fip_standard", ""), "fip_standard")
    for k in ("hr", "bb", "so"):
        c.number(c.get(std, k, "fip_standard"), f"fip_standard.{k}", 0, 100)
    defaults = c.section(c.get(root, "defaults", ""), "defaults") or {}
    needed = [k for k in VALUE_NAMES if not k.startswith("fip_") or k == "fip_constant"]
    needed.remove("pf")
    for k in needed:
        v = c.get(defaults, k, "defaults")
        if v is not None:
            try:
                Fraction(str(v))
            except (ValueError, ZeroDivisionError):
                c.add(f"defaults.{k}", f"数(小数の文字)を書いてください(値: {v!r})")
    for k in defaults:
        if k not in needed:
            c.add(f"defaults.{k}", "知らない名前です")
    if c.problems:
        raise ConfigError(source, c.problems)
    return BaselineSettings(copy.deepcopy(root), source)


# ---- 試運転(D-121) ----

def trial_seed(league_seed: int) -> int:
    """試運転のシード(リーグのシードから、環境に依存しない方法で決める)。"""
    from .season import derive_seed

    return derive_seed(league_seed, "trial-season")


def season_baselines(results: list[GameResult], settings: BaselineSettings, fallback: Baselines | None = None, source: str = "season") -> Baselines:
    rec = season_records(results)
    return compute_baselines(tally_games(results), league_totals(rec, "batter"), league_totals(rec, "pitcher"), settings, fallback, source)


def blended_for_results(prior: Baselines, results: list[GameResult], settings: BaselineSettings) -> tuple[Baselines, Fraction]:
    """シーズンの試合の結果から、出発点と混ぜた基準値を求める(画面の操作の関数と同じ値になる)。"""
    tally = tally_games(results)
    rec = season_records(results)
    batting = league_totals(rec, "batter")
    current = None
    if tally.plate_appearances:
        current = compute_baselines(tally, batting, league_totals(rec, "pitcher"), settings, prior)
    return blend(prior, current, batting["PA"], settings.blend_constant)  # 比重は今シーズンの全打席数で(D-126)


def league_totals(rec: Records, role: str) -> Counter:
    total = Counter()
    for c in (rec.batters if role == "batter" else rec.pitchers).values():
        total.update(c)
    return total


def trial_baselines(
    league: League,
    settings: BaselineSettings,
    progress: Callable[[int, int], None] | None = None,
) -> Baselines:
    """見えない試運転のシーズンを1回回して、初年度の基準値を求める。

    リーグは複製して回すので、渡したリーグ(疲労などの状態)には触れない。試運転の成績・打席ログは捨てる。
    progress には (終わった日数, 全日数) を知らせる。
    """
    from .season import Season

    copied = copy.deepcopy(league)
    season = Season(copied, trial_seed(league.seed))
    season.play_to_end(None if progress is None else (lambda day, days, _g, _n: progress(day, days)))
    return season_baselines([p.result for p in season.played], settings, settings.default_baselines(), "trial")
