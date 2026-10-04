"""答え合わせ用の関数(最小のブラウザ画面②。D-108、D-114、D-115)。

公開用の関数(api.py)とは別のモジュールにする。画面は、答え合わせモードがオンのときだけ呼ぶ。
  - 段階1:現在の能力(20〜80、5刻み。範囲外は端に丸めて印を付ける)
  - 段階2:段階1に加えて、潜在能力(同じ目盛り)・成長タイプ・生成時の型
能力の表は、個人成績と同じ選び方(打者/投手、規定到達者、リーグ、チーム)で選手を選び、能力で並べ替えられる。
"""

from __future__ import annotations

import math

from .abilities import ITEM_LABELS, POSITION_LABELS, items_for
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
    """能力の表(D-115)。選手の選び方は個人成績と同じ。並べ替えは、内部の値(丸める前)で行う。

    sort には、成績の列(元の数・指標)も使える(D-132。個人成績から並び順を保ったまま切り替えたとき)。
    そのときは extra_column にその列を返し、各行の values にも値を入れる。値なしの選手は最後。
    """
    from .api import _values, column_info, is_sortable, metrics_config

    cols = columns(role, level)
    keys = [c["key"] for c in cols]
    config = metrics_config()
    extra = None
    if sort not in keys:
        if sort and is_sortable(config, role, sort):
            extra = column_info(config, role, sort)
        else:
            sort = keys[0]
    if order not in ("asc", "desc"):  # 向きの省略時:成績の指標なら「よい」向き、能力の項目なら高い順
        order = ("desc" if extra["better"] == "high" else "asc") if extra else "desc"
    growth, arche = _labels(game)
    rec = game.records.total
    group, owner = (rec.batters, rec.batter_team) if role == "batter" else (rec.pitchers, rec.pitcher_team)
    base = game.baselines()[0] if extra else None
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
        if extra:
            value = _values(config, role, group[pid], base)[sort]
            row["values"][sort] = value[1]
            key = value[0]
        row["_sort"] = key
        rows.append(row)
    present = [r for r in rows if r["_sort"] is not None]
    missing = [r for r in rows if r["_sort"] is None]
    present.sort(key=lambda r: r["player_id"])
    present.sort(key=lambda r: r["_sort"], reverse=order == "desc")
    missing.sort(key=lambda r: r["player_id"])
    rows = present + missing
    for i, r in enumerate(rows):
        r.pop("_sort")
        r["rank"] = i + 1
    sort_col = extra or next(c for c in cols if c["key"] == sort)
    return {"role": role, "level": level, "note": LEVEL_NOTE[level], "columns": cols, "sort": sort_col, "order": order, "extra_column": extra, "qualified": qualified, "rows": rows}


def stadium_answers(game: Game, team_id: str, level: int = 1) -> dict:
    """球場の真の倍率(段階1以上で見せる。D-138)。千分率の整数と、小数3桁の文字。"""
    _check_level(level)
    team = game._team(team_id)
    park = team.park
    return {
        "team_id": team_id,
        "stadium": team.stadium,
        "home_run": {"value": park.home_run, "text": f"{park.home_run / 1000:.3f}"},
        "babip": {"value": park.babip, "text": f"{park.babip / 1000:.3f}"},
        "note": "真の倍率(1.000 が平均。本塁打 1.100 なら、本塁打が平均より約1割出やすい球場)。各リーグの6球場の平均は 1.000。",
    }


def scouting_answers(game: Game, player_id: str, level: int = 1) -> dict:
    """入団時のスカウト評価と、真の能力(今の能力。段階2は潜在能力も)を並べる(F3-1。D-206)。"""
    _check_level(level)
    p = game.state.season.players[player_id]
    if p.scouting is None:
        return {"player_id": player_id, "available": False, "items": []}
    est = p.scouting["items"]
    items = []
    for item in items_for(p.role):
        if item not in est:
            continue
        row = {"key": item, "label": ITEM_LABELS[item], "estimate": scale(est[item])["text"], "current": scale(p.ratings[item])["text"], "diff": f"{p.ratings[item] - est[item]:+.1f}"}
        if level == 2:
            row["potential"] = scale(p.hidden.potential[item])["text"]
        items.append(row)
    return {"player_id": player_id, "available": True, "level": level, "entry_year": p.scouting.get("year"), "items": items, "note": "入団時の推定値と、今の真の能力の差。入団後の成長・衰退も含まれます。"}


