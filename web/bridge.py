"""画面(web/)と操作の関数(pennant.api)をつなぐ、ごく薄い取り次ぎ役(D-107)。

裏の計算(worker.js)が、この関数を呼ぶ。やり取りは JSON の文字(セーブデータの中身だけは bytes)。
遊んでいるゲームを1つ持つ。読み込みや新規開始に失敗したときは、今のゲームをそのまま残す。
選手の非公開の情報は扱わない(pennant.api の公開用の関数だけを使う。D-108)。
"""

from __future__ import annotations

import json

from pennant import api

_game: api.Game | None = None


def _ok(value) -> str:
    return json.dumps({"ok": True, "value": value}, ensure_ascii=False)


def _ng(message: str, problems: list[str] | None = None) -> str:
    return json.dumps({"ok": False, "message": message, "problems": problems or []}, ensure_ascii=False)


def _view() -> dict:
    """画面の表示に使うもの一式(今の状況・順位表・自球団の直近の試合)。"""
    return {"status": _game.status(), "standings": _game.standings(), "recent": _game.recent_games()}


def preview(seed: int) -> str:
    return _ok(api.preview_teams(int(seed)))


def check(seed: int, names_json: str) -> str:
    return _ok(api.check_team_names(int(seed), json.loads(names_json)))


def new_game(seed: int, season_seed: int, names_json: str, my_team_index: int) -> str:
    global _game
    try:
        game = api.Game.new(int(seed), json.loads(names_json), int(my_team_index), season_seed=int(season_seed))
    except api.TeamNameError as exc:
        return _ng("入力に問題があります。", exc.problems)
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
    return _ok(_view())


def view() -> str:
    return _ok(_view())
