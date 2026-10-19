"""画面(web/)と操作の関数(pennant.api)をつなぐ、ごく薄い取り次ぎ役(D-107)。

裏の計算(worker.js)が、この関数を呼ぶ。やり取りは JSON の文字(セーブデータの中身だけは bytes)。
遊んでいるゲームを1つ持つ。読み込みや新規開始に失敗したときは、今のゲームをそのまま残す。
見る画面は query(公開用の関数だけ。D-108)。答え合わせモードはなくした(作り直し①。D-338)。
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
    return {"status": _game.status(), "standings": _game.standings(), "recent": _game.recent_games(), "last_day": _game.last_day_games()}


def preview(seed: int) -> str:
    return _ok(api.preview_teams(int(seed)))


def check(seed: int, names_json: str) -> str:
    return _ok(api.check_team_names(int(seed), json.loads(names_json)))


def check_places(seed: int, leagues_json: str, stadiums_json: str) -> str:
    """リーグ名・球場名の欄ごとの問題(D-409)。"""
    return _ok(api.check_place_names(int(seed), json.loads(leagues_json), json.loads(stadiums_json)))


def new_game(seed: int, season_seed: int, names_json: str, my_team_index, baselines: str = "trial", progress=None, prerun_progress=None, scout_level: str = "medium", rules_json: str = "{}", chronicle=None, places_json: str = "{}") -> str:
    """新規開始。baselines は基準値の求め方(trial:試運転で求める / default:既定値)。
    progress は試運転の進み具合を知らせる関数((終わった日数, 全日数) を受け取る)。prerun_progress は事前運転の (終わった年数, 全年数)。
    rules_json は仕組みの選択肢({auto_fa, active_draft, trade, foreign_development}。D-381)。
    chronicle は事前運転の 1 年ごとの出来事を JSON の文字で受け取る関数(ロード画面の「歴史」。D-406)。
    places_json は {leagues, stadiums, my_tier}(リーグ名・球場名と自球団の資金力。D-409、D-410)。"""
    global _game
    try:
        mine = None if my_team_index is None or int(my_team_index) < 0 else int(my_team_index)
        places = json.loads(places_json or "{}")
        game = api.Game.new(
            int(seed), json.loads(names_json), mine, season_seed=int(season_seed), baselines=str(baselines), progress=progress, prerun_progress=prerun_progress, scout_level=str(scout_level), rules=json.loads(rules_json or "{}"),
            prerun_chronicle=None if chronicle is None else (lambda e: chronicle(json.dumps(e, ensure_ascii=False))),
            league_names=places.get("leagues"), stadium_names=places.get("stadiums"), my_tier=places.get("my_tier"),
        )
    except api.TeamNameError as exc:
        return _ng("入力に問題があります。", exc.problems)
    except ValueError as exc:
        return _ng(f"始められませんでした({exc})")
    _game = game
    return _ok(_view())


def template_info() -> str:
    """用意したリーグ(テンプレート)の名前・紹介とリーグのシード(D-464)。"""
    return _ok(api.template_info())


def new_from_template(season_seed: int, names_json: str, my_team_index, rules_json: str = "{}", places_json: str = "{}") -> str:
    """用意したリーグで始める(D-465)。places_json は {leagues, stadiums, my_tier}。"""
    global _game
    try:
        mine = None if my_team_index is None or int(my_team_index) < 0 else int(my_team_index)
        places = json.loads(places_json or "{}")
        game = api.Game.from_template(json.loads(names_json), mine, int(season_seed), rules=json.loads(rules_json or "{}"), league_names=places.get("leagues"), stadium_names=places.get("stadiums"), my_tier=places.get("my_tier"))
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
    "contract_release": lambda a: _game.offseason_contract_release(str(a["player_id"])),
    "stage_auto": lambda a: _game.offseason_stage_auto(),
    "fa_offer": lambda a: _game.offseason_fa_offer(str(a["player_id"]), a.get("years", 1), a.get("salary")),
    "fa_cancel": lambda a: _game.offseason_fa_cancel(str(a["player_id"])),
    "fa_close": lambda a: _game.offseason_fa_close(),
    "market_offer": lambda a: _game.offseason_market_offer(str(a["player_id"]), a.get("years", 1), a.get("salary")),
    "market_cancel": lambda a: _game.offseason_market_cancel(str(a["player_id"])),
    "market_close": lambda a: _game.offseason_market_close(),
    "foreign_offer": lambda a: _game.offseason_foreign_offer(str(a["player_id"])),
    "foreign_cancel": lambda a: _game.offseason_foreign_cancel(str(a["player_id"])),
    "foreign_close": lambda a: _game.offseason_foreign_close(),
    "promote": lambda a: _game.offseason_promote(str(a["player_id"])),
    "facility_set": lambda a: _game.set_facility(str(a.get("kind", "")), a.get("level")),
    "active_offer": lambda a: _game.active_draft_offer([str(x) for x in a.get("player_ids", [])]),
    "active_pick": lambda a: _game.active_draft_pick(str(a["player_id"])),
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


_LINEUP = {
    "view": lambda a: _game.lineup_view(),
    "set": lambda a: _game.set_lineup(list(a.get("slots", []))),
    "style": lambda a: _game.set_lineup_style(str(a.get("style", ""))),
    "auto": lambda a: _game.lineup_auto(),
}


_POLICY = {
    "set": lambda a: _game.set_team_policy(str(a["team_id"]), dict(a.get("values") or {})),
    "ai": lambda a: _game.reset_team_policy(str(a["team_id"])),
}


def team_policy(name: str, args_json: str = "{}") -> str:
    """チーム方針(D-454)。どの球団でも変えられる。戻り値はチーム方針の欄の情報。"""
    if _game is None:
        return _ng("ゲームが始まっていません。")
    if name not in _POLICY:
        return _ng(f"知らない操作です({name})")
    try:
        return _ok(_POLICY[name](json.loads(args_json or "{}")))
    except (KeyError, ValueError) as exc:
        return _ng(f"操作できませんでした({exc})")


def lineup(name: str, args_json: str = "{}") -> str:
    """自球団のスタメンと打順(D-373)。戻り値は打順の画面の情報。"""
    if _game is None:
        return _ng("ゲームが始まっていません。")
    if name not in _LINEUP:
        return _ng(f"知らない操作です({name})")
    try:
        return _ok(_LINEUP[name](json.loads(args_json or "{}")))
    except (KeyError, ValueError) as exc:
        return _ng(f"操作できませんでした({exc})")


_ROSTER = {  # 一軍登録(D-425)
    "view": lambda a: _game.roster_view(),
    "swap": lambda a: _game.roster_swap(str(a["out_id"]), str(a["in_id"])),
    "auto": lambda a: _game.roster_set_auto(bool(a.get("auto", True))),
    "pitching": lambda a: _game.roster_set_pitching(list(a.get("starters", [])), dict(a.get("roles", {}))),  # 投手の起用(D-449)
    "pitching_auto": lambda a: _game.roster_pitching_auto(),
}


def roster(name: str, args_json: str = "{}") -> str:
    """自球団の一軍登録(D-425)。戻り値は一軍登録の画面の情報。"""
    if _game is None:
        return _ng("ゲームが始まっていません。")
    if name not in _ROSTER:
        return _ng(f"知らない操作です({name})")
    try:
        return _ok(_ROSTER[name](json.loads(args_json or "{}")))
    except (KeyError, ValueError) as exc:
        return _ng(f"操作できませんでした({exc})")


_TRADE = {
    "view": lambda a: _game.trade_view(a.get("team_id")),
    "propose": lambda a: _game.trade_propose(str(a.get("team_id", "")), list(a.get("gives", [])), list(a.get("gets", []))),
    "answer": lambda a: _game.trade_offer_answer(str(a.get("offer_id", "")), bool(a.get("accept", False))),
}


def trade(name: str, args_json: str = "{}") -> str:
    """トレード(D-378)。戻り値はトレードの画面の情報(交換を持ちかけたときは結果つき)。"""
    if _game is None:
        return _ng("ゲームが始まっていません。")
    if name not in _TRADE:
        return _ng(f"知らない操作です({name})")
    try:
        return _ok(_TRADE[name](json.loads(args_json or "{}")))
    except (KeyError, ValueError) as exc:
        return _ng(f"操作できませんでした({exc})")


# ---- 見る画面(公開用の関数だけ。D-114) ----
_QUERIES = {
    "stats": lambda a: _game.stats(a.get("role", "batter"), a.get("kind", "basic"), a.get("sort"), a.get("order"), bool(a.get("qualified", True)), a.get("league"), a.get("team_id"), a.get("season")),
    "fielding_stats": lambda a: _game.fielding_stats(a.get("position", "all"), a.get("sort"), a.get("order"), bool(a.get("qualified", True)), a.get("league"), a.get("team_id"), a.get("season")),
    "year_end_preview": lambda a: _game.year_end_preview(),
    "offseason_summary": lambda a: _game.offseason_summary(a.get("year")),
    "offseason_view": lambda a: _game.offseason_view(),
    "facilities_view": lambda a: _game.facilities_view(),
    "offseason_table": lambda a: _game.offseason_table(a.get("phase", "release"), a.get("role", "batter"), a.get("kind", "basic"), a.get("sort"), a.get("order"), a.get("season")),
    "contract_table": lambda a: _game.contract_table(a.get("group", "all"), a.get("kind", "war"), a.get("sort"), a.get("order"), a.get("season"), a.get("status", "all")),
    "decision_table": lambda a: _game.decision_table(a.get("stage", "fa"), a.get("group", "all"), a.get("kind", "war"), a.get("sort"), a.get("order"), a.get("season"), a.get("show", "open")),
    "team_outlook": lambda a: _game.team_outlook(),
    "transactions": lambda a: _game.transactions(a.get("year")),
    "draft_review": lambda a: _game.draft_review(a.get("team_id"), a.get("year")),
    "record_standings": lambda a: _game.record_standings(a.get("year")),  # 記録室(D-422、D-423)
    "record_titles": lambda a: _game.record_titles(a.get("year")),
    "record_awards": lambda a: _game.record_awards(a.get("year")),  # 表彰(D-442)
    "farm_standings": lambda a: _game.farm_standings(),  # 二軍の順位表(D-446)
    "record_leaders": lambda a: _game.record_leaders(a.get("role", "batter"), a.get("key")),
    "record_entries": lambda a: _game.record_entries(a.get("kind", "draft"), a.get("team_id"), a.get("year")),
    "record_moves": lambda a: _game.record_moves(a.get("year"), a.get("team_id")),
    "budget_info": lambda a: _game.budget_info(a["team_id"]),
    "player": lambda a: _game.player(a["player_id"]),
    "games_on": lambda a: _game.games_on(int(a["day"])),
    "game": lambda a: _game.game(int(a["game_no"])),
    "team": lambda a: _game.team(a["team_id"]),
    "teams": lambda a: _game.teams(),
    "baseline_info": lambda a: _game.baseline_info(),
    "sortable_keys": lambda a: api.sortable_keys(a.get("role", "batter")),
    "stadium": lambda a: _game.stadium(a["team_id"]),
    "ability_table": lambda a: _game.ability_table(a.get("role", "batter"), a.get("sort"), a.get("order", "desc"), bool(a.get("qualified", True)), a.get("league"), a.get("team_id")),
    "ability_columns": lambda a: _game.ability_columns(a.get("role", "batter")),
    "offseason_changes": lambda a: _game.offseason_changes(a.get("year")),
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
    if name == "glossary":  # 用語集(ゲームがなくても見られる。D-307)
        return _ok(api.glossary_view())
    return _run(_QUERIES, name, args_json)

