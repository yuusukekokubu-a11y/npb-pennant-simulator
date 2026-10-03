"""結果の指紋(再現性の確認用)。

決まったシードで、リーグの生成・1試合・数十日分の試合を行い、その結果から SHA-256 のハッシュ値
(指紋)を作る。端末や Python の版が違っても、同じシードなら同じ指紋になることを確かめるために使う
(依頼「再現性の確認」。PC の Python でも、ブラウザの中の Python でも同じ関数を呼ぶ)。

指紋の元にするのは、離散的な結果(打席の結果の種類、担当ポジション、得点、投手交代など)と、
選手の整数・文字列の情報だけ。小数は入れない(小数の最後の桁が環境によってわずかにずれても、
指紋が変わらないようにするため)。
"""

from __future__ import annotations

from fractions import Fraction

import hashlib
import json
import random

from .config import load_generation_config, load_name_parts
from .game import GameResult, simulate_game
from .game_config import load_game_config
from .game_stats import play_games
from .generate import generate_league
from .manager import SimpleManager
from .models import League
from .parks import neutralize_parks
from .records import Records, season_records
from .season import Season, SeasonResult

FINGERPRINT_VERSION = 10  # 10:(k)打撃・走塁・守備の得点(第3弾②)。 9:第3弾①の選手生成(ポジション別の型の割合)で (a)〜(h)・(j) の値が変わった。 2:(d)1シーズン(④)。3:(e)集計結果(⑤)。4:(f)保存と読み込み(⑥)。5:(g)(h)基準値と第2弾の指標(②)。6:(i)球場の倍率(②a)。7:(j)球場補正の推定(②b)。8:(j)の得点を本塁打と BABIP から組み立てる(②c)
LEAGUE_SEED = 1  # (a)〜(c)で使うリーグのシード
GAME_SEED = 7  # (b)1試合の乱数のシード
DAYS_SEED = 11  # (c)数十日分の試合の乱数のシード
DAYS = 30  # (c)の日数(1日=各リーグ3試合)
SEASON_SEED = 13  # (d)1シーズンのシード(日程と、各試合の乱数のもと)
SAVE_DAYS = (1, 62, 125)  # (f)保存・読み込みする日(1日目・中盤・最後)
PARK_SEASONS = 3  # (j)球場補正の推定に回すシーズン数
GAMES_PER_DAY = 6


def _digest(data) -> str:
    """整数・文字列・真偽値・None と、それらのリスト・辞書だけを受け付け、SHA-256 を返す。"""
    _check_no_float(data)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_no_float(x, path="") -> None:
    if isinstance(x, float):
        raise TypeError(f"指紋の元に小数が入っています: {path}")
    if isinstance(x, dict):
        for k, v in x.items():
            _check_no_float(v, f"{path}.{k}")
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            _check_no_float(v, f"{path}[{i}]")


# ---- 元にする情報 ----

def league_record(league: League) -> dict:
    """リーグの生成の結果のうち、整数・文字列の情報(能力値などの小数は入れない)。"""
    return {
        "seed": league.seed,
        "league_names": list(league.league_names),
        "teams": [
            {
                "id": t.id,
                "league_index": t.league_index,
                "place": t.place,
                "nickname": t.nickname,
                "stadium": t.stadium,
                "players": [
                    [
                        p.id,
                        p.family_name,
                        p.given_name,
                        p.age,
                        p.role,
                        p.position,
                        p.bats,
                        p.throws,
                        p.origin,
                        p.hidden.archetype,
                        p.hidden.growth_type,
                    ]
                    for p in t.players
                ],
            }
            for t in league.teams
        ],
    }


