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
from .contracts import ALL_TIERS, load_contract_settings, validate_contract_settings
from .negotiation import load_negotiation_settings, validate_negotiation_settings
from .draft import load_draft_settings, validate_draft_settings
from .trade import load_trade_settings, validate_trade_settings
from .facilities import load_facility_settings, set_initial, validate_facility_settings
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
FIRST_REBUILT_VERSION = 16  # 作り直しの途中の版。これより前は変換せずに読まない(D-344、D-362、D-371)
SAVE_FORMAT_VERSION = 27  # 2:自球団(画面①)。3:指標の基準値(第2弾①)。4:球場の倍率(②a)。5:球場 × シーズンの集計の履歴(②b)。6:複数年(年・シーズンの履歴・オフの結果。F2)。7:校正の定数(D-197)。8:オフの手続き・スカウト評価・指名の履歴(F3-1)。9:評価の 2 層化(ずれの値が共通と項目ごとの 2 つ。方式の版。D-212、D-215)。10:契約・お金のルール・予算の格差・単価の推移(F3-2a。D-230〜D-235)。11:志望の重み・更改の交渉の状態・更改の履歴の提示回数・前年の順位・交渉の設定(F3-2b。D-253)。12:FA 権の年数・FA 市場の状態・FA の設定(F3-2c。D-263)。13:市場の提示の状態(①b。D-300)。14:見せる能力・球種・伸びやすさ(作り直し①。D-344、D-349〜D-352。13 以前は読まない)。15:お金のルールをなくし、球団の規模と資金(作り直し②。D-353、D-362。14 以前は読まない)。16:作り直し③(志望をなくし、ドラフトの入札と A〜E の評価。D-371)。17:自球団の打順の型と自分で組んだスタメン(D-373)。18:特殊能力(D-374)。19:育成選手と外国人の区分・外国人市場(D-375〜D-377)。20:トレード(D-378、D-379)。21:仕組みの選択肢と現役ドラフト(D-380〜D-382)。22:設備(D-383、D-385)。23:FA 権を取ったことがある印(自動 FA。D-390、D-393)。24:球団の規模に超大量(自球団の資金力。D-410)。25:調子と一軍の入れ替え(調子を始めた日・外した日・AI に任せる。一軍の顔ぶれは移り変わりの記録から。D-424〜D-426)。26:盗塁・暴投・捕逸(試合の記録の events、盗塁などを始めた日、打順の計画の盗塁の作戦。D-427〜D-433)。27:143 試合の日程(オールスターの結果、履歴の交流戦とオールスター。版 26 のシーズンは 125 試合のまま。D-435〜D-438)


