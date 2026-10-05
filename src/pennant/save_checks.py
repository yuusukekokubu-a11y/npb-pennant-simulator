"""セーブデータの読み込みで使う点検と組み立ての部品(保守②で savegame.py から分けた。D-292)。

問題は _Problems に集めて、場所と理由を日本語で示す(D-102)。読み込みの順序は savegame.load_game にある。
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from .abilities import BATTER, PITCHER, items_for
from .baselines import BaselineSettings, Baselines, STATES, VALUE_NAMES
from .baserunning import RunnerMove
from .draft import OffseasonProcedure
from .game import GamePlateAppearance, GameResult, PitcherLine
from .history import SeasonArchive
from .models import HiddenInfo, Player, PlayerState
from .offseason import OffseasonResult
from .parkfactors import COUNT_KEYS, history_from_dict
from .plate_appearance import BaseOutState, PlateAppearance
from .season import GameContext


RATING_RANGE = (-50.0, 150.0)  # 能力値として受け付ける範囲(内部では 20〜80 の外も許す。D-024)
GROWTH_TYPES = ("early", "normal", "late")



class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, why: str) -> None:
        self.items.append(f"{where}: {why}")



def _read_json(raw: bytes, where: str, p: _Problems):
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        p.add(where, "文字コードが UTF-8 ではありません")
    except json.JSONDecodeError as exc:
        p.add(where, f"JSON として読めません({exc.lineno} 行目 {exc.colno} 文字目: {exc.msg})")
    return None



def _need(d: Any, key: str, kind, where: str, p: _Problems):
    if not isinstance(d, dict) or key not in d:
        p.add(where, f"「{key}」がありません(必須項目です)")
        return None
    v = d[key]
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if (bool not in kinds and isinstance(v, bool)) or not isinstance(v, kinds):
        p.add(f"{where}.{key}", f"形が違います(値: {str(v)[:40]!r})")
        return None
    return v



def _check_player(pd: Any, where: str, team_id: str, p: _Problems) -> None:
    if not isinstance(pd, dict):
        p.add(where, "選手の情報のまとまり({ })が必要です")
        return
    for key, kind in (("id", str), ("family_name", str), ("given_name", str), ("age", int), ("role", str), ("position", str)):
        _need(pd, key, kind, where, p)
    role = pd.get("role")
    if role not in (BATTER, PITCHER):
        p.add(f"{where}.role", f"batter か pitcher にしてください(値: {role!r})")
        return
    age = pd.get("age")
    if isinstance(age, int) and not isinstance(age, bool) and not 15 <= age <= 60:
        p.add(f"{where}.age", f"年齢が範囲外です(値: {age}。15〜60)")
    if pd.get("team_id") != team_id:
        p.add(f"{where}.team_id", f"所属の球団 {pd.get('team_id')!r} が、入っている球団 {team_id!r} と違います")
    sc = pd.get("scouting")
    if sc is not None and not (isinstance(sc, dict) and isinstance(sc.get("items"), dict) and isinstance(sc.get("ceiling"), str)):
        p.add(f"{where}.scouting", "入団時のスカウト評価の形が違います")
    ct = pd.get("contract")
    if ct is not None and not (isinstance(ct, dict) and isinstance(ct.get("salary"), int) and not isinstance(ct.get("salary"), bool) and ct["salary"] >= 0 and isinstance(ct.get("until"), int) and isinstance(ct.get("history", []), list)):
        p.add(f"{where}.contract", "契約の形が違います(年俸は 0 以上の整数、満了シーズンは整数)")
    pref = pd.get("preference")
    if pref is not None and not (isinstance(pref, dict) and pref and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for k, v in pref.items())):
        p.add(f"{where}.preference", "志望の形が違います(軸 → 0 以上の重み)")
    fs = pd.get("fa_seasons")
    if fs is not None and not (isinstance(fs, int) and not isinstance(fs, bool) and 0 <= fs <= 60):
        p.add(f"{where}.fa_seasons", "FA 権の年数は 0〜60 の整数にしてください")
    items = items_for(role)
    for group in ("ratings",):
        ratings = _need(pd, group, dict, where, p)
        if ratings is None:
            continue
        for item in items:
            v = ratings.get(item)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                p.add(f"{where}.ratings.{item}", f"能力値がない、または数ではありません(値: {v!r})")
            elif not RATING_RANGE[0] <= v <= RATING_RANGE[1]:
                p.add(f"{where}.ratings.{item}", f"能力値が範囲外です(値: {v}。{RATING_RANGE[0]:g}〜{RATING_RANGE[1]:g})")
        extra = sorted(set(ratings) - set(items))
        if extra:
            p.add(f"{where}.ratings", f"この役割にない能力があります: {', '.join(extra)}")
    hidden = _need(pd, "hidden", dict, where, p)
    if hidden is not None:
        pot = _need(hidden, "potential", dict, f"{where}.hidden", p)
        if pot is not None and sorted(pot) != sorted(items):
            p.add(f"{where}.hidden.potential", "潜在能力の項目が、能力値の項目とそろっていません")
        if hidden.get("growth_type") not in GROWTH_TYPES:
            p.add(f"{where}.hidden.growth_type", f"early / normal / late のどれかにしてください(値: {hidden.get('growth_type')!r})")
        _need(hidden, "archetype", str, f"{where}.hidden", p)
        _need(hidden, "ability_drift", dict, f"{where}.hidden", p)
    st = _need(pd, "state", dict, where, p)
    if st is not None:
        for key in ("form", "fatigue"):
            v = st.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                p.add(f"{where}.state.{key}", f"数が必要です(値: {v!r})")
        if isinstance(st.get("fatigue"), (int, float)) and st["fatigue"] < 0:
            p.add(f"{where}.state.fatigue", f"疲労はマイナスにできません(値: {st['fatigue']})")



def _player_from(pd: dict) -> Player:
    h = pd["hidden"]
    return Player(
        id=pd["id"],
        family_name=pd["family_name"],
        given_name=pd["given_name"],
        age=pd["age"],
        role=pd["role"],
        position=pd["position"],
        bats=pd.get("bats"),
        throws=pd.get("throws"),
        ratings=dict(pd["ratings"]),
        hidden=HiddenInfo(dict(h["potential"]), h["growth_type"], h["archetype"], dict(h["ability_drift"])),
        state=PlayerState(form=pd["state"]["form"], fatigue=pd["state"]["fatigue"]),
        team_id=pd.get("team_id"),
        origin=pd.get("origin"),
        scouting=copy.deepcopy(pd.get("scouting")),
        contract=copy.deepcopy(pd.get("contract")),
        preference=None if pd.get("preference") is None else {str(k): float(v) for k, v in pd["preference"].items()},
        fa_seasons=pd.get("fa_seasons", 0),
    )



def _game_from(d: dict) -> GameResult:
    log = []
    for x in d["log"]:
        pa = dict(x["pa"])
        pa["base_out"] = BaseOutState(**pa["base_out"])
        row = dict(x)
        row["base_out"] = BaseOutState(**x["base_out"])
        row["pa"] = PlateAppearance(**pa)
        row["moves"] = [RunnerMove(**m) for m in x["moves"]]
        log.append(GamePlateAppearance(**row))
    g = dict(d)
    g["log"] = log
    g["pitchers"] = [PitcherLine(**line) for line in d["pitchers"]]
    g["lineups"] = {k: [tuple(s) for s in v] for k, v in d["lineups"].items()}
    g["line"] = {k: list(v) for k, v in d["line"].items()}
    return GameResult(**g)



def _check_procedure(d, team_ids: set, league_ids: set, p: _Problems):
    """オフの手続きの状態の検証(F3-1)。候補・市場の選手は、所属なし(team_id は None)で点検する。"""
    if d is None:
        return None
    where = "state.json.procedure"
    if not isinstance(d, dict):
        p.add(where, "手続きのまとまり({ })が必要です")
        return None
    try:
        if d["phase"] not in ("renewal", "release", "fa", "draft", "market", "done"):
            p.add(f"{where}.phase", f"段階が正しくありません(値: {d['phase']!r})")
        for i, tid in enumerate(d["order"]):
            if tid not in team_ids:
                p.add(f"{where}.order[{i}]", "球団の一覧にない ID です")
        seen = set()
        for key in ("candidates", "market", "fa_pool"):
            for i, pd in enumerate(d.get(key, [])):
                _check_player(pd, f"{where}.{key}[{i}]", None, p)
                pid = pd.get("id") if isinstance(pd, dict) else None
                if pid in seen or pid in league_ids:
                    p.add(f"{where}.{key}[{i}].id", f"選手の ID {pid!r} が重複しています")
                seen.add(pid)
        negs = d.get("negotiations", {})
        if not isinstance(negs, dict):
            p.add(f"{where}.negotiations", "選手 ID → 交渉のまとまりが必要です")
        else:
            for pid, e in negs.items():
                w = f"{where}.negotiations.{pid}"
                if not isinstance(e, dict) or e.get("status") not in ("pending", "accepted", "released", "declared") or not isinstance(e.get("offers"), list) or not isinstance(e.get("auto_salary"), int) or e.get("team_id") not in team_ids:
                    p.add(w, "交渉の形が違います(状態は pending / accepted / released / declared、提示の一覧、自動案の年俸、球団)")
                    continue
                for j, o in enumerate(e["offers"]):
                    if not (isinstance(o, dict) and isinstance(o.get("years"), int) and isinstance(o.get("salary"), int) and isinstance(o.get("accepted"), bool)):
                        p.add(f"{w}.offers[{j}]", "提示の形が違います(年数・年俸は整数、受けたかは真偽)")
                if e["status"] == "pending" and e["player_id"] not in league_ids:
                    p.add(w, "交渉中の選手が、リーグにいません")
        if p.items:
            return None
        return OffseasonProcedure.from_dict(d, _player_from)
    except (KeyError, TypeError, ValueError) as exc:
        p.add(where, f"手続きの形が違います({type(exc).__name__}: {exc})")
        return None



def _check_history(history_d: list, raws: dict, team_ids: set, year: int, p: _Problems) -> list:
    """過去シーズンの集計の検証(F2。D-182)。打席ログが残っているシーズンは、試合の結果も読み込む。"""
    out = []
    seen = set()
    for i, d in enumerate(history_d):
        where = f"state.json.history[{i}]"
        if not isinstance(d, dict):
            p.add(where, "シーズンの集計のまとまり({ })が必要です")
            continue
        try:
            y = int(d["year"])
            if y in seen or y >= year or y < 1:
                p.add(where, f"シーズンの番号が正しくありません(値: {y}。1〜{year - 1} で重複なし)")
                continue
            seen.add(y)
            for tid in d["records"]["teams"]:
                if tid not in team_ids:
                    p.add(f"{where}.records.teams.{tid}", "球団の一覧にない ID です")
            games = None
            if d.get("has_games"):
                log_name = f"logs/season-{y}.jsonl"
                if log_name not in raws:
                    p.add(log_name, "ファイルが入っていません(履歴に打席ログがあることになっています)")
                else:
                    games = []
                    for n, raw in enumerate(line for line in raws[log_name].split(b"\n") if line.strip()):
                        row = _read_json(raw, f"{log_name} の {n + 1} 行目", p)
                        if row is None:
                            continue
                        games.append(_archived_game(row))
            out.append(SeasonArchive.from_dict(d, games))
        except (KeyError, TypeError, ValueError) as exc:
            p.add(where, f"シーズンの集計の形が違います({type(exc).__name__}: {exc})")
    out.sort(key=lambda a: a.year)
    return out



def _archived_game(row: dict):
    """過去シーズンの試合(打席ログつき)。日程は持たないので、試合の番号と結果だけを持つ。"""
    from .season import PlayedGame, ScheduledGame

    result = _game_from(row["result"])
    scheduled = ScheduledGame(int(row["number"]), 0, 0, result.home_team_id, result.away_team_id)
    return PlayedGame(scheduled, result, GameContext({}, {}), {})



def _check_offseasons(offseasons_d: list, p: _Problems) -> list:
    out = []
    for i, d in enumerate(offseasons_d):
        try:
            out.append(OffseasonResult.from_dict(d))
        except (KeyError, TypeError, ValueError) as exc:
            p.add(f"state.json.offseasons[{i}]", f"オフの結果の形が違います({type(exc).__name__}: {exc})")
    return out



def _check_park_history(d, team_ids: set, p) -> list:
    """球場 × シーズンの集計の履歴の検証(D-146)。"""
    where = "state.json.park_history"
    if d is None:
        p.add(where, "値がありません(必須項目です)")
        return []
    if not isinstance(d, list):
        p.add(where, "リスト([ ])が必要です")
        return []
    for i, season in enumerate(d):
        sw = f"{where}[{i}]"
        if not isinstance(season, dict) or not isinstance(season.get("parks"), dict):
            p.add(sw, "シーズンの集計のまとまり({ season, parks })が必要です")
            continue
        for tid, t in season["parks"].items():
            if tid not in team_ids:
                p.add(f"{sw}.parks.{tid}", "球団の一覧にない ID です")
            for side in ("home", "away"):
                counts = t.get(side) if isinstance(t, dict) else None
                if not isinstance(counts, dict):
                    p.add(f"{sw}.parks.{tid}.{side}", "まとまり({ })が必要です")
                    continue
                for key in COUNT_KEYS:
                    v = counts.get(key)
                    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                        p.add(f"{sw}.parks.{tid}.{side}.{key}", f"0 以上の整数が必要です(値: {v!r})")
    if p.items:
        return []
    return history_from_dict(d)



def _check_baselines(d, settings: BaselineSettings | None, p) -> Baselines | None:
    """基準値の検証。source が default だけのものは、設定ファイルの既定値を使う(None を返す)。"""
    where = "state.json.baselines"
    if d is None:
        p.add(where, "値がありません(必須項目です)")
        return None
    if not isinstance(d, dict):
        p.add(where, "まとまり({ })が必要です")
        return None
    if set(d) == {"source"}:
        return None
    values = d.get("values")
    if not isinstance(values, dict):
        p.add(f"{where}.values", "まとまり({ })が必要です")
        return None
    for k in VALUE_NAMES:
        if k not in values:
            p.add(f"{where}.values.{k}", "値がありません(必須項目です)")
    re24 = d.get("re24")
    if re24 is not None and (not isinstance(re24, list) or len(re24) != STATES):
        p.add(f"{where}.re24", f"{STATES}個の値のリストが必要です")
    try:
        b = Baselines.from_dict(d)
    except (TypeError, ValueError, ZeroDivisionError, KeyError, AttributeError) as exc:
        p.add(where, f"基準値の形が違います({exc})")
        return None
    return b
