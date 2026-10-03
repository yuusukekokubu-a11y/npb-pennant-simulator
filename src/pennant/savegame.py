"""セーブ・ロード(実装⑥。D-100〜D-104)。

セーブデータは1つのファイル(拡張子 .sav。中身は ZIP)。メモリ上で作り、bytes で受け渡す
(ファイルへの書き出しは、保存の差し替え口 storage.py が行う。D-101)。

  manifest.json         形式の名前・バージョン番号・保存日時・セーブデータの名前・中のファイル
  state.json            シード、設定値、リーグ(球団・選手。隠し情報と状態を含む)、シーズンの状態
  logs/season-1.jsonl   1行に1試合(試合の結果と打席ログ)

読み込み(load_game)は、ZIP → manifest → バージョン(古い版は変換)→ 状態 → 設定値 → リーグ・選手 →
シーズン → 打席ログ の順に点検し、問題をすべて集めて、場所と理由を日本語で示す(SaveDataError)。
点検が済んでから、新しい状態を作って返す。今遊んでいる状態には触れない(D-102)。
"""

from __future__ import annotations

import copy
import dataclasses
import io
import json
import math
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from .abilities import BATTER, PITCHER, items_for
from .baselines import STATES, VALUE_NAMES, Baselines, BaselineSettings, load_baseline_settings, validate_baseline_settings
from .history import SeasonArchive
from .offseason import OffseasonResult, OffseasonSettings, load_offseason_settings, validate_offseason_settings
from .parkfactors import COUNT_KEYS, ParkTally, history_from_dict, history_to_dict
from .baserunning import RunnerMove
from .config import (
    ConfigError,
    GenerationConfig,
    NameParts,
    load_generation_config,
    load_name_parts,
    validate_generation_config,
    validate_name_parts,
)
from .game import GamePlateAppearance, GameResult, PitcherLine
from .game_config import load_game_config, validate_game_config
from .manager import SimpleManager
from .models import HiddenInfo, League, ParkFactors, Player, PlayerState, Team
from .newgame import check_team_name, new_league
from .pa_config import load_pa_config, validate_pa_config
from .plate_appearance import BaseOutState, OddsRatioModel, PlateAppearance
from .season import GameContext, PlayedGame, Season
from .season_config import load_season_config, validate_season_config

SAVE_FORMAT = "npb-pennant-simulator-save"
SAVE_FORMAT_VERSION = 7  # 2:自球団(画面①)。3:指標の基準値(第2弾①)。4:球場の倍率(②a)。5:球場 × シーズンの集計の履歴(②b)。6:複数年(年・シーズンの履歴・オフの結果。F2)。7:校正の定数(D-197)
ZIP_TIME = (2020, 1, 1, 0, 0, 0)  # ZIP の中の日時は固定する(保存日時は manifest にだけ入れる)
STATE_FILE = "state.json"
MANIFEST_FILE = "manifest.json"
RATING_RANGE = (-50.0, 150.0)  # 能力値として受け付ける範囲(内部では 20〜80 の外も許す。D-024)
GROWTH_TYPES = ("early", "normal", "late")
MAX_ROSTER = 70  # 支配下の上限(D-029)
MIN_ROSTER = 29  # 一軍の人数(D-029 補足)

# 古い版の変換:版 n の内容(manifest と state の組)を、版 n+1 の形に直す関数。版を1つずつ上げる


def _v1_to_v2(bundle: dict) -> dict:
    """版1には自球団の情報がない。「自球団なし」として足す。"""
    bundle["state"].setdefault("user", {"my_team_id": None})
    return bundle


def _v2_to_v3(bundle: dict) -> dict:
    """版2には指標の基準値がない。「設定ファイルの既定値を使う」として足す(D-122)。"""
    bundle["state"].setdefault("baselines", {"source": "default"})
    return bundle


