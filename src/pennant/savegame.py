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
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime

from .baselines import Baselines, BaselineSettings, load_baseline_settings, validate_baseline_settings
from .offseason import OffseasonSettings, load_offseason_settings, validate_offseason_settings
from .parkfactors import ParkTally, history_to_dict
from .contracts import MONEY_RULES, TIERS, load_contract_settings, validate_contract_settings
from .negotiation import load_negotiation_settings, validate_negotiation_settings
from .draft import load_draft_settings, validate_draft_settings
from .config import (
    ConfigError,
    GenerationConfig,
    NameParts,
    load_generation_config,
    load_name_parts,
    validate_generation_config,
    validate_name_parts,
)
from .game_config import load_game_config, validate_game_config
from .manager import SimpleManager
from .models import League, ParkFactors, Team
from .newgame import check_team_name, new_league
from .pa_config import load_pa_config, validate_pa_config
from .plate_appearance import OddsRatioModel
from .season import GameContext, PlayedGame, Season
from .season_config import load_season_config, validate_season_config
from .save_checks import (  # RATING_RANGE・GROWTH_TYPES は、分ける前と同じく savegame からも参照できるように残す
    RATING_RANGE,
    GROWTH_TYPES,
    _Problems,
    _read_json,
    _need,
    _check_player,
    _player_from,
    _game_from,
    _check_procedure,
    _check_history,
    _check_offseasons,
    _check_park_history,
    _check_baselines,
)
from .save_migrations import MIGRATIONS