def game_record(result: GameResult) -> dict:
    """1試合の結果のうち、離散的な情報(打席ごとの結果・走者の動き・投手の登板)。"""
    pas = []
    for x in result.log:
        pa = x.pa
        pas.append(
            [
                x.inning,
                x.half,
                x.lineup_slot,
                x.batter_id,
                x.pitcher_id,
                x.score_diff,
                [x.base_out.outs, x.base_out.first, x.base_out.second, x.base_out.third],
                pa.result,
                pa.batted_ball,
                pa.fielder,
                x.fielder_id,
                pa.unfieldable,
                pa.batter_side,
                [[m.player_id, m.start, m.end, m.responsible_pitcher_id, m.reached_on_error, m.advanced_on_error] for m in x.moves],
                x.runs,
                x.outs_made,
                x.double_play,
                x.sac_fly,
                x.walkoff,
                x.walkoff_truncated,
            ]
        )
    pitchers = [
        [p.pitcher_id, p.team_id, p.role, p.entered_inning, p.batters_faced, p.outs, p.runs, p.exit_reason, p.forced_extra_innings]
        for p in result.pitchers
    ]
    return {
        "home": result.home_team_id,
        "away": result.away_team_id,
        "score": [result.home_runs, result.away_runs],
        "line": result.line,
        "innings": result.innings,
        "tie": result.tie,
        "walkoff": result.walkoff,
        "lineups": result.lineups,
        "plate_appearances": pas,
        "pitchers": pitchers,
    }


def season_record(result: SeasonResult) -> dict:
    """1シーズンの結果のうち、離散的な情報(日程・全試合・順位表。勝率などの小数は入れない)。"""
    return {
        "games": [
            {
                "number": p.scheduled.number,
                "day": p.scheduled.day,
                "league": p.scheduled.league_index,
                "starter_skipped": p.starter_skipped,
                "game": game_record(p.result),
            }
            for p in result.games
        ],
        "standings": {
            str(i): [[r.team_id, r.wins, r.losses, r.ties, r.rank] for r in rows] for i, rows in result.standings.items()
        },
        "champions": {str(i): ids for i, ids in result.champions.items()},
    }


def records_record(rec: Records) -> dict:
    """集計結果の元の数(すべて整数)。"""
    def plain(group):
        return {key: {k: v for k, v in sorted(c.items()) if v} for key, c in sorted(group.items())}

    return {"batters": plain(rec.batters), "pitchers": plain(rec.pitchers), "teams": plain(rec.teams)}


# ---- 指紋 ----

def _new_league(parks: bool = True) -> League:
    """指紋に使うリーグ。parks=False なら球場の倍率をすべて 1.0 にする(回帰の確認用。D-137)。"""
    league = generate_league(LEAGUE_SEED, load_generation_config(), load_name_parts())
    if not parks:
        neutralize_parks(league)
    return league


def park_record(league: League) -> list:
    """(i)球場の倍率(千分率の整数)。"""
    return [[t.id, t.park.home_run, t.park.babip] for t in league.teams]


def season_fingerprint(parks: bool = True) -> tuple[str, SeasonResult]:
    """(d)1シーズンの指紋と、その結果。"""
    result = Season(_new_league(parks), SEASON_SEED).play_to_end()
    return _digest(season_record(result)), result


def save_fingerprint(reference: dict | None = None, parks: bool = True) -> tuple[str, list[bool]]:
    """(f)シーズンの途中で保存・読み込みしてから最後まで進めた結果の指紋。

    1日目・中盤・最後のそれぞれで保存・読み込みし、最後まで進めた結果を並べて指紋にする。
    どれも、保存せずに進めた (d) と同じ結果になるはず(2つ目の戻り値は、その一致の確認)。
    """
    from .savegame import GameState, load_game, save_game

    if reference is None:
        reference = season_record(Season(_new_league(parks), SEASON_SEED).play_to_end())
    records = []
    for day in SAVE_DAYS:
        season = Season(_new_league(parks), SEASON_SEED)
        season.play_days(day)
        state = GameState(season, load_generation_config(), load_name_parts())
        loaded = load_game(save_game(state))
        records.append(season_record(loaded.season.play_to_end()))
    return _digest(records), [r == reference for r in records]


def baseline_fingerprint(season: SeasonResult, parks: bool = True) -> tuple[str, str, dict]:
    """(g)試運転で求めた基準値(分数を文字にして)と、(h)(d)のシーズンの全選手の第2弾の指標(分数)の指標。

    (h)の基準値は、(g)を出発点に、(d)のシーズンの値を混ぜた最終値(画面と同じ。D-126)。
    """
    from .api import metrics_config
    from .baselines import blended_for_results, load_baseline_settings, trial_baselines
    from .metrics import compute

    settings = load_baseline_settings()
    prior = trial_baselines(_new_league(parks), settings)
    g = _digest(prior.to_dict())
    results = [p.result for p in season.games]
    final, weight = blended_for_results(prior, results, settings)
    rec = season_records(results)
    config = metrics_config()
    rows = []
    for role, group, keys in (("batter", rec.batters, ("woba", "wrc_plus", "ops_plus")), ("pitcher", rec.pitchers, ("fip",))):
        for pid in sorted(group):
            values = compute(config, role, group[pid], final.values)
            rows.append([pid] + [None if values[k] is None else str(values[k]) for k in keys])
    h = _digest(rows)
    info = {
        "trial_plate_appearances": prior.plate_appearances,
        "batters": len(rec.batters),
        "pitchers": len(rec.pitchers),
        "weight": str(weight),
    }
    return g, h, info


