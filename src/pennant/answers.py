"""答え合わせ用の関数(最小のブラウザ画面②。D-108、D-114、D-115)。

公開用の関数(api.py)とは別のモジュールにする。画面は、答え合わせモードがオンのときだけ呼ぶ。
  - 段階1:現在の能力(20〜80、5刻み。範囲外は端に丸めて印を付ける)
  - 段階2:段階1に加えて、潜在能力(同じ目盛り)・成長タイプ・生成時の型
能力の表は、個人成績と同じ選び方(打者/投手、規定到達者、リーグ、チーム)で選手を選び、能力で並べ替えられる。
"""

from __future__ import annotations

import math

from .abilities import ITEM_LABELS, items_for
from .api import Game

LEVELS = (1, 2)
LOW, HIGH, STEP = 20, 80, 5

ITEM_DESCRIPTIONS = {
    "contact": "バットにボールを当てる力。高いほど三振が少ない",
    "eye": "ボール球を見きわめる力。高いほど四球が多い",
    "power": "打球を遠くへ飛ばす力。高いほど本塁打が多い",
    "batted_ball_quality": "打球の強さ・鋭さ。高いほどヒットになりやすい",
    "gb_fb": "打球(投手は打たせる打球)がゴロ寄りかフライ寄りか。強い・弱いではなく「型」。高いほどゴロ寄り",
    "speed": "足の速さ。ゴロのヒットや、走塁に効く",
    "baserunning": "走塁の判断のうまさ",
    "range": "守備で打球に追いつく範囲の広さ",
    "arm": "守備で投げる力(肩の強さ)",
    "fielding": "打球を確実に捕る力。高いほど失策が少ない",
    "strikeout": "三振を奪う力",
    "control": "狙ったところに投げる力。高いほど四球が少ない",
    "stuff": "球の力強さ。高いほど本塁打を打たれにくい",
    "contact_suppression": "打たれた打球をヒットにさせにくい力(効果は小さめ)",
    "stamina": "長いイニングを投げる体力",
    "recovery": "登板の疲れから回復する速さ",
    "holding": "走者を塁にくぎ付けにする力",
}
LEVEL_NOTE = {
    1: "現在の能力(20〜80。50 が一軍の平均、10点の差が「標準偏差1つ分」。5刻みで表示)",
    2: "現在の能力に加えて、潜在能力(伸びきったときの能力)・成長タイプ・生成時の型",
}


def scale(value: float) -> dict:
    """能力の値を、20〜80・5刻みの表示にする。範囲外は端に丸めて、印(over:上に外れた / under:下に外れた)を付ける。"""
    shown = min(HIGH, max(LOW, int(math.floor(value / STEP + 0.5) * STEP)))
    mark = "over" if value > HIGH else "under" if value < LOW else None
    return {"value": shown, "mark": mark, "text": f"{shown}{'+' if mark == 'over' else '-' if mark == 'under' else ''}"}


def _check_level(level: int) -> int:
    if level not in LEVELS:
        raise ValueError(f"答え合わせの段階は 1 か 2 です(値: {level!r})")
    return level


def _labels(game: Game) -> tuple[dict, dict]:
    cfg = game.state.gen_config
    growth = {k: v["label"] for k, v in cfg["aging"]["growth_types"].items()}
    arche = {k: v["label"] for k, v in cfg["batter_archetypes"].items()}
    arche.update({f"{pos}/{q}": f"{cfg['pitcher_roles'][pos]['label']}・{v['label']}" for pos in cfg["pitcher_roles"] for q, v in cfg["pitcher_qualities"].items()})
    return growth, arche


def columns(role: str, level: int) -> list[dict]:
    """能力の表の列(見出しと解説)。"""
    cols = [{"key": item, "label": ITEM_LABELS[item], "description": ITEM_DESCRIPTIONS[item], "type": "ability"} for item in items_for(role)]
    if _check_level(level) == 2:
        cols += [
            {"key": "growth_type", "label": "成長タイプ", "description": "伸び方の違い。早熟(若くしてピーク)・標準・晩成(遅れてピーク)", "type": "text"},
            {"key": "archetype", "label": "生成時の型", "description": "選手を作ったときの型(長距離砲・技巧派など)。年齢で変わらない", "type": "text"},
        ]
    return cols


def ability_table(
    game: Game,
    role: str = "batter",
    level: int = 1,
    sort: str | None = None,
    order: str = "desc",
    qualified: bool = True,
    league: int | None = None,
    team_id: str | None = None,
) -> dict:
    """能力の表(D-115)。選手の選び方は個人成績と同じ。並べ替えは、内部の値(丸める前)で行う。"""
    cols = columns(role, level)
    keys = [c["key"] for c in cols]
    sort = sort if sort in keys else keys[0]
    order = order if order in ("asc", "desc") else "desc"
    growth, arche = _labels(game)
    rec = game.records.total
    owner = rec.batter_team if role == "batter" else rec.pitcher_team
    rows = []
    for pid in game.select_players(role, qualified, league, team_id):
        p = game.state.season.players[pid]
        row = game._player_row(pid, owner[pid])
        row["values"] = {item: scale(p.ratings[item])["text"] for item in items_for(role)}
        key: object = p.ratings.get(sort)
        if level == 2:
            row["values"]["growth_type"] = growth.get(p.hidden.growth_type, p.hidden.growth_type)
            row["values"]["archetype"] = arche.get(p.hidden.archetype, p.hidden.archetype)
            if sort in ("growth_type", "archetype"):
                key = row["values"][sort]
        row["_sort"] = key
        rows.append(row)
    rows.sort(key=lambda r: r["player_id"])
    rows.sort(key=lambda r: r["_sort"], reverse=order == "desc")
    for i, r in enumerate(rows):
        r.pop("_sort")
        r["rank"] = i + 1
    sort_col = next(c for c in cols if c["key"] == sort)
    return {"role": role, "level": level, "note": LEVEL_NOTE[level], "columns": cols, "sort": sort_col, "order": order, "qualified": qualified, "rows": rows}


def player_answers(game: Game, player_id: str, level: int = 1) -> dict:
    """1人分の答え合わせ:能力の項目ごとに、現在の能力(段階2では潜在能力も)。段階2は成長タイプ・生成時の型も。"""
    _check_level(level)
    p = game.state.season.players[player_id]
    items = []
    for item in items_for(p.role):
        row = {"key": item, "label": ITEM_LABELS[item], "description": ITEM_DESCRIPTIONS[item], "current": scale(p.ratings[item])["text"]}
        if level == 2:
            row["potential"] = scale(p.hidden.potential[item])["text"]
        items.append(row)
    out = {"player_id": player_id, "level": level, "note": LEVEL_NOTE[level], "items": items}
    if level == 2:
        growth, arche = _labels(game)
        out["growth_type"] = growth.get(p.hidden.growth_type, p.hidden.growth_type)
        out["archetype"] = arche.get(p.hidden.archetype, p.hidden.archetype)
    return out