SAVE_FORMAT = "npb-pennant-simulator-save"
SAVE_FORMAT_VERSION = 12  # 2:自球団(画面①)。3:指標の基準値(第2弾①)。4:球場の倍率(②a)。5:球場 × シーズンの集計の履歴(②b)。6:複数年(年・シーズンの履歴・オフの結果。F2)。7:校正の定数(D-197)。8:オフの手続き・スカウト評価・指名の履歴(F3-1)。9:評価の 2 層化(ずれの値が共通と項目ごとの 2 つ。方式の版。D-212、D-215)。10:契約・お金のルール・予算の格差・単価の推移(F3-2a。D-230〜D-235)。11:志望の重み・更改の交渉の状態・更改の履歴の提示回数・前年の順位・交渉の設定(F3-2b。D-253)。12:FA 権の年数・FA 市場の状態・FA の設定(F3-2c。D-263)
ZIP_TIME = (2020, 1, 1, 0, 0, 0)  # ZIP の中の日時は固定する(保存日時は manifest にだけ入れる)
STATE_FILE = "state.json"
MANIFEST_FILE = "manifest.json"
MAX_ROSTER = 70  # 支配下の上限(D-029)
MIN_ROSTER = 29  # 一軍の人数(D-029 補足)


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
    scout_level: str = "medium"  # スカウト評価のずれの段階(small / medium / large。D-199)
    scout_sd: dict = field(default_factory=dict)  # 球団ごとのずれ({common, item}。設定より優先。既定は空 = 全球団同じ。D-200、D-212)
    procedure: object | None = None  # 進行中のオフの手続き(draft.OffseasonProcedure)。None なら手続き中でない(F3-1)
    transactions: list = field(default_factory=list)  # 指名・獲得・自由契約の履歴({year, phase, round, team_id, player_id, name, ...})
    draft_settings: object | None = None  # オフの手続きの設定(None は設定ファイル)
    money_rule: str = "none"  # お金のルール(none / loose / standard / strict。新規開始で選ぶ。D-230)
    budget_tiers: dict = field(default_factory=dict)  # きびしいの格差(球団 → large / medium / small。D-231)
    contract_rates: dict = field(default_factory=dict)  # 年俸の単価の推移(シーズン番号(文字)→ 1 WAR あたりの万円。D-234)
    contract_settings: object | None = None  # 契約の設定(None は設定ファイル)
    negotiation_settings: object | None = None  # 交渉(志望の判定)の設定(None は設定ファイル。F3-2b)

    def __post_init__(self) -> None:
        from .draft import load_draft_settings

        if self.draft_settings is None:
            self.draft_settings = load_draft_settings()
        if self.contract_settings is None:
            self.contract_settings = load_contract_settings()
        if self.negotiation_settings is None:
            self.negotiation_settings = load_negotiation_settings()
        if self.baseline_settings is None:
            self.baseline_settings = load_baseline_settings()
        if self.baselines is None:
            self.baselines = self.baseline_settings.default_baselines()
        if self.offseason_settings is None:
            self.offseason_settings = load_offseason_settings()
        if self.calibration is None:
            self.calibration = self.offseason_settings.calibration(self.scout_level)

    def scout_sd_of(self, team_id: str) -> dict[str, float]:
        """球団のスカウト評価のずれの標準偏差(球団ごとの値があればそれ、なければ段階の設定値)。"""
        if team_id in self.scout_sd:
            return {k: float(v) for k, v in self.scout_sd[team_id].items()}
        return self.draft_settings.level_sd(self.scout_level)

    def scout_sd_map(self) -> dict[str, dict]:
        return {t.id: self.scout_sd_of(t.id) for t in self.league.teams}

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
            "draft": copy.deepcopy(state.draft_settings.data),
            "contracts": copy.deepcopy(state.contract_settings.data),
            "negotiation": copy.deepcopy(state.negotiation_settings.data),
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
        "scout_level": state.scout_level,
        "scout_sd": {tid: {k: float(x) for k, x in v.items()} for tid, v in state.scout_sd.items()},
        "procedure": None if state.procedure is None else state.procedure.to_dict(_plain),
        "transactions": [dict(x) for x in state.transactions],
        "money_rule": state.money_rule,
        "budget_tiers": {tid: str(t) for tid, t in state.budget_tiers.items()},
        "contract_rates": {str(k): round(float(v), 4) for k, v in state.contract_rates.items()},
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
        "draft": validate_draft_settings,
        "contracts": validate_contract_settings,
        "negotiation": validate_negotiation_settings,
    }
    if "offseason" not in configs:
        configs["offseason"] = load_offseason_settings().data  # 版5以前は設定ファイルの値
    if "draft" not in configs:
        configs["draft"] = load_draft_settings().data  # 版7以前は設定ファイルの値
    if "contracts" not in configs:
        configs["contracts"] = load_contract_settings().data  # 版9以前は設定ファイルの値
    if "negotiation" not in configs:
        configs["negotiation"] = load_negotiation_settings().data  # 版10以前は設定ファイルの値
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
    scout_level = state.get("scout_level", "medium")
    if scout_level not in ("small", "medium", "large"):
        p.add("state.json.scout_level", f"small / medium / large のどれかが必要です(値: {scout_level!r})")
    scout_sd_d = state.get("scout_sd", {})
    if not isinstance(scout_sd_d, dict):
        p.add("state.json.scout_sd", "球団 → 数のまとまりが必要です")
        scout_sd_d = {}
    for tid, v in list(scout_sd_d.items()):
        if not (isinstance(v, dict) and set(v) == {"common", "item"}):
            p.add(f"state.json.scout_sd.{tid}", "共通(common)と項目ごと(item)の 2 つの数が必要です")
            scout_sd_d[tid] = {"common": 0.0, "item": 0.0}
            continue
        for key, x in v.items():
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 30:
                p.add(f"state.json.scout_sd.{tid}.{key}", f"0〜30 の数が必要です(値: {x!r})")
                scout_sd_d[tid] = {"common": 0.0, "item": 0.0}
    money_rule = state.get("money_rule", "none")
    if money_rule not in MONEY_RULES:
        p.add("state.json.money_rule", f"none / loose / standard / strict のどれかが必要です(値: {money_rule!r})")
        money_rule = "none"
    tiers_d = state.get("budget_tiers", {})
    if not isinstance(tiers_d, dict) or any(v not in TIERS for v in tiers_d.values()):
        p.add("state.json.budget_tiers", "球団 → large / medium / small のまとまりが必要です")
        tiers_d = {}
    rates_d = state.get("contract_rates", {})
    if not isinstance(rates_d, dict) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in rates_d.values()):
        p.add("state.json.contract_rates", "シーズン → 単価(0 以上の数)のまとまりが必要です")
        rates_d = {}
    transactions_d = state.get("transactions", [])
    if not isinstance(transactions_d, list) or not all(isinstance(x, dict) for x in transactions_d):
        p.add("state.json.transactions", "履歴の一覧が必要です")
        transactions_d = []
    procedure_d = state.get("procedure")
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
    history = _check_history(history_d, raws, team_ids, year + (1 if procedure_d else 0), p)  # 手続き中は、今シーズンの集計がすでに履歴にある
    offseasons = _check_offseasons(offseasons_d, p)
    procedure = _check_procedure(procedure_d, team_ids, {p_.id for p_ in season.players.values()}, p)
    if procedure is not None:
        for p_ in list(procedure.market) + list(procedure.fa_pool):  # 手放された選手と FA を宣言した選手は、終わったシーズンの成績を持つので、シーズンの選手の一覧に残す(選手のページ・通算のため)
            season.players.setdefault(p_.id, p_)
    for tid in scout_sd_d:
        if tid not in team_ids:
            p.add(f"state.json.scout_sd.{tid}", "球団の一覧にない ID です")
    for tid in tiers_d:
        if tid not in team_ids:
            p.add(f"state.json.budget_tiers.{tid}", "球団の一覧にない ID です")
    if p.items:
        raise SaveDataError(p.items)
    name = manifest.get("name", "")
    out = GameState(season, cfg["generation"], cfg["names"], name if isinstance(name, str) else "", my_team_id, baselines, baseline_settings, park_history, year, history, offseasons, cfg["offseason"], calibration, str(scout_level), {tid: {k: float(x) for k, x in v.items()} for tid, v in scout_sd_d.items()}, procedure, [dict(x) for x in transactions_d], cfg["draft"], money_rule=str(money_rule), budget_tiers={tid: str(v) for tid, v in tiers_d.items()}, contract_rates={str(k): float(v) for k, v in rates_d.items()}, contract_settings=cfg["contracts"], negotiation_settings=cfg["negotiation"])
    if any(p_.contract is None for p_ in out.league.all_players()):
        from .draft import fill_missing_contracts

        fill_missing_contracts(out)  # 版9以前のセーブデータ:契約を算定で補う(D-235)
    from .negotiation import ensure_preferences

    ensure_preferences(out.league.all_players(), out.league.seed, out.negotiation_settings)  # 版10以前:志望をシードから補う(D-253)
    if out.procedure is not None:
        ensure_preferences(list(out.procedure.candidates) + list(out.procedure.market) + list(out.procedure.fa_pool), out.league.seed, out.negotiation_settings)
    if any(p_.fa_seasons is None for p_ in out.league.all_players()):
        from .fa import initialize_seasons

        initialize_seasons(out.league, out.season.actives, out.league.seed, out.negotiation_settings, out.history, only_missing=True)  # 版11以前:FA 権の年数を補う(D-264)
    return out