def _v3_to_v4(bundle: dict) -> dict:
    """版3には球場の倍率がない。すべて 1.0(1000)として足し、これまでどおりの計算を続ける(D-138)。"""
    league = bundle["state"].get("league")
    if isinstance(league, dict) and isinstance(league.get("teams"), list):
        for td in league["teams"]:
            if isinstance(td, dict):
                td.setdefault("park", {"home_run": 1000, "babip": 1000})
    return bundle


def _v4_to_v5(bundle: dict) -> dict:
    """版4には球場 × シーズンの集計の履歴がない。履歴なし(球場補正 1.0)として足す(D-146)。"""
    bundle["state"].setdefault("park_history", [])
    return bundle


def _v5_to_v6(bundle: dict) -> dict:
    """版5には年とシーズンの履歴がない。1シーズン目・履歴なしとして足す(F2。D-189)。"""
    bundle["state"].setdefault("year", 1)
    bundle["state"].setdefault("history", [])
    bundle["state"].setdefault("offseasons", [])
    return bundle


def _v6_to_v7(bundle: dict) -> dict:
    """版6には校正の定数がない。校正なし(0)として足す(旧版の選手には校正を適用しない。D-197)。"""
    bundle["state"].setdefault("calibration", {"batter": 0.0, "pitcher": 0.0})
    return bundle


MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5, 5: _v5_to_v6, 6: _v6_to_v7}
PARK_RANGE = (100, 10000)  # 球場の倍率(千分率)として受け付ける範囲