def _saved_name_problem(value) -> str | None:
    """セーブデータの中のリーグ名・球場名の点検(D-409):文字列で、空でなく、球団名と同じ点検に通ること。"""
    if not isinstance(value, str):
        return "文字列が必要です"
    if not value.strip():
        return "空の名前は使えません"
    return check_team_name(value)
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
    budget_tiers: dict = field(default_factory=dict)  # 球団の規模(球団 → large / medium / small。D-353)
    team_funds: dict = field(default_factory=dict)  # 球団の資金(球団 → 万円。年俸の合計の上限。毎年のオフの最初に増減する。D-353、D-354)
    contract_rates: dict = field(default_factory=dict)  # 年俸の単価の推移(シーズン番号(文字)→ 1 WAR あたりの万円。D-234)
    contract_settings: object | None = None  # 契約の設定(None は設定ファイル)
    negotiation_settings: object | None = None  # FA で選手が選ぶ点数と FA の設定(None は設定ファイル。D-365)
    trade_settings: object | None = None  # トレードの設定(None は設定ファイル。D-378)
    rules: object | None = None  # 仕組みの選択肢(rules.GameRules。None は既定。新規開始のときだけ決める。D-381)
    facility_settings: object | None = None  # 設備の設定(None は設定ファイル。D-383)

    def __post_init__(self) -> None:
        from .draft import load_draft_settings

        if self.draft_settings is None:
            self.draft_settings = load_draft_settings()
        if getattr(self.draft_settings, "gen_config", None) is None and getattr(self, "gen_config", None) is not None:
            # AI の判断が人数の目安を見るための生成の設定(手続きの開始時にも入れる)。読み込んだ状態でも同じにして、
            # 保存して読み込んでも同じ結果にする
            object.__setattr__(self.draft_settings, "gen_config", self.gen_config)
        if self.contract_settings is None:
            self.contract_settings = load_contract_settings()
        if self.negotiation_settings is None:
            self.negotiation_settings = load_negotiation_settings()
        if self.trade_settings is None:
            self.trade_settings = load_trade_settings()
        if self.facility_settings is None:
            self.facility_settings = load_facility_settings()
        if self.rules is None:
            from .rules import DEFAULT_RULES

            self.rules = DEFAULT_RULES
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
        if f.name != "source" and not (f.name.startswith("foreign_") and not getattr(parts, f.name)):  # 外国人の名前の部品は、あるときだけ(D-376)
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
            "trade": copy.deepcopy(state.trade_settings.data),
            "facilities": copy.deepcopy(state.facility_settings.data),
        },
        "league": {
            "seed": s.league.seed,
            "league_names": list(s.league.league_names),
            "teams": [_plain(t) for t in s.league.teams],
        },
        "user": {"my_team_id": state.my_team_id},
        "rules": state.rules.to_dict(),  # 仕組みの選択肢(D-381。版 21)
        "baselines": state.baselines.to_dict(),
        "park_history": history_to_dict(state.park_history),
        "year": state.year,
        "calibration": {role: float(v) for role, v in state.calibration.items()},
        "scout_level": state.scout_level,
        "scout_sd": {tid: {k: float(x) for k, x in v.items()} for tid, v in state.scout_sd.items()},
        "procedure": None if state.procedure is None else state.procedure.to_dict(_plain),
        "transactions": [dict(x) for x in state.transactions],
        "budget_tiers": {tid: str(t) for tid, t in state.budget_tiers.items()},
        "team_funds": {tid: int(v) for tid, v in state.team_funds.items()},
        "contract_rates": {str(k): round(float(v), 4) for k, v in state.contract_rates.items()},
        "history": [a.to_dict() for a in state.history],
        "offseasons": [o.to_dict() for o in state.offseasons],
        "season": {
            "day": s.day,
            "rotation": dict(s.rotation),
            "lineup_plans": {tid: _plain_plan(plan) for tid, plan in s.lineup_plans.items()},
            "trades": [dict(x) for x in s.trades],  # シーズン中のトレード(D-378。版 20)
            "trade_offers": [dict(x) for x in s.trade_offers],
            "active_history": {tid: [dict(h) for h in hist] for tid, hist in s.active_history.items()},
            "condition": {"start": int(s.conditions.start), **({"legacy": True, "legacy_form": dict(s.legacy_form)} if s.legacy_roster else {})},  # 調子(D-424。版 25)
            "demoted": {pid: int(d) for pid, d in s.demoted.items()},  # 一軍から外した日(D-425)
            "roster_auto": {tid: bool(v) for tid, v in s.roster_auto.items()},
            "running_start": int(s.running_start),  # 盗塁などを始めた日(D-433。版 26)
            "allstar": [dict(x) for x in s.allstar],  # オールスターの結果(D-436。版 27)
            "games": [
                {
                    "number": p.scheduled.number,
                    "rotation": p.context.rotation,
                    "fatigue": p.context.fatigue,
                    "starter_skipped": p.starter_skipped,
                    **({"lineups": {tid: _plain_plan(x) for tid, x in p.context.lineups.items()}} if p.context.lineups else {}),
                }
                for p in s.played
            ],
        },
    }


def _plain_plan(plan: dict) -> dict:
    """打順の計画(D-373)を保存する形に。"""
    out = {"style": str(plan.get("style") or "power"), "slots": None if plan.get("slots") is None else [[str(pid), str(pos)] for pid, pos in plan["slots"]]}
    if plan.get("steal") and plan["steal"] != "normal":
        out["steal"] = str(plan["steal"])  # 盗塁の作戦(D-429)
    if plan.get("set_day") is not None:
        out["set_day"] = int(plan["set_day"])
    if plan.get("notice"):
        out["notice"] = {"day": int(plan["notice"]["day"]), "reason": str(plan["notice"]["reason"])}
    return out


