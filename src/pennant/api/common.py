"""画面から呼ぶ操作の関数の、共通の部品(表示用の名前・指標の列・成績の集計の使い回し・シーズンのまとまり)。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from ..baselines import Baselines, RunTally, tally_game
from ..decisions import Decisions, decide
from ..glossary import default_glossary
from ..metrics import MetricsConfig, compute, format_value, innings_text, load_metrics_config
from ..parkfactors import ParkTally, add_game
from ..records import Records, game_records
from ..season import Season
from ..war import WarLine


def _pct_text(pct: float, games: int) -> str:
    if games == 0:
        return "-"
    text = f"{pct:.3f}"
    return text[1:] if text.startswith("0.") else text


def _gb_text(gb: float, rank_first: bool) -> str:
    if rank_first or gb == 0:
        return "-"
    return f"{gb:.1f}"


ROLE_LABELS = {"batter": "打者", "pitcher": "投手"}


ORIGIN_LABELS = {"high_school": "高卒", "college": "大卒", "corporate": "社会人・独立リーグ"}


KIND_LABELS = {"basic": "基本", "saber": "セイバー"}


BATS_LABELS = {"R": "右打ち", "L": "左打ち", "S": "両打ち"}


THROWS_LABELS = {"R": "右投げ", "L": "左投げ"}


QUALIFY_RULES = {
    "batter": "規定打席:チームの試合数 × 3.1(四捨五入)以上の打席に立った打者",
    "pitcher": "規定投球回:チームの試合数 × 1.0 回以上を投げた投手",
}


_metrics_config: MetricsConfig | None = None


def metrics_config() -> MetricsConfig:
    """指標の定義データ(一度だけ読む)。"""
    global _metrics_config
    if _metrics_config is None:
        _metrics_config = load_metrics_config()
    return _metrics_config


def column_info(config: MetricsConfig, role: str, key: str) -> dict:
    """表の列の見出し(指標の定義データから。D-119)。解説の文は用語集から画面が引く(D-311)。"""
    if key in config.metrics:
        m = config.metrics[key]
        return {"key": key, "label": m["name"], "type": "metric", "category": m["category"], "better": m["better"][role]}
    label = config["counts"][role][key].split("(")[0]
    return {"key": key, "label": label, "type": "count", "category": None, "better": "high"}


BASELINE_MODES = ("trial", "default")  # 新規開始の基準値:試運転で求める / 設定ファイルの既定値(D-121、D-122)


SOURCE_LABELS = {"trial": "試運転のシーズンの値", "default": "設定ファイルの既定値", "season": "前のシーズンの値", "blend": "前のシーズンの値"}


_BASELINE_METRICS = {"woba", "wrc_plus", "ops_plus", "fip"}  # 基準値を使う指標(第2弾)


WAR_KIND = "war"  # 個人成績の「WAR」の切り替え(第3弾③b。D-179)。指標の式ではないので、列は metrics.json でなくここで持つ


WAR_COLUMNS = {
    "batter": [
        {"key": "plate_appearances", "label": "打席", "type": "count", "category": "war", "better": "high"},
        {"key": "war", "label": "WAR", "type": "metric", "category": "war", "better": "high"},
        {"key": "batting", "label": "打撃", "type": "metric", "category": "war", "better": "high"},
        {"key": "baserunning", "label": "走塁", "type": "metric", "category": "war", "better": "high"},
        {"key": "fielding", "label": "守備", "type": "metric", "category": "war", "better": "high"},
        {"key": "position", "label": "ポジション補正", "type": "metric", "category": "war", "better": "high"},
        {"key": "replacement", "label": "控え水準", "type": "metric", "category": "war", "better": "high"},
    ],
    "pitcher": [
        {"key": "innings", "label": "投球回", "type": "count", "category": "war", "better": "high"},
        {"key": "war_ra", "label": "WAR(失点版)", "type": "metric", "category": "war", "better": "high"},
        {"key": "war_fip", "label": "WAR(FIP 版)", "type": "metric", "category": "war", "better": "high"},
    ],
}


_WAR_SORT_KEYS = {role: [c["key"] for c in cols] for role, cols in WAR_COLUMNS.items()}


# 契約の画面(D-272)の状態と、ポジションの絞り込み
CONTRACT_STATUS_LABELS = {"unoffered": "未提示", "refused": "保留", "accepted": "更改済", "declared": "FA 宣言", "released": "自由契約", "contracted": "契約中"}


CONTRACT_GROUPS = {"all": ("全員", None), "pitcher": ("投手", ("SP", "RP")), "catcher": ("捕手", ("C",)), "infield": ("内野手", ("1B", "2B", "3B", "SS")), "outfield": ("外野手", ("LF", "CF", "RF"))}


CONTRACT_COLUMNS = [  # 「状態」と「今回の提示」を指標のすぐ右に(D-284)
    {"key": "status", "label": "状態", "type": "text", "better": "low"},
    {"key": "offer", "label": "今回の提示", "type": "count", "better": "high"},
    {"key": "pos", "label": "ポジション", "type": "text", "better": "low"},
    {"key": "age", "label": "年齢", "type": "metric", "better": "low"},
    {"key": "contract", "label": "今の契約", "type": "count", "better": "high"},
    {"key": "usage", "label": "出場", "type": "count", "better": "high"},
]


CONTRACT_REASONS = {"initial": "開始時", "renew": "更改", "draft": "ドラフト", "market": "市場", "fill": "自動補充", "migrate": "旧版から", "fa": "FA"}


# 選手の一覧の表(自由契約・市場。D-222)の基本の列
ROSTER_BASE_COLUMNS = {
    "batter": [
        {"key": "pos", "label": "ポジション", "type": "text", "better": "low"},
        {"key": "age", "label": "年齢", "type": "metric", "better": "low"},
        {"key": "usage", "label": "打席", "type": "count", "better": "high"},
        {"key": "salary", "label": "年俸", "type": "count", "better": "high"},
        {"key": "years", "label": "残り", "type": "count", "better": "high"},
    ],
    "pitcher": [
        {"key": "pos", "label": "ポジション", "type": "text", "better": "low"},
        {"key": "age", "label": "年齢", "type": "metric", "better": "low"},
        {"key": "usage", "label": "投球回", "type": "count", "better": "high"},
        {"key": "salary", "label": "年俸", "type": "count", "better": "high"},
        {"key": "years", "label": "残り", "type": "count", "better": "high"},
    ],
}


def _war_text(v: Fraction, digits: int = 2) -> str:
    return f"{float(v):.{digits}f}"


def _war_values(line: WarLine) -> dict:
    """WAR の表の1行分:並べ替え用の数と表示用の文字。"""
    if line.role == "batter":
        raw = {"plate_appearances": Fraction(line.plate_appearances), "war": line.war, "batting": line.batting, "baserunning": line.baserunning, "fielding": line.fielding, "position": line.position, "replacement": line.replacement}
        text = {k: (str(line.plate_appearances) if k == "plate_appearances" else _war_text(v, 2 if k == "war" else 1)) for k, v in raw.items()}
    else:
        raw = {"innings": Fraction(line.outs), "war_ra": line.war_ra, "war_fip": line.war_fip}
        text = {"innings": innings_text(line.outs), "war_ra": _war_text(line.war_ra), "war_fip": _war_text(line.war_fip)}
    return {k: (raw[k], text[k]) for k in raw}


def table_cols(config: MetricsConfig, role: str, kind: str) -> list[dict]:
    return [column_info(config, role, k) for k in config["tables"][role][kind]["columns"]]


def sortable_keys(role: str) -> list[dict]:
    """並び順に使える列(D-132):基本・セイバーの表の列(元の数を含む)と、その役割の全指標。表の順に並べ、残りの指標を後ろに足す。"""
    if role not in ROLE_LABELS:
        raise ValueError(f"打者か投手を選んでください(値: {role!r})")
    config = metrics_config()
    keys: list[str] = []
    for kind in KIND_LABELS:
        for k in config["tables"][role][kind]["columns"]:
            if k not in keys:
                keys.append(k)
    for k in config.for_role(role):
        if k not in keys:
            keys.append(k)
    return [column_info(config, role, k) for k in keys]


def is_sortable(config: MetricsConfig, role: str, key: str) -> bool:
    return key in config["counts"][role] or (key in config.metrics and role in config.metrics[key]["formulas"])


def glossary_view() -> dict:
    """用語集のページ(UI の整理②。D-307〜D-311):分類と項目。表の見出しの title・説明バーも、画面がここから引く。"""
    g = default_glossary()
    return {"categories": list(g.categories), "terms": [dict(t) for t in g.terms]}


def raw_rate(c, key: str) -> Fraction | None:
    """球場の集計の率(得点/打席、本塁打/打席、BABIP = HIT/BIP)。"""
    num, den = {"runs": ("R", "PA"), "home_run": ("HR", "PA"), "babip": ("HIT", "BIP")}[key]
    return Fraction(c[num], c[den]) if c[den] else None


def _values(config: MetricsConfig, role: str, counts, baselines: Baselines | None = None, park_factor: Fraction | None = None, override: Mapping | None = None) -> dict:
    """元の数と指標の値(並べ替え用の数と、表示用の文字)。park_factor は選手ごとの球場補正(式の pf。D-142)。
    override は通算用:指標 → 値(基準値に依存する指標を、シーズンごとの値の加重平均で置き換える。D-192)。"""
    values = dict(baselines.values) if baselines else None
    if values is not None and park_factor is not None:
        values["pf"] = park_factor
    metrics = compute(config, role, counts, values)
    if override:
        metrics.update(override)
    out = {}
    for key in config["counts"][role]:
        v = counts.get(key, 0)
        out[key] = (v, innings_text(v) if key == "OUTS" else str(v))
    for key, v in metrics.items():
        out[key] = (v, format_value(config, key, v))
    return out


class _StatsCache:
    """試合ごとの元の数と、その合計(日が進むまで使い回す。D-114)。

    進めた試合の分だけ計算して足す。読み込み直したゲームでは作り直す。
    """

    def __init__(self):
        self.per_game: list[Records] = []
        self.decisions: list[Decisions] = []
        self.total = Records()
        self.tally = RunTally()  # 基準値(RE24 など)の元の整数の集計
        self.park_tallies: dict[str, ParkTally] = {}  # 球場 × 今シーズンの集計(本拠地・アウェイ。D-146)
        self.player_park_pa: dict[str, Counter] = {}  # 選手 → 球場(ホームチームの ID)→ 立った打席数(D-142)
        self._baselines: tuple[int, Baselines, object] | None = None

    def update(self, season: Season) -> "_StatsCache":
        for played in season.played[len(self.per_game) :]:
            d = decide(played.result)
            rec = game_records(played.result, d)
            self.per_game.append(rec)
            self.decisions.append(d)
            self.total.add(rec)
            self.tally.add(tally_game(played.result))
            add_game(self.park_tallies, played.result)
            park = played.result.home_team_id
            for x in played.result.log:
                self.player_park_pa.setdefault(x.batter_id, Counter())[park] += 1
        return self


def _sum(group) -> Counter:
    total = Counter()
    for c in group.values():
        total.update(c)
    return total


@dataclass
class _SeasonView:
    """成績の計算に使う1シーズン分(または通算)のまとまり(F2。D-182)。"""

    key: str  # "current" / シーズン番号 / "career"
    label: str
    records: Records
    baselines: Baselines
    park_factor: object  # 選手 ID → 球場補正(Fraction)
    war: dict
    info: object  # 選手 ID → {name, team_id, role, position, age}
    baseline_note: str
    war_note: str
    day: int | None
    override: dict | None = None  # 通算:選手 ID → 指標 → 加重平均した値(基準値に依存する指標。D-192)


def _add_war(total: WarLine | None, v: WarLine) -> WarLine:
    """WAR の行を足す(通算用)。"""
    if total is None:
        total = WarLine(v.role, v.team_id)
    total.team_id = v.team_id
    total.plate_appearances += v.plate_appearances
    total.outs += v.outs
    for k in ("batting", "baserunning", "fielding", "position", "replacement", "war", "fip_runs", "ra_runs", "defense_adjustment", "war_fip", "war_ra"):
        setattr(total, k, getattr(total, k) + getattr(v, k))
    total.runs_per_win = v.runs_per_win
    return total