def roster_ability_table(game: Game, phase: str, role: str = "batter", level: int = 1, sort: str | None = None, order: str | None = None, season: str | None = None) -> dict:
    """自由契約・市場の画面の「能力」の表(答え合わせ用。D-222):基本の列(ポジション・年齢・打席か投球回)+ 能力の項目。
    並び順は基本の列・能力の項目のほか、成績の指標・WAR の列も使える(そのときは extra_column で名前の隣に出す)。"""
    from .api import ROSTER_BASE_COLUMNS

    _check_level(level)
    players = game.offseason_players(phase)
    cols = columns(role, level)
    keys = [c["key"] for c in cols]
    base_keys = [c["key"] for c in ROSTER_BASE_COLUMNS[role]]
    stats_sort = sort if sort and sort not in keys and sort not in ("pos", "age") else None  # 成績・打席(投球回)で並べるときは、成績の表の順を使う
    base = game.roster_table(players, role, "basic", stats_sort, order, season)  # 基本の列の値(と、成績で並べるときの固定列)
    base_rows = {r["player_id"]: r for r in base["rows"]}
    extra = (base["extra_column"] or next(c for c in base["columns"] if c["key"] == stats_sort)) if stats_sort and stats_sort not in base_keys else None
    sort = sort or keys[0]
    if order not in ("asc", "desc"):
        order = base["order"] if stats_sort else ("desc" if sort in keys else "asc")
    growth, arche = _labels(game)
    rows = []
    for p in players:
        if p.role != role:
            continue
        b = base_rows[p.id]
        row = {k: v for k, v in b.items() if k != "values"}
        row["values"] = {k: b["values"][k] for k in base_keys}
        row["values"].update({item: scale(p.ratings[item])["text"] for item in items_for(role)})
        if level == 2:
            row["values"]["growth_type"] = growth.get(p.hidden.growth_type, p.hidden.growth_type)
            row["values"]["archetype"] = arche.get(p.hidden.archetype, p.hidden.archetype)
        if extra:
            row["values"][sort] = b["values"][sort]
        if sort in keys:
            key = row["values"][sort] if sort in ("growth_type", "archetype") else p.ratings.get(sort)
        elif sort in ("pos", "age"):
            key = list(POSITION_LABELS).index(p.position) if sort == "pos" else p.age
        else:
            key = base["rows"].index(b)  # 成績の指標で並べたときの順
        row["_sort"] = key
        rows.append(row)
    present = [r for r in rows if r["_sort"] is not None]
    missing = [r for r in rows if r["_sort"] is None]
    present.sort(key=lambda r: r["player_id"])
    reverse = order == "desc" if (sort in keys or sort in ("pos", "age")) else False  # 成績の順は base が向きを含めて並べている
    present.sort(key=lambda r: r["_sort"], reverse=reverse)
    rows = present + missing
    for r in rows:
        r.pop("_sort")
    sort_col = extra or next((c for c in cols if c["key"] == sort), None) or next(c for c in ROSTER_BASE_COLUMNS[role] if c["key"] == sort)
    return {"phase": phase, "role": role, "kind": "ability", "level": level, "note": LEVEL_NOTE[level], "columns": ROSTER_BASE_COLUMNS[role] + cols, "sort": sort_col, "order": order, "extra_column": extra, "rows": rows, "season": base["season"], "season_label": base["season_label"], "seasons": base["seasons"]}