def _check_plan(d, where: str, team_players: set, p) -> dict | None:
    """打順の計画の点検(D-373):型は決まったもの、スタメンは 9 つの枠に 1 人ずつ、そのチームの選手。"""
    from .game_config import STEAL_STRATEGIES
    from .manager import LINEUP_POSITIONS, LINEUP_STYLES

    if not isinstance(d, dict) or d.get("style") not in LINEUP_STYLES:
        p.add(where, f"打順の型が正しくありません(使えるもの: {' / '.join(LINEUP_STYLES)})")
        return None
    slots = d.get("slots")
    if slots is not None:
        ok = isinstance(slots, list) and len(slots) == len(LINEUP_POSITIONS) and all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x) for x in slots)
        if not ok or sorted(pos for _, pos in slots) != sorted(LINEUP_POSITIONS) or len({pid for pid, _ in slots}) != len(slots) or any(pid not in team_players for pid, _ in slots):
            p.add(f"{where}.slots", "スタメンの形が違います(9 つの守備位置に 1 人ずつ、そのチームの別々の選手)")
            return None
    out = {"style": d["style"], "slots": None if slots is None else [[pid, pos] for pid, pos in slots]}
    steal = d.get("steal", "normal")
    if steal not in STEAL_STRATEGIES:
        p.add(f"{where}.steal", f"盗塁の作戦が正しくありません(使えるもの: {' / '.join(STEAL_STRATEGIES)})")
        return None
    if steal != "normal":
        out["steal"] = steal
    if isinstance(d.get("set_day"), int):
        out["set_day"] = d["set_day"]
    n = d.get("notice")
    if isinstance(n, dict) and isinstance(n.get("day"), int) and n.get("reason") in ("review", "roster"):
        out["notice"] = {"day": n["day"], "reason": n["reason"]}
    return out


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
    if version < FIRST_REBUILT_VERSION:
        p.add(
            "manifest.json.format_version",
            f"このセーブデータは作り直しの前の版(バージョン {version})で作られているため、使えません(能力・お金・FA とドラフトの仕組みが変わりました)。新しく始めてください",
        )
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