def park_estimate_fingerprint(parks: bool = True) -> tuple[str, dict]:
    """(j)固定のシードで3シーズンを回した、球場補正の推定値(分数を文字にして)。"""
    from .parkfactors import load_park_settings, run_seasons

    _, est, _ = run_seasons(LEAGUE_SEED, PARK_SEASONS, load_park_settings(), parks=parks)
    record = {tid: est[tid].to_dict() for tid in sorted(est)}
    return _digest(record), {"seasons": PARK_SEASONS, "teams": len(est)}


def run_values_fingerprint(parks: bool = True) -> tuple[str, dict]:
    """(k)固定のシードで3シーズン回した、選手ごとの打撃・走塁・守備の得点(分数を文字にして。D-166)。

    基準値はシーズンの記録から求めた値(出発点は設定ファイルの既定値)。球場補正は前のシーズンまでの推定(1シーズン目は 1.0)。
    """
    from .baselines import load_baseline_settings, season_baselines
    from .parkfactors import load_park_settings, run_seasons
    from .runvalues import player_park_factors, runs_record, season_player_runs

    settings = load_baseline_settings()
    record: dict[str, dict] = {}
    info: dict = {"seasons": PARK_SEASONS}

    def on_results(k, results, league, estimates):
        base = season_baselines(results, settings, settings.default_baselines())
        pfs = player_park_factors(results, estimates)
        runs = season_player_runs(results, base, lambda pid: pfs.get(pid, Fraction(1)))
        record[str(k)] = runs_record(runs)
        if k == PARK_SEASONS:
            info["players"] = len(runs)
            info["fielding_chances"] = sum(sum(r.chances_by_position.values()) for r in runs.values())

    run_seasons(LEAGUE_SEED, PARK_SEASONS, load_park_settings(), parks=parks, on_results=on_results)
    return _digest(record), info


