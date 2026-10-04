"""画面(web/)と操作の関数(pennant.api)をつなぐ、ごく薄い取り次ぎ役(D-107)。

裏の計算(worker.js)が、この関数を呼ぶ。やり取りは JSON の文字(セーブデータの中身だけは bytes)。
遊んでいるゲームを1つ持つ。読み込みや新規開始に失敗したときは、今のゲームをそのまま残す。
見る画面は query(公開用の関数だけ。D-108)、答え合わせは answer(pennant.answers。D-114)で、入口を分ける。
画面は、答え合わせモードがオンのときだけ answer を呼ぶ。
"""

from __future__ import annotations

import json

from pennant import answers, api

_game: api.Game | None = None


def _ok(value) -> str:
    return json.dumps({"ok": True, "value": value}, ensure_ascii=False)


def _ng(message: str, problems: list[str] | None = None) -> str:
    return json.dumps({"ok": False, "message": message, "problems": problems or []}, ensure_ascii=False)


def _view() -> dict:
    """画面の表示に使うもの一式(今の状況・順位表・自球団の直近の試合)。"""
    return {"status": _game.status(), "standings": _game.standings(), "recent": _game.recent_games(), "last_day": _game.last_day_games()}


def preview(seed: int) -> str:
    return _ok(api.preview_teams(int(seed)))


def check(seed: int, names_json: str) -> str:
    return _ok(api.check_team_names(int(seed), json.loads(names_json)))


def new_game(seed: int, season_seed: int, names_json: str, my_team_index, baselines: str = "trial", progress=None, prerun_progress=None, scout_level: str = "medium") -> str:
    """新規開始。baselines は基準値の求め方(trial:試運転で求める / default:既定値)。
    progress は試運転の進み具合を知らせる関数((終わった日数, 全日数) を受け取る)。prerun_progress は事前運転の (終わった年数, 全年数)。"""
    global _game
    try:
        mine = None if my_team_index is None or int(my_team_index) < 0 else int(my_team_index)
        game = api.Game.new(
            int(seed), json.loads(names_json), mine, season_seed=int(season_seed), baselines=str(baselines), progress=progress, prerun_progress=prerun_progress, scout_level=str(scout_level)
        )
    except api.TeamNameError as exc:
        return _ng("入力に問題があります。", exc.problems)
    except ValueError as exc:
        return _ng(f"始められませんでした({exc})")
    _game = game
    return _ok(_view())


def load(data) -> str:
    global _game
    try:
        game = api.Game.load(bytes(data))
    except api.SaveDataError as exc:
        return _ng("このファイルは読み込めませんでした。今のゲームは、そのまま残っています。", exc.problems)
    _game = game
    return _ok(_view())


def save(today: str) -> bytes:
    """セーブデータの中身(bytes)。ファイル名などは save_info で受け取る。"""
    global _saved
    _saved = _game.save(today=today)
    return _saved["data"]


_saved: dict | None = None


def save_info() -> str:
    return _ok({"file_name": _saved["file_name"], "bytes": _saved["bytes"], "status": _game.status()})


def advance(days: int) -> str:
    _game.advance(int(days))
    _game.records  # 集計も、進めた日の分だけ足しておく(成績の画面をすぐ開けるように)
    return _ok(_view())


def view() -> str:
    return _ok(_view())


def year_end() -> str:
    """年度の確定(F2。D-185)。画面の確認のあとに呼ぶ。戻り値は表示用の情報と、オフの結果の要約。
    操作する球団があるときは、オフの手続き(F3-1)が始まる(status.offseason が入る)。"""
    try:
        summary = _game.year_end()
    except ValueError as exc:
        return _ng(f"年度を確定できませんでした({exc})")
    return _ok({"view": _view(), "summary": summary})


_OFFSEASON = {
    "release": lambda a: _game.offseason_release(list(a.get("player_ids", []))),
    "next": lambda a: _game.offseason_next(),
    "advance": lambda a: _game.offseason_advance(),
    "pick": lambda a: _game.offseason_pick(str(a["player_id"])),
    "pass": lambda a: _game.offseason_pass(),
    "auto": lambda a: _game.offseason_auto(),
}