class SaveDataError(ValueError):
    """セーブデータを読み込めないとき。problems は「場所: 理由」の一覧(日本語)。"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        shown = problems[:30]
        more = f"\n  …ほか {len(problems) - 30} 件" if len(problems) > 30 else ""
        super().__init__("セーブデータを読み込めません:\n" + "\n".join(f"  - {p}" for p in shown) + more)


@dataclass
class GameState:
    """遊んでいる状態の全体(シーズンと、リーグを作った設定値と、セーブデータの名前)。"""

    season: Season
    gen_config: GenerationConfig
    name_parts: NameParts
    name: str = ""
    my_team_id: str | None = None  # 自球団(画面で選ぶ。指紋の元には入れない)
    baselines: Baselines | None = None  # シーズンの出発点の基準値(初年度は試運転。D-121)。None は既定値
    baseline_settings: BaselineSettings | None = None  # 基準値の設定(None は設定ファイル)
    park_history: list = field(default_factory=list)  # 前のシーズンまでの、球場 × シーズンの集計(球場補正の推定用。D-146)
    year: int = 1  # 何シーズン目か(F2)
    history: list = field(default_factory=list)  # 過去シーズンの集計(SeasonArchive。古い順。F2。D-182)
    offseasons: list = field(default_factory=list)  # 年度ごとのオフの結果(OffseasonResult。F2)
    offseason_settings: OffseasonSettings | None = None  # 年度の確定の設定(None は設定ファイル)
    calibration: dict | None = None  # 新規開始時の校正の定数(役割 → 潜在能力に足す値。新人にも足す。D-197)。None は設定ファイルの値

    def __post_init__(self) -> None:
        if self.baseline_settings is None:
            self.baseline_settings = load_baseline_settings()
        if self.baselines is None:
            self.baselines = self.baseline_settings.default_baselines()
        if self.offseason_settings is None:
            self.offseason_settings = load_offseason_settings()
        if self.calibration is None:
            self.calibration = self.offseason_settings.calibration

    @property
    def league(self) -> League:
        return self.season.league


def start_game(seed: int, team_names=None, name: str = "") -> GameState:
    """新しいリーグを作り、シーズンを始める(球団名の空欄は架空の初期名。D-008)。"""
    gen, parts = load_generation_config(), load_name_parts()
    league = new_league(seed, team_names, gen, parts)
    return GameState(Season(league, seed), gen, parts, name)


def default_file_name(day: date) -> str:
    """既定のファイル名(日付だけを使う。球団名は入れない。D-100、D-104)。"""
    return f"save-{day:%Y%m%d}.sav"


# ---- 書き出し ----

def _plain(x):
    if dataclasses.is_dataclass(x):
        return {f.name: _plain(getattr(x, f.name)) for f in dataclasses.fields(x)}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    return x


def _names_data(parts: NameParts) -> dict:
    data = {"format_version": 1}
    for f in dataclasses.fields(parts):
        if f.name != "source":
            data[f.name] = list(getattr(parts, f.name))
    return data


def build_state(state: GameState) -> dict:
    """状態の JSON の中身(保存日時やセーブデータの名前は入れない)。"""
    s = state.season
    return {
        "seed": s.seed,
        "rng": {
            "initial_seed": s.seed,
            "method": "試合ごとの乱数は、シーズンのシードと試合の通し番号から SHA-256 で作る(持ち越す乱数の状態はない)",
        },
        "configs": {
            "generation": copy.deepcopy(state.gen_config.data),
            "names": _names_data(state.name_parts),
            "plate_appearance": copy.deepcopy(s.model.config.data),
            "game": copy.deepcopy(s.game_config.data),
            "season": copy.deepcopy(s.season_config.data),
            "baselines": copy.deepcopy(state.baseline_settings.data),
            "offseason": copy.deepcopy(state.offseason_settings.data),
        },
        "league": {
            "seed": s.league.seed,
            "league_names": list(s.league.league_names),
            "teams": [_plain(t) for t in s.league.teams],
        },
        "user": {"my_team_id": state.my_team_id},
        "baselines": state.baselines.to_dict(),
        "park_history": history_to_dict(state.park_history),
        "year": state.year,
        "calibration": {role: float(v) for role, v in state.calibration.items()},
        "history": [a.to_dict() for a in state.history],
        "offseasons": [o.to_dict() for o in state.offseasons],
        "season": {
            "day": s.day,
            "rotation": dict(s.rotation),
            "games": [
                {
                    "number": p.scheduled.number,
                    "rotation": p.context.rotation,
                    "fatigue": p.context.fatigue,
                    "starter_skipped": p.starter_skipped,
                }
                for p in s.played
            ],
        },
    }


def _json_bytes(data, indent=None) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":") if indent is None else None, indent=indent, allow_nan=False).encode("utf-8")


def save_game(state: GameState, saved_at: datetime | None = None) -> bytes:
    """セーブデータ(.sav の中身)を作る。saved_at を省略すると今の日時。"""
    saved_at = saved_at or datetime.now().astimezone()
    log_lines = [_json_bytes({"number": p.scheduled.number, "result": _plain(p.result)}) for p in state.season.played]
    files = {
        STATE_FILE: _json_bytes(build_state(state)),
        f"logs/season-{state.year}.jsonl": b"\n".join(log_lines) + (b"\n" if log_lines else b""),
    }
    for a in state.history:  # 打席ログを残している過去シーズン(D-189)
        if a.games is not None:
            lines = [_json_bytes({"number": p.scheduled.number, "result": _plain(p.result)}) for p in a.games]
            files[f"logs/season-{a.year}.jsonl"] = b"\n".join(lines) + (b"\n" if lines else b"")
    manifest = {
        "format": SAVE_FORMAT,
        "format_version": SAVE_FORMAT_VERSION,
        "saved_at": saved_at.isoformat(timespec="seconds"),
        "name": state.name,
        "files": sorted(files),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in [(MANIFEST_FILE, _json_bytes(manifest, indent=2))] + sorted(files.items()):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


# ---- 読み込み ----

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


def _upgrade(manifest: dict, state: dict, p: _Problems) -> tuple[dict, dict] | None:
    version = manifest.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int):
        p.add("manifest.json.format_version", f"形式のバージョン番号が整数ではありません(値: {version!r})")
        return None
    if version > SAVE_FORMAT_VERSION:
        p.add(
            "manifest.json.format_version",
            f"このセーブデータは新しい版(バージョン {version})で作られています。このプログラムが読めるのはバージョン {SAVE_FORMAT_VERSION} までです。プログラムを新しくしてください",
        )
        return None
    if version < 1:
        p.add("manifest.json.format_version", f"知らないバージョンです(値: {version})")
        return None
    bundle = {"manifest": manifest, "state": state}
    while version < SAVE_FORMAT_VERSION:
        convert = MIGRATIONS.get(version)
        if convert is None:
            p.add("manifest.json.format_version", f"バージョン {version} から {version + 1} への変換がありません")
            return None
        bundle = convert(bundle)
        version += 1
        bundle["manifest"]["format_version"] = version
    return bundle["manifest"], bundle["state"]


def read_manifest(data: bytes) -> dict:
    """セーブデータの manifest(形式・保存日時・名前)だけを読む(一覧の表示用)。"""
    p = _Problems()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = _read_json(zf.read(MANIFEST_FILE), MANIFEST_FILE, p)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise SaveDataError([f"ファイル: セーブデータ(ZIP)として読めません({exc})"]) from exc
    if p.items or not isinstance(manifest, dict):
        raise SaveDataError(p.items or ["manifest.json: まとまり({ })が必要です"])
    return manifest


def load_game(data: bytes) -> GameState:
    """セーブデータを読み込み、点検してから、新しい状態を作って返す(D-102)。"""
    p = _Problems()
    try:
        zf = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except zipfile.BadZipFile:
        raise SaveDataError(["ファイル: セーブデータ(.sav。中身は ZIP)として読めません。別のファイルか、壊れています"]) from None
    with zf:
        names = set(zf.namelist())
        raws = {}
        for name in sorted(names):
            if name == MANIFEST_FILE or name == STATE_FILE or name.startswith("logs/"):
                try:
                    raws[name] = zf.read(name)
                except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as exc:
                    p.add(name, f"取り出せません。ファイルが壊れています({exc})")
        for name in (MANIFEST_FILE, STATE_FILE):
            if name not in names:
                p.add(name, "ファイルが入っていません")
    if p.items:
        raise SaveDataError(p.items)

    manifest = _read_json(raws[MANIFEST_FILE], MANIFEST_FILE, p)
    if isinstance(manifest, dict) and manifest.get("format") != SAVE_FORMAT:
        p.add("manifest.json.format", f"このプログラムのセーブデータではありません(値: {manifest.get('format')!r})")
    state = _read_json(raws[STATE_FILE], STATE_FILE, p)
    if p.items:
        raise SaveDataError(p.items)
    if not isinstance(manifest, dict) or not isinstance(state, dict):
        raise SaveDataError(["manifest.json / state.json: まとまり({ })が必要です"])
    upgraded = _upgrade(manifest, state, p)
    if upgraded is None:
        raise SaveDataError(p.items)
    manifest, state = upgraded

    # ---- 設定値 ----
    seed = _need(state, "seed", int, "state.json", p)
    configs = _need(state, "configs", dict, "state.json", p) or {}
    validators = {
        "generation": validate_generation_config,
        "names": validate_name_parts,
        "plate_appearance": validate_pa_config,
        "game": validate_game_config,
        "season": validate_season_config,
        "offseason": validate_offseason_settings,
    }
    if "offseason" not in configs:
        configs["offseason"] = load_offseason_settings().data  # 版5以前は設定ファイルの値
    cfg = {}
    for key, validate in validators.items():
        if key not in configs:
            p.add(f"state.json.configs.{key}", "設定値がありません(必須項目です)")
            continue
        try:
            cfg[key] = validate(configs[key], f"セーブデータの設定値 {key}")
        except ConfigError as exc:
            for problem in exc.problems:
                p.add(f"state.json.configs.{key}", problem)

    # ---- リーグと選手 ----
    league_d = _need(state, "league", dict, "state.json", p) or {}
    teams_d = _need(league_d, "teams", list, "state.json.league", p) or []
    _need(league_d, "league_names", list, "state.json.league", p)
    team_ids: set[str] = set()
    player_ids: set[str] = set()
    for ti, td in enumerate(teams_d):
        where = f"state.json.league.teams[{ti}]"
        if not isinstance(td, dict):
            p.add(where, "球団の情報のまとまり({ })が必要です")
            continue
        tid = _need(td, "id", str, where, p)
        if tid in team_ids:
            p.add(f"{where}.id", f"球団の ID {tid!r} が重複しています")
        team_ids.add(tid)
        for key in ("place", "nickname", "stadium"):
            _need(td, key, str, where, p)
        _need(td, "league_index", int, where, p)
        park = _need(td, "park", dict, where, p)
        if park is not None:
            for key in ("home_run", "babip"):
                v = _need(park, key, int, f"{where}.park", p)
                if v is not None and not PARK_RANGE[0] <= v <= PARK_RANGE[1]:
                    p.add(f"{where}.park.{key}", f"球場の倍率が範囲外です(値: {v}。{PARK_RANGE[0]}〜{PARK_RANGE[1]})")
        name = td.get("display_name")
        if name is not None:
            problem = "文字列が必要です" if not isinstance(name, str) else check_team_name(name)
            if problem:
                p.add(f"{where}.display_name", problem)
        players = _need(td, "players", list, where, p) or []
        if players and not MIN_ROSTER <= len(players) <= MAX_ROSTER:
            p.add(f"{where}.players", f"選手の数が範囲外です({len(players)}人。{MIN_ROSTER}〜{MAX_ROSTER}人)")
        for pi, pd in enumerate(players):
            pw = f"{where}.players[{pi}]"
            _check_player(pd, pw, tid, p)
            pid = pd.get("id") if isinstance(pd, dict) else None
            if pid in player_ids:
                p.add(f"{pw}.id", f"選手の ID {pid!r} が重複しています")
            player_ids.add(pid)
    if len(teams_d) < 2:
        p.add("state.json.league.teams", "球団が2つ以上必要です")
    user = state.get("user", {"my_team_id": None})
    my_team_id = user.get("my_team_id") if isinstance(user, dict) else None
    if not isinstance(user, dict):
        p.add("state.json.user", "まとまり({ })が必要です")
    elif my_team_id is not None and my_team_id not in team_ids:
        p.add("state.json.user.my_team_id", f"自球団 {my_team_id!r} が、球団の一覧にありません")
    baseline_settings = None
    if "baselines" in configs:
        try:
            baseline_settings = validate_baseline_settings(configs["baselines"], "セーブデータの設定値 baselines")
        except ConfigError as exc:
            p.items.extend(f"state.json.configs.baselines: {x}" for x in exc.problems)
    baselines = _check_baselines(state.get("baselines"), baseline_settings, p)
    park_history = _check_park_history(state.get("park_history"), team_ids, p)
    year = _need(state, "year", int, "state.json", p) or 1
    if year < 1:
        p.add("state.json.year", f"1 以上の整数が必要です(値: {year!r})")
    calibration_d = _need(state, "calibration", dict, "state.json", p) or {}
    calibration = {}
    for role in ("batter", "pitcher"):
        v = calibration_d.get(role)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -30 <= v <= 30:
            p.add(f"state.json.calibration.{role}", f"-30〜30 の数が必要です(値: {v!r})")
        else:
            calibration[role] = float(v)
    history_d = _need(state, "history", list, "state.json", p) or []
    offseasons_d = _need(state, "offseasons", list, "state.json", p) or []
    if f"logs/season-{year}.jsonl" not in raws:
        p.add(f"logs/season-{year}.jsonl", "ファイルが入っていません")
    season_d = _need(state, "season", dict, "state.json", p) or {}
    day = _need(season_d, "day", int, "state.json.season", p)
    rotation = _need(season_d, "rotation", dict, "state.json.season", p)
    games_d = _need(season_d, "games", list, "state.json.season", p) or []
    if p.items:
        raise SaveDataError(p.items)

    # ---- リーグを組み立てて、シーズンを作る(日程はシードと設定値から作り直す) ----
    teams = []
    for td in teams_d:
        team = Team(
            td["id"], td["league_index"], td["place"], td["nickname"], td["stadium"],
            display_name=td.get("display_name"), park=ParkFactors(td["park"]["home_run"], td["park"]["babip"]),
        )
        team.players = [_player_from(pd) for pd in td["players"]]
        teams.append(team)
    league = League(league_d.get("seed", seed), list(league_d["league_names"]), teams)
    try:
        season = Season(
            league,
            seed,
            season_config=cfg["season"],
            game_config=cfg["game"],
            model=OddsRatioModel(cfg["plate_appearance"]),
            manager=SimpleManager(cfg["game"]),
        )
    except (ValueError, KeyError, IndexError) as exc:
        raise SaveDataError([f"state.json.league: この内容ではシーズンを作れません({exc})"]) from exc

    # ---- シーズンの状態と打席ログ ----
    total_days = season.total_days
    if not 0 <= day <= total_days:
        p.add("state.json.season.day", f"日付が範囲外です(値: {day}。0〜{total_days})")
    for tid, idx in rotation.items():
        if tid not in team_ids:
            p.add(f"state.json.season.rotation.{tid}", "知らない球団です")
        elif isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            p.add(f"state.json.season.rotation.{tid}", f"ローテーションの位置は0以上の整数にしてください(値: {idx!r})")
    expected_games = sum(len(d) for d in season.schedule.days[: max(0, min(day, total_days))])
    if len(games_d) != expected_games:
        p.add("state.json.season.games", f"{day}日目までの試合は {expected_games} 試合のはずですが、{len(games_d)} 試合あります(日付と試合が合いません)")
    log_name = f"logs/season-{year}.jsonl"
    lines = [line for line in raws[log_name].split(b"\n") if line.strip()]
    if len(lines) != len(games_d):
        p.add(log_name, f"試合の数({len(lines)})が、state.json の試合の数({len(games_d)})と合いません")
    if p.items:
        raise SaveDataError(p.items)

    schedule = season.schedule.games
    played = []
    for i, (gd, raw) in enumerate(zip(games_d, lines)):
        where = f"{log_name} の {i + 1} 行目"
        row = _read_json(raw, where, p)
        if row is None:
            continue
        try:
            number = row["number"]
            result = _game_from(row["result"])
        except (KeyError, TypeError, ValueError) as exc:
            p.add(where, f"試合の記録の形が違います({type(exc).__name__}: {exc})")
            continue
        if number != i or not isinstance(gd, dict) or gd.get("number") != i:
            p.add(where, f"試合の番号が順になっていません(値: {number!r}。{i} のはず)")
            continue
        g = schedule[i]
        if (result.home_team_id, result.away_team_id) != (g.home_id, g.away_id):
            p.add(where, f"日程と合いません(日程は {g.home_id} 対 {g.away_id}、記録は {result.home_team_id} 対 {result.away_team_id})")
            continue
        try:
            context = GameContext(dict(gd["rotation"]), {k: float(v) for k, v in gd["fatigue"].items()})
            skipped = {k: bool(v) for k, v in gd["starter_skipped"].items()}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            p.add(f"state.json.season.games[{i}]", f"試合の前の状態の形が違います({exc})")
            continue
        played.append(PlayedGame(g, result, context, skipped))
    if p.items:
        raise SaveDataError(p.items)

    season.day = day
    season.rotation = {tid: rotation.get(tid, 0) for tid in season.rotation}
    for pg in played:
        season._record(pg.result)
    season.played = played
    history = _check_history(history_d, raws, team_ids, year, p)
    offseasons = _check_offseasons(offseasons_d, p)
    if p.items:
        raise SaveDataError(p.items)
    name = manifest.get("name", "")
    return GameState(season, cfg["generation"], cfg["names"], name if isinstance(name, str) else "", my_team_id, baselines, baseline_settings, park_history, year, history, offseasons, cfg["offseason"], calibration)


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