def fingerprints(quick: bool = False, parks: bool = True) -> dict:
    """(a)リーグの生成、(b)1試合、(c)数十日分の試合、(d)1シーズン、(e)集計結果、(f)保存と読み込み、
    (g)基準値、(h)第2弾の指標、(i)球場の倍率 の指紋。parks=False は、球場の倍率をすべて 1.0 にする(回帰の確認用)。"""
    config = load_game_config()
    manager = SimpleManager(config)

    league = _new_league(parks)
    a = _digest(league_record(league))
    i = _digest(park_record(league))

    rng = random.Random(GAME_SEED)
    home, _ = manager.prepare(league.teams[0], rng, 0)
    away, _ = manager.prepare(league.teams[1], rng, 0)
    game = simulate_game(home, away, rng, config=config, manager=manager, park=league.teams[0].park)
    b = _digest(game_record(game))
    if quick:  # テスト用:(a)と(b)だけ
        return {"fingerprint_version": FINGERPRINT_VERSION, "league": a, "game": b, "parks": i}

    days_league = _new_league(parks)  # 疲労が書き換わるので、別のリーグで行う
    results = play_games(days_league, DAYS * GAMES_PER_DAY, DAYS_SEED, config, manager=manager)
    c = _digest([game_record(r) for r in results])

    d, season = season_fingerprint(parks)
    champions = [r.team_id for rows in season.standings.values() for r in rows if r.rank == 1]
    f, same_as_season = save_fingerprint(season_record(season), parks)
    rec = season_records([p.result for p in season.games])
    e = _digest(records_record(rec))
    g, h, binfo = baseline_fingerprint(season, parks)
    j, jinfo = park_estimate_fingerprint(parks)
    kk, kinfo = run_values_fingerprint(parks)
    by_league: dict[int, list] = {}
    for t in league.teams:
        by_league.setdefault(t.league_index, []).append(t)
    park_info = {
        "teams": len(league.teams),
        "home_run_mean": [sum(t.park.home_run for t in ts) // len(ts) for ts in by_league.values()],
        "babip_mean": [sum(t.park.babip for t in ts) // len(ts) for ts in by_league.values()],
        "home_run_min_max": [min(t.park.home_run for t in league.teams), max(t.park.home_run for t in league.teams)],
    }
    totals = {"R": 0, "RBI": 0, "W": 0, "SV": 0, "HLD": 0, "ER": 0}
    for key in totals:
        group = rec.batters if key in ("R", "RBI") else rec.pitchers
        totals[key] = sum(counts[key] for counts in group.values())

    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "league": a,
        "game": b,
        "days": c,
        "season": d,
        "records": e,
        "save": f,
        "baselines": g,
        "metrics2": h,
        "parks": i,
        "park_estimates": j,
        "run_values": kk,
        "counts": {
            "players": len(league.all_players()),
            "game_plate_appearances": len(game.log),
            "game_score": f"{game.away_runs}-{game.home_runs}",
            "days": DAYS,
            "days_games": len(results),
            "days_plate_appearances": sum(len(r.log) for r in results),
            "days_runs": sum(r.home_runs + r.away_runs for r in results),
            "season_games": len(season.games),
            "season_plate_appearances": sum(len(p.result.log) for p in season.games),
            "season_champions": champions,
            "records_totals": totals,
            "save_days": list(SAVE_DAYS),
            "save_same_as_season": same_as_season,
            "baselines": binfo,
            "parks": park_info,
            "park_estimates": jinfo,
            "run_values": kinfo,
        },
    }


def format_fingerprints(fp: dict) -> str:
    """画面やスクリプトで表示する文章(PC とブラウザで同じ形)。"""
    c = fp["counts"]
    return "\n".join(
        [
            f"## 結果の指紋(SHA-256。指紋の形式 {fp['fingerprint_version']})",
            f"- (a) リーグの生成({c['players']}人): {fp['league']}",
            f"- (b) 1試合({c['game_plate_appearances']}打席、{c['game_score']}): {fp['game']}",
            f"- (c) {c['days']}日分の試合({c['days_games']}試合、{c['days_plate_appearances']}打席、得点の合計 {c['days_runs']}): {fp['days']}",
            f"- (d) 1シーズン({c['season_games']}試合、{c['season_plate_appearances']}打席、優勝 {'・'.join(c['season_champions'])}): {fp['season']}",
            f"- (e) (d)の集計結果(得点 {c['records_totals']['R']}、打点 {c['records_totals']['RBI']}、自責点 {c['records_totals']['ER']}、"
            f"勝利 {c['records_totals']['W']}、セーブ {c['records_totals']['SV']}、ホールド {c['records_totals']['HLD']}): {fp['records']}",
            f"- (f) (d)を {'・'.join(str(d) for d in c['save_days'])} 日目で保存・読み込みして最後まで進めた結果"
            f"({'(d)と一致' if all(c['save_same_as_season']) else '(d)と不一致'}): {fp['save']}",
            f"- (g) 試運転で求めた基準値(RE24 などに使った打席 {c['baselines']['trial_plate_appearances']}): {fp['baselines']}",
            f"- (h) (d)の全選手の wOBA・wRC+・OPS+・FIP(打者 {c['baselines']['batters']}人・投手 {c['baselines']['pitchers']}人。"
            f"今シーズンの比重 {c['baselines']['weight']}): {fp['metrics2']}",
            f"- (i) 球場の倍率(千分率。{c['parks']['teams']}球場。本塁打 {c['parks']['home_run_min_max'][0]}〜{c['parks']['home_run_min_max'][1]}。"
            f"リーグごとの平均 {'・'.join(str(v) for v in c['parks']['home_run_mean'])}): {fp['parks']}",
            f"- (j) 球場補正の推定({c['park_estimates']['seasons']}シーズンを回した結果。{c['park_estimates']['teams']}球場): {fp['park_estimates']}",
            f"- (k) 打撃・走塁・守備の得点({c['run_values']['seasons']}シーズン。{c['run_values']['players']}人。守備の機会 {c['run_values']['fielding_chances']}): {fp['run_values']}",
        ]
    )