def offseason(name: str, args_json: str = "{}") -> str:
    """オフの手続きの操作(F3-1)。戻り値は手続きの画面の情報。手続きが終わったときは finished と、表示用の情報。"""
    if _game is None:
        return _ng("ゲームが始まっていません。")
    if name not in _OFFSEASON:
        return _ng(f"知らない操作です({name})")
    try:
        result = _OFFSEASON[name](json.loads(args_json or "{}"))
    except (KeyError, ValueError) as exc:
        return _ng(f"操作できませんでした({exc})")
    if isinstance(result, dict) and result.get("finished"):
        result["view_all"] = _view()
    return _ok(result)


# ---- 見る画面(公開用の関数だけ。D-114) ----
_QUERIES = {
    "stats": lambda a: _game.stats(a.get("role", "batter"), a.get("kind", "basic"), a.get("sort"), a.get("order"), bool(a.get("qualified", True)), a.get("league"), a.get("team_id"), a.get("season")),
    "year_end_preview": lambda a: _game.year_end_preview(),
    "offseason_summary": lambda a: _game.offseason_summary(a.get("year")),
    "offseason_view": lambda a: _game.offseason_view(),
    "offseason_table": lambda a: _game.offseason_table(a.get("phase", "release"), a.get("role", "batter"), a.get("kind", "basic"), a.get("sort"), a.get("order"), a.get("season")),
    "transactions": lambda a: _game.transactions(a.get("year")),
    "draft_review": lambda a: _game.draft_review(a.get("team_id"), a.get("year")),
    "player": lambda a: _game.player(a["player_id"]),
    "games_on": lambda a: _game.games_on(int(a["day"])),
    "game": lambda a: _game.game(int(a["game_no"])),
    "team": lambda a: _game.team(a["team_id"]),
    "teams": lambda a: _game.teams(),
    "baseline_info": lambda a: _game.baseline_info(),
    "sortable_keys": lambda a: api.sortable_keys(a.get("role", "batter")),
    "stadium": lambda a: _game.stadium(a["team_id"]),
}

# ---- 答え合わせ(答え合わせモードがオンのときだけ、画面が呼ぶ。D-108、D-114) ----
_ANSWERS = {
    "ability_table": lambda a: answers.ability_table(_game, a.get("role", "batter"), int(a.get("level", 1)), a.get("sort"), a.get("order", "desc"), bool(a.get("qualified", True)), a.get("league"), a.get("team_id")),
    "player_answers": lambda a: answers.player_answers(_game, a["player_id"], int(a.get("level", 1))),
    "ability_columns": lambda a: answers.columns(a.get("role", "batter"), int(a.get("level", 1))),
    "stadium_answers": lambda a: answers.stadium_answers(_game, a["team_id"], int(a.get("level", 1))),
    "offseason_answers": lambda a: answers.offseason_answers(_game, a.get("year"), int(a.get("level", 1))),
    "scouting_answers": lambda a: answers.scouting_answers(_game, a["player_id"], int(a.get("level", 1))),
    "procedure_answers": lambda a: answers.procedure_answers(_game, int(a.get("level", 1))),
    "offseason_ability_table": lambda a: answers.roster_ability_table(_game, a.get("phase", "release"), a.get("role", "batter"), int(a.get("level", 1)), a.get("sort"), a.get("order"), a.get("season")),
    "draft_review_answers": lambda a: answers.draft_review_answers(_game, a.get("team_id"), a.get("year"), int(a.get("level", 1))),
}


def _run(table: dict, name: str, args_json: str) -> str:
    if _game is None:
        return _ng("ゲームが始まっていません。")
    if name not in table:
        return _ng(f"知らない操作です({name})")
    try:
        return _ok(table[name](json.loads(args_json or "{}")))
    except (KeyError, ValueError) as exc:
        return _ng(f"表示できませんでした({exc})")


def query(name: str, args_json: str = "{}") -> str:
    if name == "metrics_guide":  # 指標の解説(ゲームがなくても見られる)
        return _ok(api.metrics_guide())
    return _run(_QUERIES, name, args_json)


def answer(name: str, args_json: str = "{}") -> str:
    return _run(_ANSWERS, name, args_json)