def _restore_roster(season: Season, season_d: dict, p) -> None:
    """調子と一軍の入れ替え(D-424〜D-426):調子を始めた日・外した日・AI に任せる・一軍の顔ぶれ(移り変わりの記録の最後)。"""
    from .condition import Conditions

    cond = season_d.get("condition") or {}
    demoted = season_d.get("demoted") or {}
    auto = season_d.get("roster_auto") or {}
    start = cond.get("start", 0) if isinstance(cond, dict) else None
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        p.add("state.json.season.condition.start", f"調子を始めた日は 0 以上の整数にしてください(値: {start!r})")
        return
    if not (isinstance(demoted, dict) and all(isinstance(d, int) and not isinstance(d, bool) and d >= 0 for d in demoted.values())):
        p.add("state.json.season.demoted", "一軍から外した日の形が違います")
        return
    if not (isinstance(auto, dict) and all(tid in season.teams and isinstance(v, bool) for tid, v in auto.items())):
        p.add("state.json.season.roster_auto", "AI に任せるかの形が違います(球団 → true / false)")
        return
    legacy_form = cond.get("legacy_form") or {}
    try:
        season.legacy_form = {str(k): float(v) for k, v in legacy_form.items()}
    except (TypeError, ValueError, AttributeError):
        p.add("state.json.season.condition.legacy_form", "好調・不調の形が違います")
        return
    season.legacy_roster = bool(cond.get("legacy", False))
    season.conditions = Conditions(season.seed, season.conditions.cfg, start)
    season.demoted = {str(k): int(v) for k, v in demoted.items()}
    season.roster_auto = {str(k): bool(v) for k, v in auto.items()}
    running = season_d.get("running_start", 0)  # 版 25 以前は変換で入る(D-433)
    if isinstance(running, bool) or not isinstance(running, int) or running < 0:
        p.add("state.json.season.running_start", f"盗塁などを始めた日は 0 以上の整数にしてください(値: {running!r})")
        return
    season.running_start = running
    stars = season_d.get("allstar", [])
    held = sum(1 for d in season.schedule.allstar_days if d < season.day)
    if not (isinstance(stars, list) and all(isinstance(x, dict) and x.get("number") == i for i, x in enumerate(stars))) or len(stars) != held:
        p.add("state.json.season.allstar", f"オールスターの結果の形が違います({held} 試合分の結果が、回の順に必要です)")
        return
    season.allstar = [dict(x) for x in stars]
    for tid, team in season.teams.items():
        hist = season.active_history.get(tid)
        if hist and not season.is_over:
            season.actives[tid] = season.active_on(tid, season.day)
        elif season.legacy_roster:  # 版 24:今までの選び方(総合だけ)
            season.actives[tid] = season.manager.select_active(team)
    season.apply_conditions()


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
        "trade": validate_trade_settings,
        "facilities": validate_facility_settings,
    }
    if "offseason" not in configs:
        configs["offseason"] = load_offseason_settings().data  # 版5以前は設定ファイルの値
    if "draft" not in configs:
        configs["draft"] = load_draft_settings().data  # 版7以前は設定ファイルの値
    if "contracts" not in configs:
        configs["contracts"] = load_contract_settings().data  # 版9以前は設定ファイルの値
    if "negotiation" not in configs:
        configs["negotiation"] = load_negotiation_settings().data  # 版10以前は設定ファイルの値
    if "trade" not in configs:
        configs["trade"] = load_trade_settings().data  # 版19以前は設定ファイルの値(D-379)
    if "facilities" not in configs:
        configs["facilities"] = load_facility_settings().data  # 版21以前は設定ファイルの値(D-385)
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
    for li, ln in enumerate(_need(league_d, "league_names", list, "state.json.league", p) or []):  # 入力されたリーグ名も点検(D-409)
        problem = _saved_name_problem(ln)
        if problem:
            p.add(f"state.json.league.league_names[{li}]", problem)
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
        if isinstance(td.get("stadium"), str) and _saved_name_problem(td["stadium"]):  # 入力された球場名も、球団名と同じ点検(D-409)
            p.add(f"{where}.stadium", _saved_name_problem(td["stadium"]))
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
        fac = td.get("facilities", {})  # 設備の段階(D-383。版 22)
        if not (isinstance(fac, dict) and all(k in ("practice", "medical", "development") and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5 for k, v in fac.items())):
            p.add(f"{where}.facilities", "設備の段階の形が違います(practice / medical / development → 1〜5 の整数)")
        dev = td.get("development", [])
        if not isinstance(dev, list):
            p.add(f"{where}.development", "育成選手の一覧(リスト)が必要です")
            dev = []
        for pi, pd in enumerate(dev):
            pw = f"{where}.development[{pi}]"
            _check_player(pd, pw, tid, p)
            if isinstance(pd, dict) and not (isinstance(pd.get("dev_seasons"), int) and not isinstance(pd.get("dev_seasons"), bool) and pd["dev_seasons"] >= 0):
                p.add(f"{pw}.dev_seasons", "育成選手には、育成で過ごしたシーズン数(0 以上の整数)が必要です")
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
    tiers_d = state.get("budget_tiers", {})
    if not isinstance(tiers_d, dict) or any(v not in ALL_TIERS for v in tiers_d.values()):
        p.add("state.json.budget_tiers", "球団 → large / medium / small / huge のまとまりが必要です")
        tiers_d = {}
    funds_d = state.get("team_funds", {})
    if not isinstance(funds_d, dict) or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in funds_d.values()):
        p.add("state.json.team_funds", "球団 → 資金(0 以上の整数。万円)のまとまりが必要です")
        funds_d = {}
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
        team.development = [_player_from(pd) for pd in td.get("development", [])]  # 版 18 以前はない(D-377)
        team.facilities = {str(k): int(v) for k, v in (td.get("facilities") or {}).items()}  # 版 21 以前はない(読み込みの最後に規模から補う。D-385)
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
            year=year,
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
            for tid, x in (gd.get("lineups") or {}).items():
                plan = _check_plan(x, f"state.json.season.games[{i}].lineups.{tid}", set(season.players) if tid in season.teams else set(), p)  # トレードで今は別の球団の選手もいる(D-378)
                if plan is not None:
                    context.lineups[tid] = plan
            skipped = {k: bool(v) for k, v in gd["starter_skipped"].items()}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            p.add(f"state.json.season.games[{i}]", f"試合の前の状態の形が違います({exc})")
            continue
        played.append(PlayedGame(g, result, context, skipped))
    if p.items:
        raise SaveDataError(p.items)

    season.day = day
    season.rotation = {tid: rotation.get(tid, 0) for tid in season.rotation}
    plans_d = season_d.get("lineup_plans") or {}  # 版 16 にはない(打順の計画なし)
    for tid, x in (plans_d.items() if isinstance(plans_d, dict) else []):
        if tid not in season.teams:
            p.add(f"state.json.season.lineup_plans.{tid}", "知らない球団です")
            continue
        plan = _check_plan(x, f"state.json.season.lineup_plans.{tid}", {pl.id for pl in season.teams[tid].players}, p)
        if plan is not None:
            season.lineup_plans[tid] = plan
    trades_d = season_d.get("trades") or []  # 版 19 以前はない(トレードなし。D-379)
    offers_d = season_d.get("trade_offers") or []
    hist_d = season_d.get("active_history") or {}
    if not (isinstance(trades_d, list) and all(isinstance(x, dict) and x.get("team_a") in season.teams and x.get("team_b") in season.teams for x in trades_d)):
        p.add("state.json.season.trades", "トレードの記録の形が違います")
    elif not (isinstance(offers_d, list) and all(isinstance(x, dict) and x.get("team_id") in season.teams for x in offers_d)):
        p.add("state.json.season.trade_offers", "トレードの提案の形が違います")
    # 終わったシーズン(オフ)は、引退・退団した選手も記録に残るので、形だけ確かめる
    elif not (isinstance(hist_d, dict) and all(tid in season.teams and isinstance(h, list) and all(isinstance(s, dict) and isinstance(s.get("day"), int) and all(isinstance(x, str) and (season.is_over or x in season.players) for k in ("starters", "relievers", "batters") for x in s.get(k, [])) for s in h) for tid, h in hist_d.items())):
        p.add("state.json.season.active_history", "一軍の顔ぶれの記録の形が違います(知らない選手がいます)")
    else:
        season.trades = [dict(x) for x in trades_d]
        season.trade_offers = [dict(x) for x in offers_d]
        season.active_history = {tid: [dict(s) for s in h] for tid, h in hist_d.items()}
    if p.items:
        raise SaveDataError(p.items)
    for pg in played:
        season._record(pg.result)
    season.played = played
    _restore_roster(season, season_d, p)
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
    for tid in funds_d:
        if tid not in team_ids:
            p.add(f"state.json.team_funds.{tid}", "球団の一覧にない ID です")
    if funds_d or tiers_d:  # 資金は、全球団に規模と資金の両方がそろっているか、どちらもない(資金の制限なし:試験用の状態)か
        for tid in team_ids:
            if tid not in funds_d or tid not in tiers_d:
                p.add(f"state.json.team_funds.{tid}", "球団ごとの規模(budget_tiers)と資金(team_funds)が必要です")
    from .rules import rules_from

    rules = None
    try:
        rules = rules_from(state.get("rules"))  # 版 20 以前は既定(D-382)
    except ValueError as exc:
        p.add("state.json.rules", str(exc))
    if p.items:
        raise SaveDataError(p.items)
    name = manifest.get("name", "")
    out = GameState(season, cfg["generation"], cfg["names"], name if isinstance(name, str) else "", my_team_id, baselines, baseline_settings, park_history, year, history, offseasons, cfg["offseason"], calibration, str(scout_level), {tid: {k: float(x) for k, x in v.items()} for tid, v in scout_sd_d.items()}, procedure, [dict(x) for x in transactions_d], cfg["draft"], budget_tiers={tid: str(v) for tid, v in tiers_d.items()}, team_funds={tid: int(v) for tid, v in funds_d.items()}, contract_rates={str(k): float(v) for k, v in rates_d.items()}, contract_settings=cfg["contracts"], negotiation_settings=cfg["negotiation"], trade_settings=cfg["trade"], rules=rules, facility_settings=cfg["facilities"])
    missing = {t.id for t in out.league.teams if not t.facilities}
    if missing:  # 版 21 以前:設備は規模に応じた初めの段階(D-385)
        keep = {t.id: t.facilities for t in out.league.teams}
        set_initial(out.league, out.budget_tiers, out.facility_settings)
        for t in out.league.teams:
            if t.id not in missing:
                t.facilities = keep[t.id]
    if any(p_.fa_seasons is None for p_ in out.league.all_players()):
        from .fa import initialize_seasons

        initialize_seasons(out.league, out.season.actives, out.league.seed, out.negotiation_settings, out.history, only_missing=True)  # 版11以前:FA 権の年数を補う(D-264)
    return out


