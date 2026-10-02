"""結果の指紋(再現性の確認用)。

決まったシードで、リーグの生成・1試合・数十日分の試合を行い、その結果から SHA-256 のハッシュ値
(指紋)を作る。端末や Python の版が違っても、同じシードなら同じ指紋になることを確かめるために使う
(依頼「再現性の確認」。PC の Python でも、ブラウザの中の Python でも同じ関数を呼ぶ)。

指紋の元にするのは、離散的な結果(打席の結果の種類、担当ポジション、得点、投手交代など)と、
選手の整数・文字列の情報だけ。小数は入れない(小数の最後の桁が環境によってわずかにずれても、
指紋が変わらないようにするため)。
"""

from __future__ import annotations

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
from .records import Records, season_records
from .season import Season, SeasonResult

FINGERPRINT_VERSION = 3  # 2:(d)1シーズン と打ち切りの印(実装④)。3:(e)集計結果 と担当野手の選手 ID(実装⑤)
LEAGUE_SEED = 1  # (a)〜(c)で使うリーグのシード
GAME_SEED = 7  # (b)1試合の乱数のシード
DAYS_SEED = 11  # (c)数十日分の試合の乱数のシード
DAYS = 30  # (c)の日数(1日=各リーグ3試合)
SEASON_SEED = 13  # (d)1シーズンのシード(日程と、各試合の乱数のもと)
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

def _new_league() -> League:
    return generate_league(LEAGUE_SEED, load_generation_config(), load_name_parts())


def season_fingerprint() -> tuple[str, SeasonResult]:
    """(d)1シーズンの指紋と、その結果。"""
    result = Season(_new_league(), SEASON_SEED).play_to_end()
    return _digest(season_record(result)), result


def fingerprints() -> dict:
    """(a)リーグの生成、(b)1試合、(c)数十日分の試合、(d)1シーズン の指紋と、確認用の数を返す。"""
    config = load_game_config()
    manager = SimpleManager(config)

    league = _new_league()
    a = _digest(league_record(league))

    rng = random.Random(GAME_SEED)
    home, _ = manager.prepare(league.teams[0], rng, 0)
    away, _ = manager.prepare(league.teams[1], rng, 0)
    game = simulate_game(home, away, rng, config=config, manager=manager)
    b = _digest(game_record(game))

    days_league = _new_league()  # 疲労が書き換わるので、別のリーグで行う
    results = play_games(days_league, DAYS * GAMES_PER_DAY, DAYS_SEED, config, manager=manager)
    c = _digest([game_record(r) for r in results])

    d, season = season_fingerprint()
    champions = [r.team_id for rows in season.standings.values() for r in rows if r.rank == 1]
    rec = season_records([p.result for p in season.games])
    e = _digest(records_record(rec))
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
        ]
    )