def draft_review_answers(game: Game, team_id: str | None = None, year: int | None = None, level: int = 1) -> dict:
    """ドラフトの振り返りの答え合わせ(D-216):今の真の総合、入団時の推定値との差、実際の天井(潜在能力を入団時の分位点で判定)。
    球団ごとの差の平均と標準偏差(その球団の「見る目」の目安)と、リーグ全体の値も出す。"""
    import statistics

    from .draft import entry_summary
    from .scouting import ceiling_cuts, grade_of
    from .stats import overall, potential_overall

    _check_level(level)
    view = game.draft_review(team_id, year)
    team_id, year = view["team_id"], view["year"]
    if year is None:
        return {"available": False, "players": {}, "teams": []}
    players = {p.id: p for p in game.state.league.all_players()}
    settings = game.state.draft_settings
    # 入団時の区切りがない評価(版 8)のために、その年の入団者全体の分位点を用意する
    class_members = [players[x["player_id"]] for t in game.state.league.teams for x in game.review_entries(t.id, year) if x["player_id"] in players]
    class_cuts = ceiling_cuts(class_members, settings.ceiling_shares) if class_members else None

    def diff_of(x, p):
        sc = p.scouting or {}
        est = x.get("overall") if x.get("overall") is not None else entry_summary(sc).get("overall")
        return None if est is None else overall(p) - float(est)

    out = {}
    for x in game.review_entries(team_id, year):
        p = players.get(x["player_id"])
        if p is None:
            continue
        d = diff_of(x, p)
        cuts = (p.scouting or {}).get("cuts") or class_cuts
        row = {"overall": f"{overall(p):.1f}", "diff": None if d is None else f"{d:+.1f}", "actual_ceiling": grade_of(potential_overall(p), cuts) if cuts else "-"}
        if level == 2:
            row["potential"] = f"{potential_overall(p):.1f}"
        out[p.id] = row
    teams = []
    all_diffs = []
    for t in game.state.league.teams:
        diffs = [diff_of(x, players[x["player_id"]]) for x in game.review_entries(t.id, year) if x["player_id"] in players]
        diffs = [d for d in diffs if d is not None]
        all_diffs += diffs
        teams.append({"team_id": t.id, "team_name": t.name, "is_mine": t.id == game.state.my_team_id, "selected": t.id == team_id, "count": len(diffs), "mean": f"{statistics.fmean(diffs):+.1f}" if diffs else "-", "sd": f"{statistics.pstdev(diffs):.1f}" if len(diffs) >= 2 else "-"})
    league = {"count": len(all_diffs), "mean": f"{statistics.fmean(all_diffs):+.1f}" if all_diffs else "-", "sd": f"{statistics.pstdev(all_diffs):.1f}" if len(all_diffs) >= 2 else "-"}
    return {"available": True, "level": level, "year": year, "team_id": team_id, "players": out, "teams": teams, "league": league, "note": "差 = 今の真の総合 − 入団時の推定値(+ は期待以上、− は期待以下。入団後の成長・衰退も含みます)。実際の天井は、潜在能力の総合値を入団時の区切りで判定した段階。球団ごとの差の平均をリーグ全体と比べると、その球団の「見る目」の目安になります(差の標準偏差が小さいほど、評価のぶれが小さい)。引退・退団した選手は、今の能力がないので対象外です。"}


def procedure_answers(game: Game, level: int = 1) -> dict:
    """オフの手続きの一覧(候補・市場・自球団)の、真の総合値と潜在能力の総合値(F3-1。答え合わせ用)。"""
    _check_level(level)
    from .stats import overall, potential_overall

    proc = game.state.procedure
    if proc is None:
        return {"available": False, "players": {}}
    players = list(proc.candidates) + list(proc.market)
    if game.state.my_team_id:
        players += list(game._team(game.state.my_team_id).players)
    out = {}
    for p in players:
        row = {"overall": f"{overall(p):.1f}"}
        if level == 2:
            row["potential"] = f"{potential_overall(p):.1f}"
            row["growth_type"] = p.hidden.growth_type
        out[p.id] = row
    return {"available": True, "level": level, "players": out, "note": "真の総合値(段階 2 は潜在能力の総合値と成長タイプも)。スカウト評価との差が、見る目のずれです。"}


