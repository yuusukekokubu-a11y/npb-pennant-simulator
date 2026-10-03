"""WAR の計算(第3弾③a。D-167〜D-173)。

WAR(Wins Above Replacement):控え水準の選手に比べて、何勝分多く勝ちに貢献したか。
  - 打者:(打撃 + 走塁 + 守備 + ポジション補正 + 控え水準の得点)÷ 1勝あたりの得点(D-171)
  - 投手(FIP 版):((リーグの防御率 − FIP)× 投球回 ÷ 9 + 控え水準)÷ 1勝あたりの得点。球場補正なし(D-144)
  - 投手(失点版):((リーグの9イニングあたりの失点 × 球場補正 − 投手の失点率)× 投球回 ÷ 9 − チームの野手の守備の得点の投手の分 + 控え水準)÷ 1勝あたりの得点
  - 1勝あたりの得点 = 4 × 1チーム1試合あたりの得点 ÷ 指数(指数 2 なら 2 × 得点。ピタゴラス勝率。D-170)
  - ポジション補正・控え水準は設定値(data/war.json。D-168、D-169)
計算はすべて分数。試合の計算は変えず、打席ログからの再集計で求める(保存はしない。D-172)。画面には出さない(③b)。
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .baselines import Baselines
from .config import ConfigError, _Checker, _read_json
from .game import GameResult
from .records import Records, season_records
from .runvalues import DH, PlayerRuns

SUPPORTED_FORMAT_VERSION = 1
POSITIONS_WITH_DH = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")


# ---- 設定(data/war.json) ----

@dataclass(frozen=True)
class WarSettings:
    data: dict
    source: str

    def position_runs(self, position: str) -> Fraction:
        return Fraction(str(self.data["position_runs_per_season"].get(position, 0)))

    def replacement(self, key: str) -> Fraction:
        return Fraction(str(self.data["replacement"][key]))

    @property
    def replacement_win_pct(self) -> Fraction:
        return Fraction(str(self.data["replacement_win_pct"]))

    @property
    def exponent(self) -> Fraction:
        return Fraction(str(self.data["pythagorean_exponent"]))


def load_war_settings(path: str | Path | None = None) -> WarSettings:
    data, source = _read_json(path, "war.json")
    return validate_war_settings(data, source)


def validate_war_settings(data, source: str = "(辞書)") -> WarSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    c.number(c.get(root, "pythagorean_exponent", ""), "pythagorean_exponent", 1, 3)
    c.number(c.get(root, "replacement_win_pct", ""), "replacement_win_pct", 0, 0.5)
    pos = c.section(c.get(root, "position_runs_per_season", ""), "position_runs_per_season")
    for key in POSITIONS_WITH_DH:
        c.number(c.get(pos, key, "position_runs_per_season"), f"position_runs_per_season.{key}", -50, 50)
    for key in pos or {}:
        if key not in POSITIONS_WITH_DH:
            c.add(f"position_runs_per_season.{key}", f"知らないポジションです(使えるもの: {', '.join(POSITIONS_WITH_DH)})")
    rep = c.section(c.get(root, "replacement", ""), "replacement")
    for key, low, high in (("batter_runs_per_pa", 0, 0.2), ("starter_runs_per_9", 0, 5), ("reliever_runs_per_9", 0, 5)):
        c.number(c.get(rep, key, "replacement"), f"replacement.{key}", low, high)
    if rep and all(isinstance(rep.get(k), (int, float)) for k in ("starter_runs_per_9", "reliever_runs_per_9")):
        if rep["reliever_runs_per_9"] > rep["starter_runs_per_9"]:
            c.add("replacement.reliever_runs_per_9", "救援の控え水準は先発より高く(値を小さく)してください(D-169)")
    if c.problems:
        raise ConfigError(source, c.problems)
    return WarSettings(copy.deepcopy(root), source)


# ---- 結果 ----

@dataclass
class WarLine:
    """選手 × シーズンの WAR と内訳(分数)。打者は batting〜war、投手は fip_runs〜war_ra を使う。"""

    role: str  # batter / pitcher
    team_id: str
    # 打者
    batting: Fraction = Fraction(0)
    baserunning: Fraction = Fraction(0)
    fielding: Fraction = Fraction(0)
    position: Fraction = Fraction(0)  # ポジション補正
    replacement: Fraction = Fraction(0)  # 控え水準の得点
    plate_appearances: int = 0
    war: Fraction = Fraction(0)
    # 投手
    outs: int = 0
    fip_runs: Fraction = Fraction(0)  # リーグの防御率との差(投球回で重みづけ)
    ra_runs: Fraction = Fraction(0)  # 失点との差(球場補正つき)
    defense_adjustment: Fraction = Fraction(0)  # チームの野手の守備の得点の、この投手の分(差し引く)
    park_factor: Fraction = Fraction(1)
    war_fip: Fraction = Fraction(0)
    war_ra: Fraction = Fraction(0)
    runs_per_win: Fraction = Fraction(0)  # 計算に使った1勝あたりの得点(内訳の換算用。指紋には入れない)

    def to_dict(self) -> dict:
        if self.role == "batter":
            return {k: str(getattr(self, k)) for k in ("batting", "baserunning", "fielding", "position", "replacement", "war")} | {"plate_appearances": self.plate_appearances}
        return {k: str(getattr(self, k)) for k in ("fip_runs", "ra_runs", "defense_adjustment", "park_factor", "replacement", "war_fip", "war_ra")} | {"outs": self.outs}


def runs_per_win(values: Mapping[str, Fraction], pa_per_team_game: Fraction, exponent: Fraction = Fraction(2)) -> Fraction:
    """1勝あたりの得点 = 4 × 1チーム1試合あたりの得点 ÷ 指数(指数 2 なら 2 × 得点。D-170)。

    ピタゴラス勝率 W = R^p ÷ (R^p + RA^p) を、R = RA のところで R について微分すると p ÷ (4R)。
    1点(1試合あたり 1/G 点)で勝ち数は p ÷ (4R) 増えるので、1勝には 4R ÷ p 点が要る。
    """
    runs_per_game = values["lg_r_pa"] * pa_per_team_game
    return 4 * runs_per_game / exponent


def dh_plate_appearances(results: Iterable[GameResult]) -> Counter:
    """選手ごとの、指名打者として立った打席数。"""
    out: Counter = Counter()
    for result in results:
        dh = {pid for slots in result.lineups.values() for _, pid, pos, _ in slots if pos == DH}
        for x in result.log:
            if x.batter_id in dh:
                out[x.batter_id] += 1
    return out


def pitcher_park_factors(results: Iterable[GameResult], estimates) -> dict[str, Fraction]:
    """投手ごとの球場補正(前のシーズンまでの推定を、投げた球場ごとの対戦打者数で重みづけ。D-171)。"""
    from .parkfactors import player_park_factor

    faced: dict[str, Counter] = {}
    for result in results:
        for x in result.log:
            faced.setdefault(x.pitcher_id, Counter())[result.home_team_id] += 1
    return {pid: player_park_factor(estimates, c) for pid, c in faced.items()}


def pitcher_role_outs(results: Iterable[GameResult]) -> dict[str, Counter]:
    """投手ごとの、先発・救援それぞれのアウト数。"""
    out: dict[str, Counter] = {}
    for result in results:
        for line in result.pitchers:
            out.setdefault(line.pitcher_id, Counter())[line.role] += line.outs
    return out


def season_war(
    results: list[GameResult],
    baselines: Baselines,
    runs: Mapping[str, PlayerRuns],
    settings: WarSettings,
    records: Records | None = None,
    pitcher_park_factor_of: Callable[[str], Fraction] | None = None,
    games_per_season: int | None = None,
) -> dict[str, WarLine]:
    """シーズンの試合の結果と、打撃・走塁・守備の得点(runvalues)から、選手ごとの WAR を求める。

    runs の打撃の得点には、すでに打者の球場補正が入っている(D-162)。
    pitcher_park_factor_of は投手 ID → 球場補正(省略時は 1.0)。games_per_season は按分の基準(省略時はチーム試合数の最大)。
    """
    rec = records if records is not None else season_records(results)
    values = baselines.values
    team_games = {tid: c["G"] for tid, c in rec.teams.items()}
    games = games_per_season or max(team_games.values(), default=0)
    total_pa = sum(c["PA"] for c in rec.batters.values())
    total_team_games = sum(team_games.values())
    pa_per_team_game = Fraction(total_pa, total_team_games) if total_team_games else Fraction(0)
    rpw = runs_per_win(values, pa_per_team_game, settings.exponent)
    pa_per_slot = Fraction(total_pa, 9 * len(team_games)) if team_games else Fraction(0)  # 1枠分の打席数(D-168)
    out: dict[str, WarLine] = {}

    # ---- 打者 ----
    dh_pa = dh_plate_appearances(results)
    full_outs = games * 27
    for pid, counts in rec.batters.items():
        r = runs.get(pid, PlayerRuns())
        line = WarLine("batter", rec.batter_team[pid], runs_per_win=rpw)
        line.batting, line.baserunning, line.fielding = r.batting, r.baserunning, r.fielding
        line.plate_appearances = r.plate_appearances
        pos = Fraction(0)
        for position, outs in r.outs_by_position.items():
            if full_outs:
                pos += settings.position_runs(position) * Fraction(outs, full_outs)
        if pa_per_slot and dh_pa.get(pid):
            pos += settings.position_runs(DH) * Fraction(dh_pa[pid]) / pa_per_slot
        line.position = pos
        line.replacement = settings.replacement("batter_runs_per_pa") * r.plate_appearances
        total = line.batting + line.baserunning + line.fielding + line.position + line.replacement
        line.war = total / rpw if rpw else Fraction(0)
        out[pid] = line

    # ---- 投手 ----
    lg_outs = sum(c["OUTS"] for c in rec.pitchers.values())
    lg_runs_allowed = sum(c["R"] for c in rec.pitchers.values())
    lg_ra9 = Fraction(27 * lg_runs_allowed, lg_outs) if lg_outs else Fraction(0)
    lg_era = values["lg_era"]
    role_outs = pitcher_role_outs(results)
    # チームの野手(投手を除く)の守備の得点と、チームの守備アウト数
    team_fielding: dict[str, Fraction] = {}
    for pid, r in runs.items():
        if pid in rec.batters:
            tid = rec.batter_team[pid]
            team_fielding[tid] = team_fielding.get(tid, Fraction(0)) + r.fielding
    team_outs: Counter = Counter()
    for pid, c in rec.pitchers.items():
        team_outs[rec.pitcher_team[pid]] += c["OUTS"]
    coef = {k: values[k] for k in ("fip_hr", "fip_bb", "fip_so", "fip_constant")}
    for pid, c in rec.pitchers.items():
        tid = rec.pitcher_team[pid]
        line = WarLine("pitcher", tid, runs_per_win=rpw)
        outs = c["OUTS"]
        line.outs = outs
        if outs:
            ip9 = Fraction(outs, 27)  # 投球回 ÷ 9
            fip = (coef["fip_hr"] * c["HR"] + coef["fip_bb"] * (c["BB"] + c["HBP"]) - coef["fip_so"] * c["SO"]) * 3 / Fraction(outs) + coef["fip_constant"]
            line.fip_runs = (lg_era - fip) * ip9
            pf = pitcher_park_factor_of(pid) if pitcher_park_factor_of is not None else Fraction(1)
            line.park_factor = pf
            ra9 = Fraction(27 * c["R"], outs)
            line.ra_runs = (lg_ra9 * pf - ra9) * ip9
            if team_outs[tid]:
                line.defense_adjustment = team_fielding.get(tid, Fraction(0)) * Fraction(outs, team_outs[tid])
            ro = role_outs.get(pid, Counter())
            line.replacement = settings.replacement("starter_runs_per_9") * Fraction(ro["starter"], 27) + settings.replacement("reliever_runs_per_9") * Fraction(ro["reliever"], 27)
        if rpw:
            line.war_fip = (line.fip_runs + line.replacement) / rpw
            line.war_ra = (line.ra_runs - line.defense_adjustment + line.replacement) / rpw
        if pid in out:  # 打者としても記録がある場合(今は投手は打席に立たないので起きない)
            continue
        out[pid] = line
    return out


def war_totals(lines: Mapping[str, WarLine]) -> dict[str, Fraction]:
    bat = sum((v.war for v in lines.values() if v.role == "batter"), Fraction(0))
    fip = sum((v.war_fip for v in lines.values() if v.role == "pitcher"), Fraction(0))
    ra = sum((v.war_ra for v in lines.values() if v.role == "pitcher"), Fraction(0))
    return {"batters": bat, "pitchers_fip": fip, "pitchers_ra": ra}


def target_total_war(records: Records, settings: WarSettings) -> Fraction:
    """目標:勝ち数の合計 −(控え水準の勝率 × チーム試合数の合計)(D-169)。"""
    wins = sum(c["W"] for c in records.teams.values())
    games = sum(c["G"] for c in records.teams.values())
    return wins - settings.replacement_win_pct * games


def war_record(lines: Mapping[str, WarLine]) -> dict:
    """指紋 (l) 用。"""
    return {pid: v.to_dict() for pid, v in sorted(lines.items())}


def war_for_results(results: list[GameResult], estimates, baseline_settings, war_settings: WarSettings, baselines: Baselines | None = None, records: Records | None = None):
    """試合の結果から、打撃・走塁・守備の得点と WAR を一度に求める(画面とスクリプトで同じ関数。D-179)。

    estimates は前のシーズンまでの球場補正の推定(1シーズン目は None)。baselines を省略すると、
    今シーズンの記録から求めた値(出発点は設定ファイルの既定値)。画面は混ぜた基準値を渡す。
    戻り値は (WAR の表, 打撃・走塁・守備の得点, 使った基準値)。
    """
    from .baselines import season_baselines
    from .runvalues import player_park_factors, season_player_runs

    rec = records if records is not None else season_records(results)
    base = baselines if baselines is not None else season_baselines(results, baseline_settings, baseline_settings.default_baselines())
    pfs = player_park_factors(results, estimates)
    runs = season_player_runs(results, base, lambda pid: pfs.get(pid, Fraction(1)), rec)
    ppf = pitcher_park_factors(results, estimates)
    lines = season_war(results, base, runs, war_settings, rec, lambda pid: ppf.get(pid, Fraction(1)))
    return lines, runs, base