def offseason_answers(game: Game, year: int | None = None, level: int = 1) -> dict:
    """オフの結果の答え合わせ(F2):残った選手の能力の増減(項目ごと。隠し情報)。答え合わせモードがオンのときだけ呼ぶ。"""
    _check_level(level)
    if not game.state.offseasons:
        return {"available": False, "players": []}
    if year is None:
        year = game.state.offseasons[-1].year
    r = next((o for o in game.state.offseasons if o.year == year), None)
    if r is None:
        raise ValueError(f"{year} シーズン目のオフの結果はありません")
    names = {t.id: t.name for t in game.state.league.teams}
    players = {p.id: p for p in game.state.league.all_players()}
    rows = []
    for pid, changes in r.ability_changes.items():
        p = players.get(pid)
        if p is None:
            continue
        total = sum(changes.values()) / len(changes) if changes else 0.0
        rows.append(
            {
                "player_id": pid,
                "name": p.name,
                "team_id": p.team_id,
                "team_name": names.get(p.team_id, p.team_id),
                "role": p.role,
                "position": p.position,
                "age": r.ages.get(pid, p.age),
                "mean_change": f"{total:+.1f}",
                "items": [{"key": item, "label": ITEM_LABELS[item], "change": f"{changes[item]:+.1f}"} for item in items_for(p.role) if item in changes],
                "is_mine": p.team_id == game.state.my_team_id,
            }
        )
    rows.sort(key=lambda d: (d["team_id"], d["player_id"]))
    return {"available": True, "year": year, "level": level, "players": rows, "note": "能力の項目ごとの、年度の確定の前後の差(真の能力値の差。答え合わせ用)。年齢カーブと年ごとの揺れの合計です。"}


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
    out["preference"] = preference_rows(game, p)
    return out


def preference_rows(game: Game, p) -> list[dict]:
    """志望の重み(隠し情報。答え合わせモードのときだけ。F3-2b。D-245)。"""
    neg = game.state.negotiation_settings
    if not p.preference:
        return []
    return [{"key": k, "label": neg.label(k) if k in neg.axes else k, "weight": round(float(v), 3), "text": f"{float(v) * 100:.0f}%", "active": not (k in neg.axes and neg.axes[k].get("needs_money") and game.state.money_rule == "none")} for k, v in p.preference.items()]


def negotiation_answers(game: Game, level: int = 1) -> dict:
    """契約更改の段階の、自球団の対象選手の志望の重み(答え合わせ用。F3-2b)。"""
    _check_level(level)
    proc = game.state.procedure
    if proc is None or game.state.my_team_id is None:
        return {"available": False, "players": {}}
    team = game._team(game.state.my_team_id)
    ids = {e["player_id"] for e in proc.negotiations.values() if e["team_id"] == team.id}
    return {"available": True, "level": level, "players": {p.id: preference_rows(game, p) for p in team.players if p.id in ids}, "note": "志望の重み(合計 100%)。断られた理由は、重み × 満足度が最も低い軸から出ます。「なし」では年俸の軸は効きません。"}


def fa_answers(game: Game, level: int = 1) -> dict:
    """FA の段階の選手の志望の重み(答え合わせ用。F3-2c)。"""
    _check_level(level)
    proc = game.state.procedure
    if proc is None or not proc.fa_info:
        return {"available": False, "players": {}}
    players = {p.id: p for p in game.offseason_players("fa")}
    return {"available": True, "level": level, "players": {pid: preference_rows(game, p) for pid, p in players.items()}, "note": "志望の重み(合計 100%)。選手は、受けた提示の中から重み × 満足度の合計が一番大きい提示を選びます。"}
