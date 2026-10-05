"""画面から呼ぶ「操作の関数」(最小のブラウザ画面。D-080、D-107、D-108)。

計算本体の側にあり、画面からは独立している。戻り値は、画面がそのまま使える JSON にできる形
(辞書・リスト・文字列・数・真偽値・None)。bytes はセーブデータの中身だけ。

公開用の関数だけを置く:選手の能力値・能力の見積もり・隠し情報は返さない(D-108)。
答え合わせ用の関数は、別のモジュール(answers.py)に置く(D-114)。

成績の画面(②。D-114):個人成績・選手・試合・チームの詳細。集計(試合ごとの元の数)は、
進めた試合の分だけ足していき、日が進むまで使い回す(_StatsCache)。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
from collections import Counter
from fractions import Fraction
from datetime import date, datetime
from typing import Mapping, Sequence

from .abilities import POSITION_LABELS
from .baselines import Baselines, RunTally, blend, compute_baselines, load_baseline_settings, tally_game, trial_baselines
from .config import load_generation_config, load_name_parts
from .decisions import Decisions, decide
from .game_stats import game_story
from .metrics import baseline_dependent, MetricsConfig, compute, format_value, formula_text, innings_text, load_metrics_config
from .parkfactors import FACTOR_KEYS, FACTOR_LABELS, ParkEstimate, ParkTally, add_game, estimate_parks, load_park_settings, player_park_factor, raw_ratio, season_tallies
from .war import WarLine, load_war_settings, war_for_results, war_totals
from .history import ArchivedStanding, SeasonArchive
from .offseason import OffseasonResult, age_update_retire, load_offseason_settings
from . import draft as draftmod
from .draft import PHASE_LABELS, STAGE_LABELS, STAGES, load_draft_settings, stage_of
from .scouting import ScoutReport
from .contracts import MONEY_RULES, RULE_LABELS, RULE_NOTES, TIER_LABELS, assign_tiers, budget_of, cap_of, is_hard, load_contract_settings, remaining_years, team_salary
from . import fa as famod
from .negotiation import load_negotiation_settings
from .season import derive_seed
from .records import (
    Records,
    game_records,
    qualified_batters,
    qualified_pitchers,
)
from .newgame import check_team_name, new_league, public_player, resolve_team_names, TeamNameError
from .savegame import GameState, SaveDataError, default_file_name, load_game, save_game
from .season import Season

def _pct_text(pct: float, games: int) -> str:
    if games == 0:
        return "-"
    text = f"{pct:.3f}"
    return text[1:] if text.startswith("0.") else text


def _gb_text(gb: float, rank_first: bool) -> str:
    if rank_first or gb == 0:
        return "-"
    return f"{gb:.1f}"


def preview_teams(seed: int) -> dict:
    """新規開始の画面に出す、球団の並びと架空の初期名(そのシードで作られるリーグ)。"""
    league = new_league(seed)
    return {
        "seed": seed,
        "leagues": [
            {
                "index": i,
                "name": name,
                "teams": [
                    {"id": t.id, "default_name": t.default_name, "stadium": t.stadium}
                    for t in league.teams
                    if t.league_index == i
                ],
            }
            for i, name in enumerate(league.league_names)
        ],
        "order": [t.id for t in league.teams],
    }


def check_team_names(seed: int, names: Sequence[str | None]) -> list[str | None]:
    """球団名の欄ごとの問題(なければ None)。重複は、後ろの欄に理由を付ける。"""
    league = new_league(seed)
    problems: list[str | None] = [check_team_name(n) for n in names]
    if any(problems):
        return problems
    try:
        resolve_team_names(league, names)
    except TeamNameError as exc:
        for text in exc.problems:
            head, _, reason = text.partition(": ")
            if head.endswith("番目の球団"):
                i = int(head.removesuffix("番目の球団")) - 1
                if 0 <= i < len(problems) and problems[i] is None:
                    problems[i] = reason
    return problems


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
    """表の列の見出しと解説(指標の定義データから。D-119)。"""
    if key in config.metrics:
        m = config.metrics[key]
        text = m["description"] + "".join(f"。{m[k]}" for k in ("note", "better_note") if m.get(k))
        return {"key": key, "label": m["name"], "description": text, "type": "metric", "category": m["category"], "better": m["better"][role]}
    label = config["counts"][role][key].split("(")[0]
    return {"key": key, "label": label, "description": config["count_descriptions"][role][key], "type": "count", "category": None, "better": "high"}


BASELINE_MODES = ("trial", "default")  # 新規開始の基準値:試運転で求める / 設定ファイルの既定値(D-121、D-122)
SOURCE_LABELS = {"trial": "試運転のシーズンの値", "default": "設定ファイルの既定値", "season": "前のシーズンの値", "blend": "前のシーズンの値"}


_BASELINE_METRICS = {"woba", "wrc_plus", "ops_plus", "fip"}  # 基準値を使う指標(第2弾)

WAR_KIND = "war"  # 個人成績の「WAR」の切り替え(第3弾③b。D-179)。指標の式ではないので、列は metrics.json でなくここで持つ
WAR_TERMS = [
    {"key": "war", "label": "WAR", "description": "Wins Above Replacement の略。控え水準の選手に比べて、何勝分多く勝ちに貢献したか。打者は(打撃 + 走塁 + 守備 + ポジション補正 + 控え水準)÷ 1勝あたりの得点。高いほどよい"},
    {"key": "replacement", "label": "控え水準", "description": "いつでも補充できる控え選手の水準。その選手と同じ出場量を控え選手が担ったときに比べて、どれだけ得点(失点)を増減させたかを測る土台。設定値(野手は1打席あたり、投手は9イニングあたり)"},
    {"key": "position", "label": "ポジション補正", "description": "守備の負担が重いポジションほど加点し、軽いポジション(一塁・指名打者など)は減点する調整(設定値。点/125試合を、守備に就いた量で按分)"},
    {"key": "runs_per_win", "label": "1勝あたりの得点", "description": "得点(失点)を勝ち数に直す換算。2 × 1チーム1試合あたりの得点(今の得点環境で約 9 点)"},
    {"key": "war_ra", "label": "失点版", "description": "投手の WAR のうち、実際の失点から求めたもの。チームの野手の守備の得点を投球回で按分して差し引き、球場補正(前のシーズンまでの推定)を入れる"},
    {"key": "war_fip", "label": "FIP 版", "description": "投手の WAR のうち、FIP(本塁打・四死球・三振だけで見た失点のしにくさ)から求めたもの。守備と運の影響を受けにくい。球場補正はしない"},
]
WAR_COLUMNS = {
    "batter": [
        {"key": "plate_appearances", "label": "打席", "description": "打数 + 四球 + 死球 + 犠飛(wOBA の分母)", "type": "count", "category": "war", "better": "high"},
        {"key": "war", "label": "WAR", "description": WAR_TERMS[0]["description"], "type": "metric", "category": "war", "better": "high"},
        {"key": "batting", "label": "打撃", "description": "打撃の得点:wOBA から求めた、平均的な打者に比べた得点の増減(球場補正つき)", "type": "metric", "category": "war", "better": "high"},
        {"key": "baserunning", "label": "走塁", "description": "走塁の得点:走者としての進塁(安打での追加の進塁、タッチアップ、併殺の回避など)の価値を、同じ状況の平均と比べたもの", "type": "metric", "category": "war", "better": "high"},
        {"key": "fielding", "label": "守備", "description": "守備の得点:担当した打球をアウトにした数と、同じポジション・打球の種類のリーグ平均との差を得点に直したもの", "type": "metric", "category": "war", "better": "high"},
        {"key": "position", "label": "ポジション補正", "description": WAR_TERMS[2]["description"], "type": "metric", "category": "war", "better": "high"},
        {"key": "replacement", "label": "控え水準", "description": "控え水準の得点:出場量 × 設定値。多く出るほど大きい", "type": "metric", "category": "war", "better": "high"},
    ],
    "pitcher": [
        {"key": "innings", "label": "投球回", "description": "投げたイニング(アウト 3 つで 1 回)", "type": "count", "category": "war", "better": "high"},
        {"key": "war_ra", "label": "WAR(失点版)", "description": WAR_TERMS[4]["description"], "type": "metric", "category": "war", "better": "high"},
        {"key": "war_fip", "label": "WAR(FIP 版)", "description": WAR_TERMS[5]["description"], "type": "metric", "category": "war", "better": "high"},
    ],
}
_WAR_SORT_KEYS = {role: [c["key"] for c in cols] for role, cols in WAR_COLUMNS.items()}
# 契約の画面(D-272)の状態と、ポジションの絞り込み
CONTRACT_STATUS_LABELS = {"unoffered": "未提示", "refused": "保留", "accepted": "更改済", "declared": "FA 宣言", "released": "自由契約", "contracted": "契約中"}
CONTRACT_GROUPS = {"all": ("全員", None), "pitcher": ("投手", ("SP", "RP")), "catcher": ("捕手", ("C",)), "infield": ("内野手", ("1B", "2B", "3B", "SS")), "outfield": ("外野手", ("LF", "CF", "RF"))}
CONTRACT_COLUMNS = [
    {"key": "pos", "label": "ポジション", "description": "守備位置", "type": "text", "better": "low"},
    {"key": "age", "label": "年齢", "description": "今の年齢", "type": "metric", "better": "low"},
    {"key": "contract", "label": "今の契約", "description": "今の契約の年俸(万円)と、次のシーズンからの残り年数(満了は更改の対象)", "type": "count", "better": "high"},
    {"key": "offer", "label": "今回の提示", "description": "今回の更改の提示(年俸・年数)。未提示は自動案", "type": "count", "better": "high"},
    {"key": "status", "label": "状態", "description": "未提示・保留(断られた)・更改済・FA 宣言・自由契約・契約中(複数年契約の途中)", "type": "text", "better": "low"},
    {"key": "usage", "label": "出場", "description": "選んだシーズンの打席数(投手は投球回)", "type": "count", "better": "high"},
]
CONTRACT_REASONS = {"initial": "開始時", "renew": "更改", "draft": "ドラフト", "market": "市場", "fill": "自動補充", "migrate": "旧版から", "fa": "FA"}
# 選手の一覧の表(自由契約・市場。D-222)の基本の列
ROSTER_BASE_COLUMNS = {
    "batter": [
        {"key": "pos", "label": "ポジション", "description": "守備位置", "type": "text", "better": "low"},
        {"key": "age", "label": "年齢", "description": "今の年齢", "type": "metric", "better": "low"},
        {"key": "usage", "label": "打席", "description": "選んだシーズンの打席数", "type": "count", "better": "high"},
        {"key": "salary", "label": "年俸", "description": "年俸(万円)。手続き中は次のシーズンの契約", "type": "count", "better": "high"},
        {"key": "years", "label": "残り", "description": "残りの契約年数(次のシーズンを含む)", "type": "count", "better": "high"},
    ],
    "pitcher": [
        {"key": "pos", "label": "ポジション", "description": "先発か救援か", "type": "text", "better": "low"},
        {"key": "age", "label": "年齢", "description": "今の年齢", "type": "metric", "better": "low"},
        {"key": "usage", "label": "投球回", "description": "選んだシーズンの投球回(アウト 3 つで 1 回)", "type": "count", "better": "high"},
        {"key": "salary", "label": "年俸", "description": "年俸(万円)。手続き中は次のシーズンの契約", "type": "count", "better": "high"},
        {"key": "years", "label": "残り", "description": "残りの契約年数(次のシーズンを含む)", "type": "count", "better": "high"},
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


def metrics_guide() -> dict:
    """指標の解説のページ:すべての指標を区分別に、式・解説・見るときの注意つきで(指標の定義データから)。"""
    config = metrics_config()
    groups = []
    for cat, label in (("basic", "基本"), ("saber", "セイバー")):
        items = []
        for mid in config.in_category(cat):
            m = config.metrics[mid]
            items.append(
                {
                    "key": mid,
                    "name": m["name"],
                    "description": m["description"],
                    "formulas": [{"role": ROLE_LABELS[r], "text": formula_text(config, r, e)} for r, e in m["formulas"].items()],
                    "better": [f"{ROLE_LABELS[r]}:{'高いほどよい' if b == 'high' else '低いほどよい'}" for r, b in m["better"].items()],
                    "notes": [m[k] for k in ("note", "better_note") if m.get(k)],
                    "stage": m["stage"],
                }
            )
        groups.append({"category": cat, "label": label, "metrics": items})
    names = config.data.get("baseline_names", {})
    return {"groups": groups, "baseline_names": [{"key": k, "label": v} for k, v in names.items()]}


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


class Game:
    """遊んでいる1つのゲーム(画面は、これを1つ持って操作する)。"""

    def __init__(self, state: GameState, dirty: bool):
        self.state = state
        self.dirty = dirty  # 未保存の変更があるか
        self._cache = _StatsCache()
        self._park_estimates: tuple[int, dict[str, ParkEstimate] | None] | None = None
        self._war: tuple[int, dict[str, WarLine]] | None = None  # (試合数, WAR の表)。試合数が変わるまで覚えておく(D-179)
        self.last_negotiations: dict | None = None  # 直前に終わったオフの手続きの更改の交渉(保存しない。F3-2b)
        self.last_fa: dict | None = None  # 直前に終わったオフの FA(保存しない。F3-2c)

    @property
    def records(self) -> _StatsCache:
        """集計(試合ごとの元の数と合計)。前に計算した分は使い回す。"""
        return self._cache.update(self.state.season)

    def baselines(self) -> tuple[Baselines, object]:
        """今使う基準値(出発点と今シーズンの値を、累積打席数で混ぜたもの。D-126)と、今シーズンの比重。

        日が進むまで使い回す。
        """
        cache = self.records
        n = len(cache.per_game)
        if cache._baselines is None or cache._baselines[0] != n:
            prior = self.state.baselines
            settings = self.state.baseline_settings
            batting = _sum(cache.total.batters)
            pa = batting["PA"]  # 比重は今シーズンの全打席数で(D-126)
            current = None
            if cache.tally.plate_appearances:
                current = compute_baselines(cache.tally, batting, _sum(cache.total.pitchers), settings, prior)
            blended, w = blend(prior, current, pa, settings.blend_constant)
            cache._baselines = (n, blended, w)
        return cache._baselines[1], cache._baselines[2]

    def baseline_info(self) -> dict:
        """画面に出す、基準値の混ぜ方と球場補正の説明(D-126、D-143)。"""
        _, w = self.baselines()
        source = SOURCE_LABELS.get(self.state.baselines.source, "出発点の値")
        pct = math.floor(w * 100 + Fraction(1, 2))
        n = self.season_number()
        park_text = (
            "球場補正は、1シーズン目のため 1.0(補正なし)です。"
            if n == 1
            else f"球場補正は、前のシーズンまで({n - 1}シーズン分)の結果から推定した値を使っています。"
        )
        return {
            "source": self.state.baselines.source,
            "source_label": source,
            "weight_percent": pct,
            "plate_appearances": _sum(self.records.total.batters)["PA"],
            "season_number": n,
            "text": f"wOBA などの基準値は、{source}に、今シーズンの値を混ぜて使っています(今シーズンの比重 {pct}%。打席が増えるほど上がり、シーズンの最後で約75%)。{park_text}",
        }

    # ---- 球場補正(②b。D-140〜D-143) ----

    def season_number(self) -> int:
        """今が何シーズン目か(前のシーズンまでの履歴の数 + 1)。"""
        return len(self.state.park_history) + 1

    def park_estimates(self) -> dict[str, ParkEstimate] | None:
        """前のシーズンまでの履歴から推定した球場補正。1シーズン目(履歴なし)は None。シーズン中は変わらない(D-143)。"""
        n = len(self.state.park_history)
        if n == 0:
            return None
        if self._park_estimates is None or self._park_estimates[0] != n:
            league_of = {t.id: t.league_index for t in self.state.league.teams}
            self._park_estimates = (n, estimate_parks(self.state.park_history, league_of, load_park_settings()))
        return self._park_estimates[1]

    def player_park_factor(self, player_id: str) -> Fraction:
        """選手の球場補正(立った球場ごとの打席数で重みづけ。D-142)。1シーズン目は 1。"""
        return player_park_factor(self.park_estimates(), self.records.player_park_pa.get(player_id, {}))

    def war_lines(self) -> dict[str, WarLine]:
        """今シーズンの、その時点までの WAR(③b。D-179)。基準値は wRC+ と同じ混ぜた値、球場補正は前のシーズンまでの推定。試合数が変わるまで覚えておく。"""
        n = len(self.state.season.played)
        if self._war is None or self._war[0] != n:
            results = [p.result for p in self.state.season.played]
            lines, _, _ = war_for_results(results, self.park_estimates(), self.state.baseline_settings or load_baseline_settings(), load_war_settings(), self.baselines()[0], self.records.total)
            self._war = (n, lines)
        return self._war[1]

    def war_note(self) -> str:
        n = self.season_number()
        park = "球場補正は、1シーズン目のため 1.0(補正なし)" if n == 1 else f"球場補正は、前のシーズンまで({n - 1}シーズン分)の結果から推定した値"
        return f"{self.state.season.day}日目までの値です(シーズンが進むと変わります)。基準値(リーグ平均・得点期待値)は wRC+ と同じく、出発点の値に今シーズンの値を混ぜたもの。{park}。控え水準とポジション補正は設定値(仮置き)。"

    def finish_season(self) -> int:
        """シーズンを終えて、球場 × シーズンの集計を履歴に足す(年度の確定の一部。D-146)。戻り値は履歴の数。"""
        if not self.state.season.is_over:
            raise ValueError("シーズンがまだ終わっていません")
        self.state.park_history.append(season_tallies(p.result for p in self.state.season.played))
        self._park_estimates = None
        self.dirty = True
        return len(self.state.park_history)

    # ---- 複数年(F2。D-180〜D-189) ----

    def year_end_preview(self) -> dict:
        """年度の確定の確認の画面に出す情報(戻せないこと、保存への導線は画面側)。"""
        season = self.state.season
        names = self._team_names()
        champs = []
        if season.is_over:
            for i in season.league_indexes():
                champs.append({"league_name": self.state.league.league_names[i], "teams": [names[t] for t in season.result().champions[i]]})
        return {
            "year": self.state.year,
            "is_over": season.is_over,
            "champions": champs,
            "players": len(self.state.league.all_players()),
            "log_seasons": self.state.offseason_settings.log_seasons,
            "dirty": self.dirty,
            "note": "年度を確定すると、今シーズンの集計を履歴に残し、選手の年齢が1つ進んで能力が更新され、引退と新人の入団が決まり、次のシーズンが始まります。この操作は戻せません。直前の状態を残したいときは、先に「保存」で別の名前のファイルに保存してください。",
        }

    def _archive_current_season(self) -> SeasonArchive:
        season = self.state.season
        result = season.result()
        standings = [
            ArchivedStanding(i, row.rank, row.team_id, row.wins, row.losses, row.ties, Fraction(row.games_behind))
            for i, rows in result.standings.items()
            for row in rows
        ]
        rec = self.records.total
        players = {}
        for pid in set(rec.batters) | set(rec.pitchers):
            p = season.players[pid]
            players[pid] = {"name": p.name, "team_id": p.team_id, "role": p.role, "position": p.position, "age": p.age}
        keep = len(self.state.history) + 1 < self.state.offseason_settings.log_seasons  # 今シーズンのログを残すか(今シーズンを含めて log_seasons 分)
        return SeasonArchive(
            year=self.state.year,
            seed=season.seed,
            standings=standings,
            champions=dict(result.champions),
            records=copy.deepcopy(rec),
            players=players,
            park_factors={pid: self.player_park_factor(pid) for pid in rec.batters},
            war=dict(self.war_lines()),
            baselines=self.baselines()[0],
            games=list(season.played) if keep else None,
            day=season.day,
            total_days=season.total_days,
        )

    def year_end(self) -> dict:
        """年度の確定(F2。D-185、D-186):今シーズンの集計を履歴へ → 球場補正の履歴と基準値の出発点を更新 →
        加齢・能力の更新・引退・補充(offseason.run_offseason)→ 次のシーズンを作る。戻り値はオフの結果の要約。"""
        state = self.state
        season = state.season
        if not season.is_over:
            raise ValueError("シーズンがまだ終わっていません(最後まで進めてから、年度を確定してください)")
        if state.procedure is not None:
            raise ValueError("オフの手続きが進行中です(手続きを終えると、次のシーズンが始まります)")
        famod.count_active_seasons(state.league, season.actives)  # このシーズンの一軍に入っていた選手の FA 権の年数(D-258)
        archive = self._archive_current_season()
        state.history.append(archive)
        # 残す打席ログの数を守る(直近 log_seasons シーズン。今シーズンは次のシーズンの分として数える)
        limit = max(0, state.offseason_settings.log_seasons - 1)
        with_games = [a for a in state.history if a.games is not None]
        for a in with_games[: max(0, len(with_games) - limit)]:
            a.games = None
        records = {tid: (int(c["W"]), int(c["L"])) for tid, c in self.records.total.teams.items()}
        state.park_history.append(season_tallies(p.result for p in season.played))
        final, _ = self.baselines()
        state.baselines = Baselines(dict(final.values), list(final.re24), dict(final.linear_weights), final.plate_appearances, "season")  # 次の出発点(D-121)
        off_seed = derive_seed(season.seed, "offseason")
        result, _ = age_update_retire(state.league, off_seed, state.gen_config, state.offseason_settings, state.year, state.calibration)
        state.offseasons.append(result)
        state.procedure = draftmod.start_procedure(state.league, off_seed, state.gen_config, state.name_parts, state.draft_settings, state.year, state.calibration, records)
        state.procedure.ranks = {s.team_id: int(s.rank) for s in archive.standings}  # 勝利軸は公式の順位(D-250)
        ctx = self._contract_ctx()
        draftmod.apply_contracts_start(state.league, state.procedure, ctx, state.my_team_id, *self._mins())  # 更改の交渉(AI 球団は最後まで)と AI の超過の解消(D-235、D-244)
        if state.my_team_id is not None and not draftmod.open_entries(state.procedure, state.my_team_id):
            draftmod.next_phase(state.procedure)  # 自球団に満了者がいなければ、契約更改の段階は飛ばす
        state.contract_rates[str(state.year + 1)] = state.procedure.rate
        self.dirty = True
        if state.my_team_id is None:  # 観戦のみ:手続きはすべて自動(D-198)
            filled = draftmod.complete(state.league, state.procedure, state.gen_config, state.name_parts, state.draft_settings, state.scout_sd_map(), state.calibration, None, *self._mins(), ctx=ctx)
            self._finish_offseason(filled)
        return self.offseason_summary(result.year)

    def budget_info(self, team_id: str) -> dict:
        """球団の総年俸・予算・上限・使用率(公開情報。なしのときは総年俸だけ)。"""
        state = self.state
        team = self._team(team_id)
        total = team_salary(team)
        tier = state.budget_tiers.get(team_id)
        budget = budget_of(state.money_rule, tier, state.contract_settings)
        cap = cap_of(state.money_rule, tier, state.contract_settings)
        return {
            "team_id": team_id, "rule": state.money_rule, "rule_label": RULE_LABELS[state.money_rule], "total": total, "total_text": f"{total:,} 万円",
            "budget": budget, "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "tier": tier, "tier_label": TIER_LABELS.get(tier) if tier else None,
            "usage": None if cap is None else round(100.0 * total / cap, 1), "over": None if cap is None else max(0, total - cap), "hard": is_hard(state.money_rule),
            "rate": state.contract_rates.get(str(state.year)) or (max(state.contract_rates.values()) if state.contract_rates else None),
        }

    def _contract_public(self, p, year: int | None = None) -> dict | None:
        c = p.contract
        if not c:
            return None
        y = self.state.year if year is None else year
        need = int(famod.fa_settings(self.state.negotiation_settings)["seasons_required"])
        seasons = famod.seasons_of(p)
        return {"fa_seasons": seasons, "fa_required": need, "fa_holder": seasons >= need, "fa_text": f"FA 権あり(一軍 {seasons} シーズン)" if seasons >= need else f"一軍 {seasons} シーズン(FA 権まであと {need - seasons})", "salary": int(c["salary"]), "salary_text": f"{int(c['salary']):,} 万円", "until": int(c["until"]), "remaining": remaining_years(c, y), "history": [{"year": h["year"], "salary": h["salary"], "salary_text": f"{int(h['salary']):,} 万円", "years": h["years"], "reason": h["reason"], "reason_label": CONTRACT_REASONS.get(h["reason"], h["reason"]), "offers": h.get("offers")} for h in c.get("history", [])]}

    def _procedure_contracts(self, proc) -> dict:
        """手続きの画面に出す契約の情報:ルール、自球団の総年俸と予算、更改の結果、予算超過で自由契約になった選手。"""
        state = self.state
        names = self._team_names()
        my = state.my_team_id
        ctx = draftmod.state_context(state, proc)
        mine = None
        if my is not None:
            info = self.budget_info(my)
            info["over_now"] = draftmod.over_cap(self._team(my), ctx)
            info["blocked"] = ctx.hard() and info["over_now"] > 0
            mine = info
        renew = lambda x: {**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my, "old_text": None if x.get("old_salary") is None else f"{x['old_salary']:,} 万円", "salary_text": f"{x['salary']:,} 万円", "change": None if x.get("old_salary") is None else x["salary"] - x["old_salary"]}
        return {
            "rule": state.money_rule, "rule_label": RULE_LABELS[state.money_rule], "rule_note": RULE_NOTES[state.money_rule], "hard": ctx.hard(),
            "rate": proc.rate, "rate_text": f"{proc.rate / 10000:.2f} 億円 / WAR" if proc.rate else "-",
            "mine": mine,
            "renewals": [renew(x) for x in proc.renewals],
            "budget_releases": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my, "salary_text": f"{x['salary']:,} 万円"} for x in proc.budget_releases],
            "negotiation_releases": [{"player_id": e["player_id"], "name": e["name"], "team_id": e["team_id"], "team_name": names.get(e["team_id"], ""), "position_label": POSITION_LABELS.get(e["position"], ""), "age": e["age"], "is_mine": e["team_id"] == my, "offers": len(e["offers"]), "reason": self._renewal_status({**e, "status": "pending"})[2] if e["offers"] else ""} for e in proc.negotiations.values() if e["status"] == "released"],
            "teams": [{"team_id": t.id, "team_name": t.name, "is_mine": t.id == my, **{k: v for k, v in self.budget_info(t.id).items() if k in ("total", "total_text", "cap", "cap_text", "usage", "tier_label")}} for t in state.league.teams],
            "note": "契約が満了した選手に、見込みの WAR(直近 3 シーズンの加重平均。履歴がなければスカウト評価)から算定した年俸で提示し、選手が志望で受けるか断るかを決めました(AI 球団は、断られたら見込みの高い選手にだけ条件を上げて再提示し、ほかは自由契約)。" + ("標準以上では、予算の上限を超える球団は、見込みの WAR あたりの年俸が高い選手から自由契約にして超過を解消します(あなたの球団は自由契約の画面で自分で選びます)。" if ctx.hard() else ""),
        }

    def _contract_ctx(self):
        """進行中の手続きの契約の文脈(評価は手続きのシードと区切りで引く)。"""
        return draftmod.state_context(self.state, self.state.procedure)

    def _mins(self):
        return draftmod.minimum_positions(self.state.season.game_config), draftmod.minimum_batters(self.state.season.game_config)

    def _finish_offseason(self, filled) -> None:
        """オフの手続きの完了:履歴に記録し、次のシーズンを作る。"""
        state = self.state
        proc = state.procedure
        season = state.season
        result = state.offseasons[-1]
        result.rookies = draftmod.joined_players(proc) + list(filled)
        for x in proc.released:
            state.transactions.append({"year": proc.year, "phase": "release", "round": 0, **x})
        for x in proc.picks:
            state.transactions.append({"year": proc.year, **x})
        for x in proc.filled:
            state.transactions.append({"year": proc.year, **x})  # 自動補充も履歴に残す(振り返りのため。D-216)
        for pid, x in proc.fa_info.items():
            state.transactions.append({"year": proc.year, "phase": "fa_declare", "round": 0, "team_id": x["former_team"], "player_id": pid, "name": x["name"], "role": x["role"], "position": x["position"], "age": x["age"], "salary": x.get("old_salary")})
        for x in proc.fa_results:
            state.transactions.append({"year": proc.year, "phase": "fa", **{k: v for k, v in x.items() if k != "offers"}})
        self.last_negotiations = proc.negotiations  # 終わった手続きの更改の交渉(保存しない。指紋 (p) と開発者向けの集計用)
        self.last_fa = {"info": proc.fa_info, "results": proc.fa_results, "log": proc.fa_log, "ranks": dict(proc.ranks), "budget_releases": list(proc.budget_releases)}  # 終わった FA(保存しない。指紋 (q) と集計用)
        state.procedure = None
        state.year += 1
        state.season = Season(state.league, derive_seed(season.seed, "next-season"), season.season_config, season.game_config, season.model, season.manager)
        self._cache = _StatsCache()
        self._park_estimates = None
        self._war = None
        self.dirty = True

    # ---- オフの手続き(F3-1。D-201〜D-207) ----

    def _proc(self):
        proc = self.state.procedure
        if proc is None:
            raise ValueError("オフの手続きは進行中ではありません")
        return proc

    def _my_team(self):
        if self.state.my_team_id is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        return self._team(self.state.my_team_id)

    def _report_public(self, player, team_id: str) -> dict:
        proc = self._proc()
        return draftmod.scout_report(proc, player, team_id, self.state.scout_sd_of(team_id), self.state.draft_settings).to_public()

    def _player_brief(self, p, team_id: str | None, names: dict) -> dict:
        return {"player_id": p.id, "name": p.name, "role": p.role, "role_label": ROLE_LABELS[p.role], "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age, "origin": ORIGIN_LABELS.get(p.origin, "") if p.origin else "", "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws), "team_id": team_id, "team_name": names.get(team_id, "") if team_id else ""}

    def offseason_view(self) -> dict:
        """オフの手続きの画面に出す情報(公開用。スカウト評価は操作する球団のもの)。"""
        state = self.state
        proc = self._proc()
        names = self._team_names()
        my = state.my_team_id
        mins, min_batters = self._mins()
        view = {
            "year": proc.year,
            "next_year": proc.year + 1,
            "phase": proc.phase,
            "phase_label": STAGE_LABELS[stage_of(proc.phase)],
            "stage": stage_of(proc.phase),
            "phases": [{"key": k, "label": STAGE_LABELS[k]} for k in STAGES],
            "order": [{"team_id": t, "team_name": names[t], "is_mine": t == my} for t in proc.order],
            "round": proc.round,
            "total_rounds": proc.total_rounds() if proc.phase in ("draft", "market") else 0,
            "current_team": proc.current_team() if proc.phase in ("draft", "market") else None,
            "is_my_turn": proc.phase in ("draft", "market") and proc.current_team() == my and not draftmod.phase_finished(proc),
            "phase_finished": draftmod.phase_finished(proc) if proc.phase in ("draft", "market") else proc.phase == "done",
            "my_team": None if my is None else {"team_id": my, "team_name": names[my], "players": len(self._team(my).players), "max": draftmod.MAX_ROSTER, "shortages": self._shortage_text(self._team(my).players, mins, min_batters)},
            "my_release_done": proc.my_release_done,
            "contracts": self._procedure_contracts(proc),
            "minimums": {"positions": dict(mins), "batters": min_batters, "labels": {pos: POSITION_LABELS[pos] for pos in mins}},
            "seasons": self.roster_seasons(),
            "scout_level": state.scout_level,
            "scout_sd": state.scout_sd_of(my) if my else None,
            "picks": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my} for x in proc.picks],
            "released": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my} for x in proc.released],
            "counts": {"candidates": len(proc.candidates), "market": len(proc.market), "released": len(proc.released), "picked": sum(1 for x in proc.picks if x["player_id"])},
            "rosters": [{"team_id": t.id, "team_name": t.name, "players": len(t.players), "is_mine": t.id == my} for t in state.league.teams],
            "note": "手続きは 契約更改 → 自由契約 → FA → ドラフト → 自由契約市場 → 完了(自動補充)の順です。途中で保存して、あとで続きから再開できます。「おまかせ」を押すと、残りを自動(AI と同じ方針)で進めます。",
        }
        if my is not None and stage_of(proc.phase) == "contract":
            view["renewal"] = self._renewal_info(proc)
        if proc.phase == "fa":
            view["fa"] = self._fa_info(proc)
        if my is not None:
            team = self._team(my)
            if proc.phase in ("draft", "market"):
                pool = draftmod.pool_of(proc)
                rows = []
                for p in pool:
                    row = self._player_brief(p, None, names)
                    row["scouting"] = self._report_public(p, my)
                    row["former_team"] = next((names.get(x["team_id"], "") for x in proc.released if x["player_id"] == p.id), "")
                    rows.append(row)
                rows.sort(key=lambda r: (-r["scouting"]["overall"], r["player_id"]))
                view["pool"] = rows
        return view

    def _shortage_text(self, players, mins, min_batters) -> list[str]:
        out = []
        for pos, n in draftmod.shortages(players, mins, min_batters).items():
            out.append(f"{'野手' if pos == 'batter' else POSITION_LABELS[pos]} があと {n} 人足りません")
        return out

    # ---- 契約更改(F3-2b。D-243〜D-252) ----

    def _renewal_status(self, e) -> tuple[int, str, str]:
        """交渉の状態の (並び順, 状態の文, 理由の文)。理由は最後に断られた理由(公開。D-245)。"""
        neg = self.state.negotiation_settings
        if e["status"] == "accepted":
            last = e["offers"][-1] if e["offers"] else {"years": e.get("ai_years", 1), "salary": e["auto_salary"]}
            return 2, f"更改済({last['years']} 年・{int(last['salary']):,})", ""
        if e["status"] == "released":
            return 3, "自由契約", ""
        if e["status"] == "declared":
            reason = e["offers"][-1].get("reason") if e["offers"] else None
            return 3, "FA 宣言", neg.reason(reason) if reason in neg.axes else ""
        if not e["offers"]:
            return 1, "未提示", ""
        left = draftmod.offers_left(e, neg)
        reason = e["offers"][-1].get("reason")
        return 0, f"断られた(残り {left} 回)", neg.reason(reason) if reason in neg.axes else "条件が合わない"

    def _renewal_public(self, e) -> dict:
        neg = self.state.negotiation_settings
        order, status, reason = self._renewal_status(e)
        return {
            "player_id": e["player_id"], "name": e["name"], "role": e["role"], "position": e["position"], "position_label": POSITION_LABELS.get(e["position"], ""), "age": e["age"],
            "old_salary": e["old_salary"], "old_text": "-" if e["old_salary"] is None else f"{int(e['old_salary']):,} 万円",
            "auto_salary": int(e["auto_salary"]), "auto_text": f"{int(e['auto_salary']):,} 万円", "expected": e["expected"],
            "status": e["status"] if e["status"] != "pending" else ("refused" if e["offers"] else "pending"), "status_label": status, "reason": reason,
            "offers": [{"years": o["years"], "salary": o["salary"], "salary_text": f"{int(o['salary']):,} 万円", "accepted": o["accepted"], "reason": "" if o["accepted"] else (neg.reason(o["reason"]) if o.get("reason") in neg.axes else "条件が合わない")} for o in e["offers"]],
            "offers_left": draftmod.offers_left(e, neg), "max_offers": neg.max_offers,
        }

    def _renewal_info(self, proc) -> dict:
        """契約更改の段階の、自球団の情報(公開用)。"""
        state = self.state
        my = state.my_team_id
        ctx = self._contract_ctx()
        entries = [e for e in proc.negotiations.values() if e["team_id"] == my]
        counts = {"pending": 0, "refused": 0, "accepted": 0, "released": 0, "declared": 0}
        for e in entries:
            counts[e["status"] if e["status"] in ("accepted", "released", "declared") else "refused" if e["offers"] else "pending"] += 1
        team = self._team(my)
        cap = ctx.cap(my)
        status = {pid: st for pid, (st, _, _) in self._contract_statuses(proc).items()}
        roster_counts = {k: sum(1 for v in status.values() if v == k) for k in CONTRACT_STATUS_LABELS}
        over = draftmod.over_cap(team, ctx) if ctx.hard() else 0
        open_n = counts["pending"] + counts["refused"]
        return {
            "counts": counts, "total": len(entries), "open": open_n, "fa_required": int(famod.fa_settings(state.negotiation_settings)["seasons_required"]),
            "unoffered": counts["pending"],
            "roster_counts": roster_counts, "status_labels": dict(CONTRACT_STATUS_LABELS), "players": len(team.players), "over": over, "over_text": f"{over:,} 万円",
            "can_next": open_n == 0 and over <= 0,
            "none_max_ratio": float(state.negotiation_settings.money_none["max_ratio"]) if state.money_rule == "none" else None,
            "salary_editable": True, "max_years": state.contract_settings.max_years, "min_years": state.contract_settings.min_years,
            "minimum_salary": state.contract_settings.minimum, "rounding": state.contract_settings.rounding, "max_offers": state.negotiation_settings.max_offers,
            "projected_total": draftmod.projected_total(team, proc), "projected_text": f"{draftmod.projected_total(team, proc):,} 万円", "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "released": [self._renewal_public(e) for e in entries if e["status"] == "released"],
            "declared": [self._renewal_public(e) for e in entries if e["status"] == "declared"],
            "axes": [{"key": k, "label": state.negotiation_settings.label(k), "reason": state.negotiation_settings.reason(k)} for k in state.negotiation_settings.axes],
        }

    def _renewal_table(self, role: str, kind: str, sort: str | None, order: str | None, season: str | None) -> dict:
        proc = self._proc()
        usage = ROSTER_BASE_COLUMNS[role][2]
        cols = [
            ROSTER_BASE_COLUMNS[role][0], ROSTER_BASE_COLUMNS[role][1], usage,
            {"key": "salary", "label": "現在の年俸", "description": "今の契約の年俸(万円)", "type": "count", "better": "high"},
            {"key": "auto", "label": "自動案の年俸", "description": "自動案(1 年)の年俸(万円)。見込みの WAR から算定", "type": "count", "better": "high"},
            {"key": "status", "label": "状態", "description": "未提示・断られた(残りの回数)・更改済", "type": "text", "better": "low"},
            {"key": "reason", "label": "理由", "description": "最後に断られた理由", "type": "text", "better": "low"},
            {"key": "fa", "label": "FA 権", "description": "一軍に登録されたシーズンの数。7 シーズンで FA 権。FA 権がある選手は断ると FA を宣言する", "type": "count", "better": "high"},
        ]
        extra = {}
        for e in proc.negotiations.values():
            if e["team_id"] != self.state.my_team_id or e["status"] == "released":
                continue
            o, status, reason = self._renewal_status(e)
            old = e["old_salary"]
            pl = next((q for q in self._my_team().players if q.id == e["player_id"]), None)
            seasons = famod.seasons_of(pl) if pl is not None else 0
            extra[e["player_id"]] = {"salary": (None, "—") if old is None else (int(old), f"{int(old):,}"), "auto": (int(e["auto_salary"]), f"{int(e['auto_salary']):,}"), "status": (o, status), "reason": (reason or "~", reason or ""), "fa": (seasons, f"あり({seasons})" if e["context"].get("fa_holder") else str(seasons))}
        if not sort:
            sort, order = "status", "asc"
        table = self.roster_table(self.offseason_players("renewal"), role, kind, sort, order, season, base_columns=cols, extra_values=extra)
        pub = {e["player_id"]: self._renewal_public(e) for e in proc.negotiations.values() if e["team_id"] == self.state.my_team_id}
        for r in table["rows"]:
            r["renewal"] = pub.get(r["player_id"])
        table["phase"] = "renewal"
        return table

    def _renewal_entry(self, player_id: str):
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        e = proc.negotiations.get(str(player_id))
        if e is None or e["team_id"] != team.id:
            raise ValueError("その選手は、自球団の更改の対象ではありません")
        if e["status"] != "pending":
            raise ValueError("その選手の更改は、もう決まっています")
        return proc, team, e

    def offseason_renew_auto(self) -> dict:
        """「自動案でまとめて更改」:未提示の全員に自動案(1 年・算定した年俸)を提示する(D-244)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        ctx = self._contract_ctx()
        default_years = self.state.contract_settings.default_years
        for e in [e for e in draftmod.open_entries(proc, team.id) if not e["offers"]]:
            draftmod.make_offer(team, e, default_years, int(e["auto_salary"]), proc, ctx)
        self.dirty = True
        return self.offseason_view()

    def offseason_offer(self, player_id: str, years: int, salary: int | None = None) -> dict:
        """個別の提示(年数 1〜5、年俸はゆるい以上で変えられる。D-244、D-246、D-252)。戻り値は画面の情報と、この提示の答え。"""
        state = self.state
        proc, team, e = self._renewal_entry(player_id)
        cs = state.contract_settings
        try:
            years = int(years)
        except (TypeError, ValueError):
            raise ValueError("年数は整数で入れてください") from None
        if not cs.min_years <= years <= cs.max_years:
            raise ValueError(f"年数は {cs.min_years}〜{cs.max_years} 年にしてください(値: {years})")
        auto = int(e["auto_salary"])
        if salary is None or salary == "":
            salary = auto
        elif state.money_rule == "none":
            salary = self._none_salary(salary, auto)
        else:
            try:
                salary = int(salary)
            except (TypeError, ValueError):
                raise ValueError("年俸は整数(万円)で入れてください") from None
            if salary < cs.minimum:
                raise ValueError(f"年俸は最低年俸({cs.minimum:,} 万円)以上にしてください")
            if salary > cs.base_budget:
                raise ValueError(f"年俸が大きすぎます({cs.base_budget:,} 万円まで)")
            ctx = self._contract_ctx()
            cap = ctx.cap(team.id)
            if ctx.hard() and salary > auto and cap is not None and draftmod.projected_total(team, proc) - auto + salary > cap:
                room = cap - (draftmod.projected_total(team, proc) - auto)
                raise ValueError(f"この年俸では、見込みの総年俸が予算の上限を超えます(この選手に出せるのは {max(auto, room):,} 万円まで)")
        if draftmod.offers_left(e, state.negotiation_settings) <= 0:
            raise ValueError("提示の回数を使い切りました")
        rec = draftmod.make_offer(team, e, years, int(salary), proc, self._contract_ctx())
        self.dirty = True
        view = self.offseason_view()
        view["last_offer"] = {"player_id": e["player_id"], "name": e["name"], "accepted": rec["accepted"], "years": rec["years"], "salary": rec["salary"], "reason": "" if rec["accepted"] else state.negotiation_settings.reason(rec["reason"]) if rec.get("reason") in state.negotiation_settings.axes else "条件が合わない", "released": e["status"] == "released"}
        return view

    def _none_salary(self, salary, calc: int) -> int:
        """お金のルール「なし」の提示の年俸:算定の 1.0〜1.3 倍(設定値)。D-273。"""
        try:
            salary = int(salary)
        except (TypeError, ValueError):
            raise ValueError("年俸は整数(万円)で入れてください") from None
        ratio = float(self.state.negotiation_settings.money_none["max_ratio"])
        top = int(calc * ratio)
        if salary < calc:
            raise ValueError(f"お金のルール「なし」では、算定({calc:,} 万円)より低い年俸は出せません")
        if salary > top:
            raise ValueError(f"お金のルール「なし」では、年俸は算定の {ratio:g} 倍({top:,} 万円)までです")
        return salary

    def offseason_renew_release(self, player_id: str) -> dict:
        """交渉をやめて自由契約にする(市場へ)。"""
        proc, team, e = self._renewal_entry(player_id)
        draftmod.release_entry(team, e, proc)
        self.dirty = True
        return self.offseason_view()

    def _over_hard_cap(self, team) -> bool:
        ctx = self._contract_ctx()
        return ctx.hard() and draftmod.over_cap(team, ctx) > 0

    # ---- 契約の画面(契約更改と自由契約をまとめたもの。D-272) ----

    def _contract_players(self, proc) -> list:
        """契約の画面の選手:自球団の今の選手と、この手続きで自球団を離れた選手(自由契約・FA 宣言)。"""
        my = self.state.my_team_id
        team = self._my_team()
        gone = {x["player_id"] for x in proc.released if x["team_id"] == my}
        gone |= {pid for pid, x in proc.fa_info.items() if x["former_team"] == my}
        out = list(team.players)
        seen = {p.id for p in out}
        for p in list(proc.market) + list(proc.fa_pool):
            if p.id in gone and p.id not in seen:
                out.append(p)
                seen.add(p.id)
        return out

    def _contract_statuses(self, proc) -> dict:
        """選手 ID → (状態のキー, 並び順, 表示)。"""
        my = self.state.my_team_id
        neg = self.state.negotiation_settings
        team_ids = {p.id for p in self._my_team().players}
        released = {x["player_id"] for x in proc.released if x["team_id"] == my}
        out = {}
        for p in self._contract_players(proc):
            e = proc.negotiations.get(p.id)
            if e is not None and e["team_id"] == my:
                if e["status"] == "accepted":
                    out[p.id] = ("accepted", 3, "更改済")
                elif e["status"] == "declared":
                    out[p.id] = ("declared", 4, "FA 宣言")
                elif e["status"] == "released":
                    out[p.id] = ("released", 5, "自由契約")
                elif e["offers"]:
                    out[p.id] = ("refused", 0, f"保留(残り {draftmod.offers_left(e, neg)} 回)")
                else:
                    out[p.id] = ("unoffered", 1, "未提示")
            elif p.id in team_ids:
                out[p.id] = ("contracted", 2, "契約中")
            elif p.id in released:
                out[p.id] = ("released", 5, "自由契約")
            else:
                out[p.id] = ("declared", 4, "FA 宣言")
        return out

    def _metric_cells(self, p, view, config) -> dict:
        """選手の成績の値(キー → (並べ替え用の数, 表示))。出場(usage)と WAR を含む。"""
        role = p.role
        group = view.records.batters if role == "batter" else view.records.pitchers
        counts = group.get(p.id)
        cells = dict(_values(config, role, counts, view.baselines, view.park_factor(p.id) if role == "batter" else None, (view.override or {}).get(p.id))) if counts is not None else {}
        line = view.war.get(p.id)
        if line is not None:
            cells.update(_war_values(line))
            cells["war_all"] = cells["war"] if role == "batter" else cells["war_ra"]
        if counts is None:
            cells["usage"] = (None, "—")
        elif role == "batter":
            cells["usage"] = (Fraction(int(counts.get("PA", 0))), f"{int(counts.get('PA', 0))} 打席")
        else:
            cells["usage"] = (Fraction(int(counts.get("OUTS", 0))), f"{innings_text(int(counts.get('OUTS', 0)))} 回")
        return cells

    def contract_table(self, group: str = "all", kind: str = "war", sort: str | None = None, order: str | None = None, season: str | None = None, status: str = "all") -> dict:
        """契約の画面の表(公開用。D-272):自球団の全選手。列は 名前・選んだ指標(既定は WAR)・ポジション・年齢・今の契約・今回の提示・状態・出場
        (投手・捕手・内野手・外野手を選んだときは、選んだ種類の成績の列も)。初期は WAR の低い順。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        if group not in CONTRACT_GROUPS:
            raise ValueError(f"全員・投手・捕手・内野手・外野手のどれかを選んでください(値: {group!r})")
        if status not in ("all", *CONTRACT_STATUS_LABELS):
            raise ValueError(f"状態の絞り込みが正しくありません(値: {status!r})")
        if season not in (None, "current") and season not in [x["key"] for x in self.roster_seasons()]:
            raise ValueError(f"このシーズンは選べません(値: {season!r})")
        view = self._season_view(None if season in (None, "current") else season)
        config = metrics_config()
        my = self.state.my_team_id
        neg = self.state.negotiation_settings
        role = None if group == "all" else ("pitcher" if group == "pitcher" else "batter")
        war_col = {"key": "war_all", "label": "WAR", "description": "野手は WAR、投手は WAR(失点版)", "type": "metric", "category": "war", "better": "high"}
        if role is None:
            kind = "war"
            kind_cols: list[dict] = []
            default_metric = war_col
        else:
            if kind == WAR_KIND:
                kind_cols = list(WAR_COLUMNS[role])
            elif kind in KIND_LABELS:
                kind_cols = table_cols(config, role, kind)
            else:
                raise ValueError(f"基本・セイバー・WAR を選んでください(値: {kind!r})")
            kind_cols = [c for c in kind_cols if c["key"] not in ("PA", "OUTS", "plate_appearances", "innings")]
            default_metric = next(c for c in WAR_COLUMNS[role] if c["key"] == ("war" if role == "batter" else "war_ra"))
        base = {c["key"]: c for c in CONTRACT_COLUMNS}
        if not sort:
            sort, order = default_metric["key"], order or "asc"
        if sort in base:
            metric = default_metric
            info = base[sort]
        elif sort == "war_all" or (role is not None and sort in [c["key"] for c in kind_cols]):
            metric = war_col if sort == "war_all" else next(c for c in kind_cols if c["key"] == sort)
            info = metric
        elif role is not None and sort in _WAR_SORT_KEYS[role]:
            metric = info = next(c for c in WAR_COLUMNS[role] if c["key"] == sort)
        elif role is not None and is_sortable(config, role, sort):
            metric = info = column_info(config, role, sort)
        else:
            raise ValueError(f"並び順に使えない列です(値: {sort!r})")
        if order not in ("asc", "desc"):
            order = "desc" if info.get("better", "high") == "high" else "asc"
        columns = [metric] + CONTRACT_COLUMNS + [c for c in kind_cols if c["key"] != metric["key"]]
        statuses = self._contract_statuses(proc)
        team = self._my_team()
        mins, min_batters = self._mins()
        ok = draftmod.releasable(team.players, mins, min_batters)
        over = self._over_hard_cap(team)
        in_team = {p.id for p in team.players}
        next_year = proc.year + 1
        positions = list(POSITION_LABELS)
        rows = []
        for p in self._contract_players(proc):
            if role is not None and p.position not in CONTRACT_GROUPS[group][1]:
                continue
            st, st_order, st_label = statuses[p.id]
            if status != "all" and st != status:
                continue
            cells = self._metric_cells(p, view, config)
            e = proc.negotiations.get(p.id) if p.id in proc.negotiations and proc.negotiations[p.id]["team_id"] == my else None
            if e is not None:
                old = e["old_salary"]
                cells["contract"] = (old, "—" if old is None else f"{int(old):,}(満了)")
                if e["status"] == "accepted" or e["offers"]:
                    last = e["offers"][-1] if e["offers"] else {"years": e.get("ai_years", 1), "salary": e["auto_salary"]}
                    cells["offer"] = (int(last["salary"]), f"{int(last['salary']):,}・{last['years']} 年")
                else:
                    cells["offer"] = (int(e["auto_salary"]), f"{int(e['auto_salary']):,}・{self.state.contract_settings.default_years} 年(案)")
            elif p.contract and p.id in in_team:
                n = remaining_years(p.contract, next_year)
                cells["contract"] = (int(p.contract["salary"]), f"{int(p.contract['salary']):,}(残り {n} 年)")
                cells["offer"] = (None, "—")
            else:
                cells["contract"] = (None, "—")
                cells["offer"] = (None, "—")
            cells["pos"] = (positions.index(p.position), POSITION_LABELS[p.position])
            cells["age"] = (p.age, f"{p.age}歳")
            cells["status"] = (st_order, st_label)
            keys = [c["key"] for c in columns]
            values = {k: (cells.get(k) or (None, "—"))[1] for k in keys}
            pending = e is not None and e["status"] == "pending"
            rows.append({
                "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
                "values": values, "status": st, "in_team": p.id in in_team,
                "can_release": pending or (p.id in in_team and (p.id in ok or over)),
                "can_offer": pending and draftmod.offers_left(e, neg) > 0,
                "renewal": self._renewal_public(e) if e is not None else None,
                "contract": self._contract_public(p, next_year) if p.id in in_team and p.contract else None,
                "_sort": (cells.get(sort) or (None, ""))[0],
            })
        present = sorted((r for r in rows if r["_sort"] is not None), key=lambda r: r["player_id"])
        present.sort(key=lambda r: r["_sort"], reverse=order == "desc")
        missing = sorted((r for r in rows if r["_sort"] is None), key=lambda r: r["player_id"])
        rows = present + missing
        for r in rows:
            r.pop("_sort")
        return {
            "phase": "contract", "group": group, "groups": [{"key": k, "label": v[0]} for k, v in CONTRACT_GROUPS.items()],
            "kind": kind, "kinds": [] if role is None else [{"key": k, "label": v} for k, v in KIND_LABELS.items()] + [{"key": WAR_KIND, "label": "WAR"}],
            "status": status, "statuses": [{"key": "all", "label": "全部"}] + [{"key": k, "label": v} for k, v in CONTRACT_STATUS_LABELS.items()],
            "columns": columns, "sort": info, "order": order, "extra_column": None, "rows": rows,
            "season": view.key, "season_label": view.label, "seasons": self.roster_seasons(),
        }

    def offseason_contract_release(self, player_id: str) -> dict:
        """契約の画面の「自由契約にする」(D-272):交渉中の選手は交渉をやめて、契約が残る選手(複数年の途中・更改済)は残りの契約を消して、市場へ。
        最低人数を割る選手は外せない(標準以上で上限を超えている間は外せる。D-255)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        pid = str(player_id)
        e = proc.negotiations.get(pid)
        if e is not None and e["team_id"] == team.id and e["status"] == "pending":
            draftmod.release_entry(team, e, proc)
            self.dirty = True
            return self.offseason_view()
        return self.offseason_release([pid])

    # ---- FA(F3-2c。D-258〜D-264) ----

    def _fa_row_status(self, pid: str, info: dict, names: dict) -> tuple[int, str]:
        proc = self._proc()
        if info["status"] == "signed":
            return 3, f"契約({names.get(info['team_id'], '')}・{info['years']} 年・{int(info['salary']):,})"
        if info["status"] == "unsigned":
            return 4, "未契約(市場へ)"
        mine = proc.fa_offers.get(pid)
        if mine:
            return 0, f"提示中({mine['years']} 年・{int(mine['salary']):,})"
        return 1, "未契約"

    def _fa_info(self, proc) -> dict:
        """FA の段階の情報(公開用)。"""
        state = self.state
        names = self._team_names()
        fa = famod.fa_settings(state.negotiation_settings)
        my = state.my_team_id
        ctx = self._contract_ctx()
        team = self._team(my) if my else None
        committed = sum(int(o["salary"]) for o in proc.fa_offers.values())
        cap = ctx.cap(my) if my else None
        results = [{**x, "former_team_name": names.get(x["former_team"], ""), "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == my or x["former_team"] == my, "stayed": x["team_id"] == x["former_team"]} for x in proc.fa_results]
        counts = {"declared": len(proc.fa_info), "signed": len(proc.fa_results), "open": sum(1 for x in proc.fa_info.values() if x["status"] == "open"), "unsigned": sum(1 for x in proc.fa_info.values() if x["status"] == "unsigned")}
        return {
            "round": min(int(proc.fa_round), int(fa["rounds"])), "rounds": int(fa["rounds"]), "done": bool(proc.fa_done), "counts": counts,
            "offers": [{"player_id": pid, "name": proc.fa_info[pid]["name"], "years": o["years"], "salary": o["salary"], "salary_text": f"{int(o['salary']):,} 万円"} for pid, o in proc.fa_offers.items()],
            "committed": committed, "committed_text": f"{committed:,} 万円",
            "space": None if team is None else famod.MAX_ROSTER - len(team.players) - len(proc.fa_offers),
            "total": None if team is None else team_salary(team), "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "salary_editable": True, "none_max_ratio": float(state.negotiation_settings.money_none["max_ratio"]) if state.money_rule == "none" else None,
            "max_years": state.contract_settings.max_years, "min_years": state.contract_settings.min_years, "minimum_salary": state.contract_settings.minimum, "rounding": state.contract_settings.rounding,
            "results": results,
            "note": f"FA を宣言した選手に、年数(1〜5 年)" + ("と年俸" if state.money_rule != "none" else "") + f"を提示できます。全 {int(fa['rounds'])} ラウンドで、各ラウンドの終わりに選手が受けた提示の中から志望(年俸・出場機会・勝利。出場機会と勝利は提示した球団での見込み)で一番よいものを選びます。"
            + "決まらなければ次のラウンドへ、最後のラウンドでも決まらなければ自由契約市場に回ります。元の球団も同じ立場で提示します。補償はありません。"
            + (f"お金のルール「なし」では年俸は算定の 1.0〜{float(state.negotiation_settings.money_none['max_ratio']):g} 倍です。" if state.money_rule == "none" else "")
            + ("標準以上では、総年俸と提示中の年俸の合計が予算の上限を超える提示はできません。" if ctx.hard() else "")
            + "「ラウンドを締める」で、AI 球団の提示と合わせて結果が出ます。「次の手続きへ」「おまかせ」は、残りのラウンドを AI と同じ方針で進めます。",
        }

    def _fa_table(self, role: str, kind: str, sort: str | None, order: str | None, season: str | None) -> dict:
        proc = self._proc()
        names = self._team_names()
        cols = [
            ROSTER_BASE_COLUMNS[role][0], ROSTER_BASE_COLUMNS[role][1], ROSTER_BASE_COLUMNS[role][2],
            {"key": "former", "label": "前の所属", "description": "FA を宣言した球団", "type": "text", "better": "low"},
            {"key": "calc", "label": "算定年俸", "description": "見込みの WAR から算定した年俸(万円)", "type": "count", "better": "high"},
            {"key": "status", "label": "状態", "description": "未契約・提示中・契約(球団・年数・年俸)・未契約(市場へ)", "type": "text", "better": "low"},
        ]
        players = self.offseason_players("fa")
        extra = {}
        for p in players:
            info = proc.fa_info[p.id]
            o, status = self._fa_row_status(p.id, info, names)
            extra[p.id] = {"former": (names.get(info["former_team"], ""), names.get(info["former_team"], "")), "calc": (int(info["calc_salary"]), f"{int(info['calc_salary']):,}"), "status": (o, status)}
        if not sort:
            sort, order = "calc", "desc"
        table = self.roster_table(players, role, kind, sort, order, season, base_columns=cols, extra_values=extra)
        for r in table["rows"]:
            info = proc.fa_info[r["player_id"]]
            mine = proc.fa_offers.get(r["player_id"])
            r["fa"] = {"status": info["status"], "former_team": info["former_team"], "former_team_name": names.get(info["former_team"], ""), "calc_salary": int(info["calc_salary"]), "calc_text": f"{int(info['calc_salary']):,} 万円", "expected": round(float(info["expected"]), 2), "offer": mine}
        table["phase"] = "fa"
        return table

    def _fa_open_player(self, player_id: str):
        proc = self._proc()
        if proc.phase != "fa" or proc.fa_done:
            raise ValueError("今は FA の段階ではありません")
        info = proc.fa_info.get(str(player_id))
        if info is None or info["status"] != "open":
            raise ValueError("その選手は、FA で交渉できる選手ではありません")
        return proc, info

    def offseason_fa_offer(self, player_id: str, years: int, salary: int | None = None) -> dict:
        """あなたの球団の FA の提示(今のラウンド。出し直しは上書き)。"""
        state = self.state
        proc, info = self._fa_open_player(player_id)
        team = self._my_team()
        cs = state.contract_settings
        try:
            years = int(years)
        except (TypeError, ValueError):
            raise ValueError("年数は整数で入れてください") from None
        if not cs.min_years <= years <= cs.max_years:
            raise ValueError(f"年数は {cs.min_years}〜{cs.max_years} 年にしてください(値: {years})")
        calc = int(info["calc_salary"])
        if salary in (None, ""):
            salary = calc
        elif state.money_rule == "none":
            salary = self._none_salary(salary, calc)
        else:
            try:
                salary = int(salary)
            except (TypeError, ValueError):
                raise ValueError("年俸は整数(万円)で入れてください") from None
            if salary < cs.minimum:
                raise ValueError(f"年俸は最低年俸({cs.minimum:,} 万円)以上にしてください")
            if salary > cs.base_budget:
                raise ValueError(f"年俸が大きすぎます({cs.base_budget:,} 万円まで)")
        others = {pid: o for pid, o in proc.fa_offers.items() if pid != str(player_id)}
        if len(team.players) + len(others) + 1 > famod.MAX_ROSTER:
            raise ValueError("空き枠が足りません(70 人。提示中の選手も数えます)")
        ctx = self._contract_ctx()
        cap = ctx.cap(team.id)
        if ctx.hard() and cap is not None and team_salary(team) + sum(int(o["salary"]) for o in others.values()) + salary > cap:
            room = cap - team_salary(team) - sum(int(o["salary"]) for o in others.values())
            raise ValueError(f"総年俸と提示中の年俸の合計が予算の上限を超えます(この選手に出せるのは {max(0, room):,} 万円まで)")
        proc.fa_offers[str(player_id)] = {"years": years, "salary": int(salary)}
        self.dirty = True
        return self.offseason_view()

    def offseason_fa_cancel(self, player_id: str) -> dict:
        proc, _ = self._fa_open_player(player_id)
        proc.fa_offers.pop(str(player_id), None)
        self.dirty = True
        return self.offseason_view()

    def offseason_fa_close(self) -> dict:
        """ラウンドを締める:AI の提示と合わせて、選手が選ぶ。戻り値は画面の情報と、このラウンドの結果。"""
        proc = self._proc()
        if proc.phase != "fa" or proc.fa_done:
            raise ValueError("今は FA の段階ではありません")
        rnd = proc.fa_round
        signed = famod.close_round(self.state.league, proc, self._contract_ctx(), self.state.my_team_id)
        self.dirty = True
        view = self.offseason_view()
        names = self._team_names()
        view["last_round"] = {"round": rnd, "signed": [{**x, "team_name": names.get(x["team_id"], ""), "former_team_name": names.get(x["former_team"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == self.state.my_team_id} for x in signed]}
        return view

    def offseason_release(self, player_ids: list[str]) -> dict:
        """操作する球団が選手を手放す(自由契約の段階)。最低人数を割る選び方は受け付けない(警告は画面側)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        mins, min_batters = self._mins()
        ids = [str(x) for x in player_ids]
        pending = [pid for pid in ids if pid in proc.negotiations and proc.negotiations[pid]["team_id"] == team.id and proc.negotiations[pid]["status"] == "pending"]
        if pending:
            raise ValueError("更改の交渉中の選手は、提示のパネルの「自由契約にする」で手放してください")
        chosen = [p for p in team.players if p.id in ids]
        if len(chosen) != len(set(ids)):
            raise ValueError("自分の球団にいない選手が含まれています")
        rest = [p for p in team.players if p.id not in ids]
        worse = draftmod.new_shortages(team.players, rest, mins, min_batters)
        if worse and not self._over_hard_cap(team):  # 予算の上限を超えている間は、最低人数を割っても外せる(不足は完了のときに最低年俸で自動補充。F3-2b)
            raise ValueError("最低人数を割ってしまいます:" + "、".join(f"{'野手' if pos == 'batter' else POSITION_LABELS[pos]} があと {n} 人足りなくなります" for pos, n in worse.items()))
        draftmod.release_players(team, chosen, proc)
        proc.my_release_done = True
        self.dirty = True
        return self.offseason_view()

    def offseason_next(self) -> dict:
        """「次の手続きへ」(D-271):自分の操作を終えて次の段階へ進む(AI の代行はしない)。
        契約:全員が決まり、標準以上で上限を超えていなければ、AI 球団の予算超過の解消と自由契約を行って FA へ。
        FA:自球団は追加の提示をせず(今のラウンドの提示は有効)、残りのラウンドを締める。ドラフト・市場:自球団の残りの番はパス。
        市場の次は完了(自動補充 → 次のシーズン)。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        ctx = self._contract_ctx()
        my = state.my_team_id
        stage = stage_of(proc.phase)
        if stage == "contract":
            if my is not None and draftmod.open_entries(proc, my):
                n = len(draftmod.open_entries(proc, my))
                raise ValueError(f"更改が決まっていない選手が {n} 人います。全員を更改か自由契約にしてから進めてください")
            if my is not None and ctx.hard() and draftmod.over_cap(self._my_team(), ctx) > 0:
                raise ValueError(f"総年俸が予算の上限を {draftmod.over_cap(self._my_team(), ctx):,} 万円超えています。自由契約で減らしてから進めてください")
            draftmod.finish_contract_stage(state.league, proc, ctx, sd, state.draft_settings, my, mins, min_batters, my_ai=False)
        elif stage == "fa":
            famod.run_all_rounds(state.league, proc, ctx, my, my_ai=False)
            draftmod.next_phase(proc)
        elif stage in ("draft", "market"):
            self._run_turns(my_ai=False)
            draftmod.next_phase(proc)
        return self._after_stage()

    def _run_turns(self, my_ai: bool) -> None:
        """ドラフト・市場の残りの番を最後まで。my_ai なら自球団の番も AI の方針、そうでなければ自球団の番はパス。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        ctx = self._contract_ctx()
        my = None if my_ai else state.my_team_id
        while draftmod.run_ai_turns(state.league, proc, sd, state.draft_settings, my, mins, min_batters, ctx=ctx):
            draftmod.pass_turn(proc, my, "pass")

    def _after_stage(self) -> dict:
        state = self.state
        proc = self._proc()
        if proc.phase == "done":
            mins, min_batters = self._mins()
            filled = draftmod.finalize(state.league, proc, state.gen_config, state.name_parts, state.draft_settings, state.scout_sd_map(), state.calibration, mins, min_batters, ctx=self._contract_ctx())
            self._finish_offseason(filled)
            self.dirty = True
            return {"finished": True, "summary": self.offseason_summary(), "status": self.status()}
        self.dirty = True
        return self.offseason_view()

    def offseason_stage_auto(self) -> dict:
        """「この段階をおまかせ」(D-271):今の段階だけを AI の方針で、自球団の分も代行して進め、次の段階の入口で止まる。
        戻り値の auto_log は、自球団の分として AI が行ったことの一覧。市場では完了まで進めて結果を返す。"""
        state = self.state
        proc = self._proc()
        my = state.my_team_id
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        ctx = self._contract_ctx()
        stage = stage_of(proc.phase)
        names = self._team_names()
        before_status = {pid: e["status"] for pid, e in proc.negotiations.items() if e["team_id"] == my}
        n_released, n_picks, n_fa = len(proc.released), len(proc.picks), len(proc.fa_results)
        if stage == "contract":
            draftmod.finish_contract_stage(state.league, proc, ctx, sd, state.draft_settings, my, mins, min_batters, my_ai=True)
        elif stage == "fa":
            famod.run_all_rounds(state.league, proc, ctx, my, my_ai=True)
            draftmod.next_phase(proc)
        elif stage in ("draft", "market"):
            self._run_turns(my_ai=True)
            draftmod.next_phase(proc)
        else:
            raise ValueError("この段階には、おまかせできる操作がありません")
        log = []
        for pid, old in before_status.items():
            e = proc.negotiations[pid]
            if e["status"] == old or old != "pending":
                continue
            if e["status"] == "accepted":
                o = e["offers"][-1]
                log.append({"kind": "renew", "player_id": pid, "name": e["name"], "text": f"更改:{e['name']}({o['years']} 年・{int(o['salary']):,} 万円)"})
            elif e["status"] == "declared":
                log.append({"kind": "declare", "player_id": pid, "name": e["name"], "text": f"FA 宣言:{e['name']}"})
            elif e["status"] == "released":
                log.append({"kind": "release", "player_id": pid, "name": e["name"], "text": f"自由契約(交渉決裂):{e['name']}"})
        for x in proc.released[n_released:]:
            if x["team_id"] == my and x.get("note") != "negotiation":
                why = "予算超過" if x.get("note") == "budget" else "自由契約"
                log.append({"kind": "release", "player_id": x["player_id"], "name": x["name"], "text": f"{why}:{x['name']}"})
        for x in proc.fa_results[n_fa:]:
            if x["team_id"] == my:
                log.append({"kind": "fa", "player_id": x["player_id"], "name": x["name"], "text": f"FA で獲得:{x['name']}({x['years']} 年・{int(x['salary']):,} 万円)"})
            elif x["former_team"] == my:
                log.append({"kind": "fa_out", "player_id": x["player_id"], "name": x["name"], "text": f"FA で移籍:{x['name']} → {names.get(x['team_id'], '')}"})
        for x in proc.picks[n_picks:]:
            if x["team_id"] == my and x["player_id"]:
                where = f"ドラフト {x['round']} 巡" if x["phase"] == "draft" else "市場"
                log.append({"kind": x["phase"], "player_id": x["player_id"], "name": x["name"], "text": f"{where}:{x['name']}"})
        out = self._after_stage()
        out["auto_log"] = {"stage": stage, "stage_label": STAGE_LABELS[stage], "items": log}
        return out

    def offseason_advance(self) -> dict:
        """AI の番を、自分の番か段階の終わりまで進める。"""
        state = self.state
        proc = self._proc()
        if proc.phase not in ("draft", "market"):
            raise ValueError("今は指名の段階ではありません")
        mins, min_batters = self._mins()
        draftmod.run_ai_turns(state.league, proc, state.scout_sd_map(), state.draft_settings, state.my_team_id, mins, min_batters, ctx=self._contract_ctx())
        self.dirty = True
        return self.offseason_view()

    def offseason_pick(self, player_id: str) -> dict:
        """自分の番に選手を指名(獲得)し、次の自分の番まで AI を進める。"""
        state = self.state
        proc = self._proc()
        team = self._my_team()
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        if proc.phase not in ("draft", "market") or proc.current_team() != team.id or draftmod.phase_finished(proc):
            raise ValueError("今は自分の番ではありません(「次の自分の番まで進める」を押してください)")
        player = next((p for p in draftmod.pool_of(proc) if p.id == player_id), None)
        if player is None:
            raise ValueError("その選手は一覧にいません")
        if len(team.players) >= draftmod.MAX_ROSTER:
            raise ValueError("空き枠がありません(70 人)")
        ctx = self._contract_ctx()
        offer = draftmod.offer_for(team, player, proc, ctx)
        if offer is not None and not draftmod.can_afford(team, offer[0], ctx):
            raise ValueError(f"この契約(年俸 {offer[0]:,} 万円)を結ぶと、予算の上限を超えます")
        draftmod.take(proc, team, player, sd, state.draft_settings, ctx)
        draftmod.run_ai_turns(state.league, proc, sd, state.draft_settings, team.id, mins, min_batters, ctx=ctx)
        self.dirty = True
        return self.offseason_view()

    def offseason_pass(self) -> dict:
        state = self.state
        proc = self._proc()
        team = self._my_team()
        mins, min_batters = self._mins()
        if proc.phase not in ("draft", "market") or proc.current_team() != team.id or draftmod.phase_finished(proc):
            raise ValueError("今は自分の番ではありません")
        draftmod.pass_turn(proc, team.id, "pass")
        draftmod.run_ai_turns(state.league, proc, state.scout_sd_map(), state.draft_settings, team.id, mins, min_batters, ctx=self._contract_ctx())
        self.dirty = True
        return self.offseason_view()

    def offseason_auto(self) -> dict:
        """「おまかせ」:残りの手続きを AI と同じ方針で最後まで進め、次のシーズンを始める(D-207)。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        filled = draftmod.complete(state.league, proc, state.gen_config, state.name_parts, state.draft_settings, state.scout_sd_map(), state.calibration, state.my_team_id, mins, min_batters, ctx=self._contract_ctx())
        self._finish_offseason(filled)
        return {"finished": True, "summary": self.offseason_summary(), "status": self.status()}

    def transactions(self, year: int | None = None) -> dict:
        """指名・獲得・自由契約の履歴(公開用)。"""
        names = self._team_names()
        rows = [x for x in self.state.transactions if year is None or x["year"] == year]
        return {"years": sorted({x["year"] for x in self.state.transactions}), "rows": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x.get("position", ""), ""), "phase_label": PHASE_LABELS.get(x["phase"], x["phase"]), "is_mine": x["team_id"] == self.state.my_team_id} for x in rows]}

    # ---- 選手の一覧の表(自由契約・市場。D-222) ----

    def roster_seasons(self) -> list[dict]:
        """選手の一覧の表で選べるシーズン(今シーズンと、2 シーズン目以降は通算)。"""
        out = [{"key": "current", "label": f"今シーズン({self.state.year}シーズン目)"}]
        if len(self.state.history) > (1 if self.state.procedure is not None else 0):  # 手続き中は、終わったシーズンがすでに履歴にある
            out.append({"key": "career", "label": "通算"})
        return out

    def roster_table(self, players: list, role: str, kind: str = "basic", sort: str | None = None, order: str | None = None, season: str | None = None, base_columns: list | None = None, extra_values: dict | None = None) -> dict:
        """任意の選手の一覧(自球団の全選手、市場の選手)を、個人成績と同じ列・値で表にする(D-222)。
        基本の列(ポジション・年齢・打席か投球回)+ 選んだ種類(基本・セイバー・WAR)の列。並び順は全部の列と、その役割の全指標・WAR の列から選べ、
        表にない指標で並べたときは extra_column で返す(D-131 と同じ)。成績のない選手の値は「—」で、並び順によらず最後。初期は WAR の低い順。"""
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        if season not in (None, "current") and season not in [x["key"] for x in self.roster_seasons()]:
            raise ValueError(f"このシーズンは選べません(値: {season!r})")
        view = self._season_view(None if season in (None, "current") else season)
        config = metrics_config()
        base_cols = base_columns if base_columns is not None else ROSTER_BASE_COLUMNS[role]
        if kind == WAR_KIND:
            cols = list(WAR_COLUMNS[role])
        elif kind in KIND_LABELS:
            cols = table_cols(config, role, kind)
        else:
            raise ValueError(f"基本・セイバー・WAR を選んでください(値: {kind!r})")
        cols = [c for c in cols if c["key"] not in ("PA", "OUTS", "plate_appearances", "innings")]  # 打席・投球回は基本の列にあるので重ねない
        shown = {c["key"]: c for c in base_cols + cols}
        if not sort:  # 初期は WAR の低い順(手放す候補が上に来る。D-222)
            sort = "war" if role == "batter" else "war_ra"
            order = order or "asc"
        if sort in shown:
            info, extra = shown[sort], None
        elif sort in _WAR_SORT_KEYS[role]:
            info = next(c for c in WAR_COLUMNS[role] if c["key"] == sort)
            extra = info
        elif is_sortable(config, role, sort):
            info = column_info(config, role, sort)
            extra = info
        else:
            raise ValueError(f"並び順に使えない列です(値: {sort!r})")
        if order not in ("asc", "desc"):
            order = "desc" if info.get("better", "high") == "high" else "asc"
        keys = [c["key"] for c in base_cols + cols] + ([sort] if extra else [])
        rec = view.records
        group = rec.batters if role == "batter" else rec.pitchers
        names = self._team_names()
        positions = list(POSITION_LABELS)
        rows = []
        for p in players:
            if p.role != role:
                continue
            counts = group.get(p.id)
            metrics = _values(config, role, counts, view.baselines, view.park_factor(p.id) if role == "batter" else None, (view.override or {}).get(p.id)) if counts is not None else {}
            line = view.war.get(p.id)
            war = _war_values(line) if line is not None else {}
            if counts is None:
                usage = (None, "—")
            elif role == "batter":
                usage = (Fraction(int(counts.get("PA", 0))), str(int(counts.get("PA", 0))))
            else:
                usage = (Fraction(int(counts.get("OUTS", 0))), innings_text(int(counts.get("OUTS", 0))))
            base = {"pos": (positions.index(p.position), POSITION_LABELS[p.position]), "age": (p.age, f"{p.age}歳"), "usage": usage}
            if p.contract:
                base["salary"] = (int(p.contract["salary"]), f"{int(p.contract['salary']):,}")
                base["years"] = (remaining_years(p.contract, self.state.year + (1 if self.state.procedure is not None else 0)), str(remaining_years(p.contract, self.state.year + (1 if self.state.procedure is not None else 0))))
            else:
                base["salary"] = (None, "—")
                base["years"] = (None, "—")
            cell = {}
            ext = (extra_values or {}).get(p.id, {})
            for k in keys:
                v = ext.get(k) or base.get(k) or metrics.get(k) or war.get(k)
                cell[k] = v if v is not None else (None, "—")
            row = {
                "player_id": p.id, "name": p.name, "role": role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if role == "batter" else THROWS_LABELS.get(p.throws),
                "team_id": p.team_id, "team_name": names.get(p.team_id, "") if p.team_id else "", "has_stats": counts is not None,
                "values": {k: v[1] for k, v in cell.items()}, "_sort": cell[sort][0],
            }
            rows.append(row)
        present = [r for r in rows if r["_sort"] is not None]
        missing = [r for r in rows if r["_sort"] is None]
        present.sort(key=lambda r: r["player_id"])
        present.sort(key=lambda r: r["_sort"], reverse=order == "desc")
        missing.sort(key=lambda r: r["player_id"])
        rows = present + missing
        for r in rows:
            r.pop("_sort")
        return {
            "role": role, "role_label": ROLE_LABELS[role], "kind": kind, "kind_label": "WAR" if kind == WAR_KIND else KIND_LABELS[kind],
            "columns": base_cols + cols, "sort": info, "order": order, "extra_column": extra, "rows": rows,
            "season": view.key, "season_label": view.label, "seasons": self.roster_seasons(),
            "baseline_note": view.baseline_note if any(c["key"] in _BASELINE_METRICS for c in cols) else None,
            "terms": WAR_TERMS if kind == WAR_KIND else None,
        }

    def offseason_players(self, phase: str) -> list:
        """手続きの画面の表に出す選手:自由契約は自球団の全選手、市場は市場の選手、ドラフトは候補。"""
        proc = self._proc()
        if phase == "renewal":
            ids = {e["player_id"] for e in proc.negotiations.values() if e["team_id"] == self.state.my_team_id and e["status"] != "released"}
            return [p for p in self._my_team().players if p.id in ids]
        if phase == "fa":
            return list(proc.fa_pool) + [p for t in self.state.league.teams for p in t.players if p.id in proc.fa_info] + [p for p in proc.market if p.id in proc.fa_info]
        if phase == "release":
            return list(self._my_team().players)
        if phase == "market":
            return list(proc.market)
        if phase == "draft":
            return list(proc.candidates)
        raise ValueError(f"自由契約か市場を選んでください(値: {phase!r})")

    def offseason_table(self, phase: str, role: str = "batter", kind: str = "basic", sort: str | None = None, order: str | None = None, season: str | None = None) -> dict:
        """自由契約・市場の画面の、成績つきの選手の一覧(公開用。D-222)。市場の行には前の球団を足す。"""
        proc = self._proc()
        if phase == "renewal":
            return self._renewal_table(role, kind, sort, order, season)
        if phase == "fa":
            return self._fa_table(role, kind, sort, order, season)
        table = self.roster_table(self.offseason_players(phase), role, kind, sort, order, season)
        if phase != "release":
            names = self._team_names()
            former = {x["player_id"]: names.get(x["team_id"], "") for x in proc.released}
            former.update({pid: names.get(x["former_team"], "") for pid, x in proc.fa_info.items()})
            ctx = self._contract_ctx()
            my = self.state.my_team_id
            team = self._team(my) if my else None
            pool = {p.id: p for p in self.offseason_players(phase)}
            for r in table["rows"]:
                r["former_team"] = former.get(r["player_id"], "")
                if team is not None:  # 自球団が獲得したときの契約(ドラフトは巡ごとの表、市場は算定)
                    offer = draftmod.offer_for(team, pool[r["player_id"]], proc, ctx)
                    r["offer"] = None if offer is None else {"salary": offer[0], "years": offer[1], "affordable": draftmod.can_afford(team, offer[0], ctx)}
                    r["values"]["salary"] = f"{offer[0]:,}" if offer else "—"
                    r["values"]["years"] = str(offer[1]) if offer else "—"
        else:
            mins, min_batters = self._mins()
            team = self._my_team()
            ok = draftmod.releasable(team.players, mins, min_batters)
            over = self._over_hard_cap(team)
            for r in table["rows"]:
                r["can_release"] = r["player_id"] in ok or over  # 上限を超えている間は、最低人数の選手も外せる
        table["phase"] = phase
        return table

    # ---- ドラフトの振り返り(当たり外れの一覧。D-216) ----

    def review_years(self) -> list[int]:
        """振り返りで選べる入団の年度(入団したシーズンの番号。履歴から。事前運転の入団は含まない)。"""
        return sorted({int(x["year"]) + 1 for x in self.state.transactions if x.get("player_id") and x["phase"] in ("draft", "market", "fill")})

    def _career_totals(self) -> tuple[dict[str, int], dict[str, float]]:
        """選手 ID → 出場(G)の累計、WAR の累計(野手は WAR、投手は失点版)。履歴と今シーズンの合計。"""
        games: dict[str, int] = {}
        war: dict[str, float] = {}

        def add(rec, lines):
            for group in (rec.batters, rec.pitchers):
                for pid, c in group.items():
                    games[pid] = games.get(pid, 0) + int(c.get("G", 0))
            for pid, line in lines.items():
                war[pid] = war.get(pid, 0.0) + float(line.war if line.role == "batter" else line.war_ra)

        for a in self.state.history:
            add(a.records, a.war)
        add(self.records.total, self.war_lines())
        return games, war

    def review_entries(self, team_id: str, year: int) -> list[dict]:
        """振り返りの元になる履歴の行(その年度にその球団に入った選手。経路つき)。"""
        rows = [x for x in self.state.transactions if x.get("player_id") and x["phase"] in ("draft", "market", "fill") and int(x["year"]) + 1 == int(year) and x["team_id"] == team_id]
        order = {"draft": 0, "market": 1, "fill": 2}
        return sorted(rows, key=lambda x: (order[x["phase"]], int(x.get("round") or 0), x["player_id"]))

    def draft_review(self, team_id: str | None = None, year: int | None = None) -> dict:
        """ドラフトの振り返り(公開用):入団の経路、入団時の総合の推定値とふれ幅・天井、今の所属、出場と WAR の累計(D-216)。
        真の能力は含めない(答え合わせ用は answers.draft_review_answers)。"""
        state = self.state
        names = self._team_names()
        years = self.review_years()
        teams = [{"team_id": t.id, "team_name": t.name, "is_mine": t.id == state.my_team_id} for t in state.league.teams]
        if team_id is None:
            team_id = state.my_team_id or state.league.teams[0].id
        if team_id not in names:
            raise ValueError(f"球団 '{team_id}' はありません")
        if year is None:
            year = years[-1] if years else None
        elif int(year) not in years:
            raise ValueError(f"{year} シーズン目に入団した選手の履歴はありません")
        out = {"team_id": team_id, "team_name": names[team_id], "year": year, "years": years, "teams": teams, "rows": [], "is_mine": team_id == state.my_team_id, "note": ""}
        if year is None:
            out["note"] = "まだドラフトを行っていません。年度を確定してオフの手続きを終えると、ここに入団した選手の一覧が出ます。"
            return out
        games, war = self._career_totals()
        players = {p.id: p for p in state.league.all_players()}
        rows = []
        for x in self.review_entries(team_id, int(year)):
            p = players.get(x["player_id"])
            sc = p.scouting if p is not None and p.scouting else None
            summary = dict(x)
            if sc and "overall" not in summary:  # 版 8 の履歴には入団時の要約がない。選手が残っていれば評価から引く
                summary.update(draftmod.entry_summary(sc))
            if p is None:
                status, status_label = "left", "引退・退団"
            elif p.team_id == team_id:
                status, status_label = "same", "在籍"
            else:
                status, status_label = "moved", names.get(p.team_id, "")
            rows.append(
                {
                    "player_id": x["player_id"],
                    "name": x["name"],
                    "role": x["role"],
                    "role_label": ROLE_LABELS.get(x["role"], ""),
                    "position": x["position"],
                    "position_label": POSITION_LABELS.get(x["position"], ""),
                    "route": x["phase"],
                    "route_label": f"ドラフト {x['round']} 巡" if x["phase"] == "draft" else ("市場" if x["phase"] == "market" else "自動補充"),
                    "entry_age": x.get("age"),
                    "age": None if p is None else p.age,
                    "entry_overall": summary.get("overall"),
                    "entry_margin": summary.get("margin"),
                    "entry_text": f"{summary['overall']} ± {float(summary['margin']):.0f}" if summary.get("overall") is not None else "-",
                    "entry_ceiling": summary.get("ceiling", "-"),
                    "method": summary.get("method"),
                    "status": status,
                    "status_label": status_label,
                    "games": games.get(x["player_id"], 0),
                    "war": f"{war.get(x['player_id'], 0.0):.1f}",
                    "in_league": p is not None,
                }
            )
        out["rows"] = rows
        out["note"] = f"{year}シーズン目に {names[team_id]} に入った選手({len(rows)}人)。入団時の評価は、そのときの {names[team_id]} のスカウトの推定値 ± ふれ幅と天井(S〜D)。出場と WAR は入団から今までの累計(WAR は野手が WAR、投手が失点版)。答え合わせモードをオンにすると、今の真の総合と、入団時の推定値との差、実際の天井が並びます。"
        return out

    def offseason_summary(self, year: int | None = None) -> dict:
        """オフの結果(公開用):引退した選手、入団した新人。能力の増減は答え合わせ用(answers.offseason_answers)。"""
        if not self.state.offseasons:
            return {"available": False, "years": []}
        if year is None:
            year = self.state.offseasons[-1].year
        r = next((o for o in self.state.offseasons if o.year == year), None)
        if r is None:
            raise ValueError(f"{year} シーズン目のオフの結果はありません")
        names = self._team_names()

        def note(n):
            return {"player_id": n.player_id, "name": n.name, "team_id": n.team_id, "team_name": names.get(n.team_id, n.team_id), "role": n.role, "role_label": ROLE_LABELS[n.role], "position": POSITION_LABELS[n.position], "age": n.age, "origin": ORIGIN_LABELS.get(n.origin, "") if n.origin else "", "is_mine": n.team_id == self.state.my_team_id}

        order = {k: i for i, k in enumerate(POSITION_LABELS)}
        retired = sorted((note(n) for n in r.retired), key=lambda d: (d["team_id"], order[next(k for k, v in POSITION_LABELS.items() if v == d["position"])], d["player_id"]))
        rookies = sorted((note(n) for n in r.rookies), key=lambda d: (d["team_id"], d["player_id"]))
        return {
            "available": True,
            "year": r.year,
            "next_year": r.year + 1,
            "years": [o.year for o in self.state.offseasons],
            "retired": retired,
            "rookies": rookies,
            "counts": {"retired": len(r.retired), "rookies": len(r.rookies), "players": len(self.state.league.all_players())},
            "note": f"{r.year}シーズン目の終わりに行ったオフの結果です。残った選手は年齢が1つ進み、能力が更新されました(能力の増減は、答え合わせモードがオンのときだけ見られます)。新人は、ドラフト・自由契約市場・自動補充で入った選手です。",
        }

    # ---- 始める・開く・保存する ----

    @classmethod
    def new(
        cls,
        seed: int,
        team_names: Sequence[str | None],
        my_team_index: int | None,
        season_seed: int | None = None,
        baselines: str = "trial",
        progress=None,
        prerun_progress=None,
        scout_level: str | None = None,
        money_rule: str | None = None,
    ) -> "Game":
        """新しいリーグを作る。seed はリーグ(選手の生成)の、season_seed はシーズン(日程と試合)のシード
        (省略時は seed と同じ)。球団名・自球団に問題があれば TeamNameError(理由つき)。

        baselines は初年度の指標の基準値の求め方:"trial"(見えない試運転のシーズンで求める。既定)か
        "default"(設定ファイルの既定値。速い)。progress には試運転の (終わった日数, 全日数) を知らせる(D-121、D-122)。
        """
        if baselines not in BASELINE_MODES:
            raise ValueError(f"基準値の求め方は trial か default です(値: {baselines!r})")
        gen, parts = load_generation_config(), load_name_parts()
        offseason_settings = load_offseason_settings()
        draft_settings = load_draft_settings()
        level = draft_settings.default_level if scout_level is None else str(scout_level)
        if level not in draft_settings.levels:
            raise ValueError(f"スカウト評価のずれの大きさは small / medium / large です(値: {scout_level!r})")
        contract_settings = load_contract_settings()
        negotiation_settings = load_negotiation_settings()
        rule = contract_settings.default_rule if money_rule is None else str(money_rule)
        if rule not in MONEY_RULES:
            raise ValueError(f"お金のルールは none / loose / standard / strict です(値: {money_rule!r})")
        tiers = assign_tiers([f"T{i:02d}" for i in range(1, 13)], seed, contract_settings) if rule == "strict" else {}
        contract_info: dict = {}
        league = new_league(seed, team_names, gen, parts, prerun=True, offseason_settings=offseason_settings, progress=prerun_progress, draft_settings=draft_settings, scout_sd=draft_settings.level_sd(level), calibration=offseason_settings.calibration(level), money_rule=rule, tiers=tiers, contract_settings=contract_settings, contract_info=contract_info, negotiation_settings=negotiation_settings)
        tiers = {tid: t for tid, t in tiers.items() if any(team.id == tid for team in league.teams)}
        if my_team_index is not None and (isinstance(my_team_index, bool) or not isinstance(my_team_index, int) or not 0 <= my_team_index < len(league.teams)):
            raise TeamNameError([f"自球団の選び方が正しくありません(値: {my_team_index!r})"])
        settings = load_baseline_settings()
        prior = trial_baselines(league, settings, progress) if baselines == "trial" else settings.default_baselines()
        season = Season(league, seed if season_seed is None else season_seed)
        famod.initialize_seasons(league, season.actives, league.seed, negotiation_settings)  # 初期選手の FA 権の年数(D-264)
        my_team_id = None if my_team_index is None else league.teams[my_team_index].id
        state = GameState(season, gen, parts, "", my_team_id, prior, settings, offseason_settings=offseason_settings, calibration=offseason_settings.calibration(level), scout_level=level, draft_settings=draft_settings, money_rule=rule, budget_tiers=tiers, contract_settings=contract_settings, negotiation_settings=negotiation_settings)
        state.contract_rates["1"] = float(contract_info.get("rate", 0.0))  # 新規開始時の単価(初期選手の契約の算定で求めた値)
        return cls(state, dirty=True)

    @classmethod
    def load(cls, data: bytes) -> "Game":
        """セーブデータを読み込む。壊れていれば SaveDataError(今のゲームには触れない)。"""
        return cls(load_game(bytes(data)), dirty=False)

    def save(self, today: date | str | None = None, saved_at: datetime | None = None) -> dict:
        """セーブデータの中身と、既定のファイル名(日付だけ。球団名は入れない)。保存すると「未保存」でなくなる。

        today は端末の今日の日付("2026-10-02" の形でもよい)。省略時は、この計算の場所の今日。"""
        if isinstance(today, str):
            today = date.fromisoformat(today)
        data = save_game(self.state, saved_at)
        self.dirty = False
        return {"data": data, "file_name": default_file_name(today or date.today()), "bytes": len(data)}

    # ---- 進める ----

    def advance(self, days: int = 1) -> dict:
        """days 日進める(シーズンの終わりで止まる)。戻り値は status と同じ。"""
        season = self.state.season
        n = max(0, min(int(days), season.total_days - season.day))
        if n:
            season.play_days(n)
            self.dirty = True
        return self.status()

    # ---- 見る ----

    def _team_names(self) -> dict[str, str]:
        return {t.id: t.name for t in self.state.league.teams}

    def status(self) -> dict:
        season = self.state.season
        my = self.state.my_team_id
        mine = None
        if my:
            team = next(t for t in self.state.league.teams if t.id == my)
            row = next(r for r in season.standings(team.league_index) if r.team_id == my)
            mine = {
                "id": my,
                "name": team.name,
                "league_name": self.state.league.league_names[team.league_index],
                "rank": row.rank,
                "wins": row.wins,
                "losses": row.losses,
                "ties": row.ties,
                "pct": _pct_text(row.pct, row.wins + row.losses),
                "games_behind": _gb_text(row.games_behind, row.rank == 1),
            }
        return {
            "day": season.day,
            "total_days": season.total_days,
            "games_played": len(season.played),
            "total_games": len(season.schedule),
            "is_over": season.is_over,
            "seed": season.seed,
            "my_team": mine,
            "dirty": self.dirty,
            "year": self.state.year,
            "can_year_end": season.is_over and self.state.procedure is None,
            "seasons": self.season_choices(),
            "offseason": None if self.state.procedure is None else {"active": True, "phase": self.state.procedure.phase, "phase_label": STAGE_LABELS[stage_of(self.state.procedure.phase)], "year": self.state.procedure.year},
            "scout_level": self.state.scout_level,
            "money_rule": self.state.money_rule,
            "money_rule_label": RULE_LABELS[self.state.money_rule],
        }

    def season_choices(self) -> list[dict]:
        """成績のページで選べるシーズン(今シーズン・過去の各シーズン・通算。D-182)。"""
        out = [{"key": "current", "label": f"今シーズン({self.state.year}シーズン目)"}]
        for a in reversed(self.state.history):
            out.append({"key": str(a.year), "label": f"{a.year}シーズン目"})
        if self.state.history:
            out.append({"key": "career", "label": "通算"})
        return out

    def standings(self) -> dict:
        """2リーグの順位表(実装④の順位の決め方そのまま)。自球団の行には is_mine が付く。"""
        season = self.state.season
        my = self.state.my_team_id
        leagues = []
        for i in season.league_indexes():
            rows = []
            for r in season.standings(i):
                rows.append(
                    {
                        "rank": r.rank,
                        "team_id": r.team_id,
                        "name": r.name,
                        "games": r.games,
                        "wins": r.wins,
                        "losses": r.losses,
                        "ties": r.ties,
                        "pct": _pct_text(r.pct, r.wins + r.losses),
                        "games_behind": _gb_text(r.games_behind, r.rank == 1),
                        "is_mine": r.team_id == my,
                    }
                )
            leagues.append({"index": i, "name": self.state.league.league_names[i], "rows": rows})
        return {"day": season.day, "leagues": leagues}

    def recent_games(self, team_id: str | None = None, count: int = 5) -> list[dict]:
        """チーム(省略時は自球団)の直近の試合(新しい順)。"""
        team_id = team_id or self.state.my_team_id
        names = self._team_names()
        out = []
        for p in reversed(self.state.season.played):
            r = p.result
            if team_id not in (r.home_team_id, r.away_team_id):
                continue
            home = r.home_team_id == team_id
            mine, theirs = (r.home_runs, r.away_runs) if home else (r.away_runs, r.home_runs)
            outcome = "分" if r.tie else ("勝" if mine > theirs else "負")
            out.append(
                {
                    "day": p.scheduled.day + 1,
                    "opponent": names[r.away_team_id if home else r.home_team_id],
                    "home": home,
                    "score": f"{mine}-{theirs}",
                    "outcome": outcome,
                    "innings": r.innings,
                    "walkoff": r.walkoff,
                }
            )
            if len(out) >= count:
                break
        return out

    def teams(self) -> list[dict]:
        return [
            {"id": t.id, "name": t.name, "league_index": t.league_index, "league_name": self.state.league.league_names[t.league_index], "stadium": t.stadium, "is_mine": t.id == self.state.my_team_id}
            for t in self.state.league.teams
        ]

    def players(self, team_id: str) -> list[dict]:
        """チームの選手(公開用の情報だけ。能力値・隠し情報は含めない。D-108)。"""
        team = next(t for t in self.state.league.teams if t.id == team_id)
        return [public_player(p, team.name) for p in team.players]


    # ---- 成績の画面(②。D-114) ----

    def _team(self, team_id: str):
        for t in self.state.league.teams:
            if t.id == team_id:
                return t
        raise KeyError(f"チーム '{team_id}' はありません")

    # ---- シーズンの選択(今シーズン・過去シーズン・通算。F2。D-182) ----

    def _season_view(self, season: str | int | None) -> "_SeasonView":
        """成績に使う元の数・基準値・球場補正・WAR のまとまり。season は None/"current"(今)、シーズン番号、"career"(通算)。"""
        state = self.state
        key = "current" if season is None else str(season)
        if key == "current" or key == str(state.year):
            return _SeasonView("current", f"今シーズン({state.year}シーズン目)", self.records.total, self.baselines()[0], self.player_park_factor, self.war_lines(), self._current_player_info, self.baseline_info()["text"], self.war_note(), state.season.day)
        if key == "career":
            if not state.history:
                raise ValueError("通算は、2シーズン目から選べます")
            rec = Records()
            war: dict[str, WarLine] = {}
            pf_sum: dict[str, Fraction] = {}
            pa_sum: dict[str, int] = {}
            info: dict[str, dict] = {}
            for a in state.history:
                rec.add(a.records)
                for pid, c in a.records.batters.items():
                    pf_sum[pid] = pf_sum.get(pid, Fraction(0)) + a.park_factors.get(pid, Fraction(1)) * c["PA"]
                    pa_sum[pid] = pa_sum.get(pid, 0) + c["PA"]
                for pid, v in a.war.items():
                    war[pid] = _add_war(war.get(pid), v)
                info.update(a.players)
            cur = self.records.total
            rec.add(cur)
            for pid, c in cur.batters.items():
                pf_sum[pid] = pf_sum.get(pid, Fraction(0)) + self.player_park_factor(pid) * c["PA"]
                pa_sum[pid] = pa_sum.get(pid, 0) + c["PA"]
            for pid, v in self.war_lines().items():
                war[pid] = _add_war(war.get(pid), v)
            for pid in set(cur.batters) | set(cur.pitchers):
                info[pid] = self._current_player_info(pid)

            def pf(pid):
                return pf_sum[pid] / pa_sum[pid] if pa_sum.get(pid) else Fraction(1)

            n = len(state.history) + 1
            return _SeasonView("career", f"通算(1〜{state.year}シーズン目)", rec, self.baselines()[0], pf, war, lambda pid: info[pid], f"{n}シーズン分の元の数を足し合わせています。基準値に依存する指標(wOBA・wRC+・OPS+・FIP)は、各シーズンの値(そのシーズンの基準値と球場補正)を打席数(投手は投球回)で加重平均した値です。", f"{n}シーズン分の WAR の合計です(各シーズンの値を足しています)。", None, self._career_overrides())
        a = next((a for a in state.history if str(a.year) == key), None)
        if a is None:
            raise ValueError(f"シーズン {season!r} の成績はありません(選べるのは、今シーズン・過去のシーズン番号・career)")
        return _SeasonView(key, f"{a.year}シーズン目", a.records, a.baselines, lambda pid: a.park_factors.get(pid, Fraction(1)), a.war, lambda pid: a.players[pid], f"{a.year}シーズン目の最終の基準値(出発点の値に、そのシーズンの値を混ぜたもの)で計算した、確定した値です。球場補正は{'、1シーズン目のため 1.0(補正なし)' if a.year == 1 else f'、前のシーズンまで({a.year - 1}シーズン分)の結果から推定した値'}。", f"{a.year}シーズン目の確定した WAR です。", a.day)

    def _career_overrides(self) -> dict[str, dict]:
        """通算の、基準値に依存する指標の加重平均(D-192):選手 ID → 指標 → 値(値なしのシーズンは除く。全シーズン値なしなら None)。"""
        config = metrics_config()
        dependent = {role: baseline_dependent(config, role) for role in ROLE_LABELS}
        sums: dict[str, dict[str, list]] = {}  # pid → metric → [重みつき合計, 重みの合計]
        seasons = [(a.records, a.baselines, lambda pid, a=a: a.park_factors.get(pid, Fraction(1))) for a in self.state.history]
        seasons.append((self.records.total, self.baselines()[0], self.player_park_factor))
        for rec, base, pf in seasons:
            for role, group in (("batter", rec.batters), ("pitcher", rec.pitchers)):
                for pid, counts in group.items():
                    weight = counts["PA"] if role == "batter" else counts["OUTS"]
                    values = dict(base.values)
                    if role == "batter":
                        values["pf"] = pf(pid)
                    metrics = compute(config, role, counts, values)
                    acc = sums.setdefault(pid, {})
                    for key in dependent[role]:
                        v = metrics.get(key)
                        if v is None or not weight:
                            acc.setdefault(key, [Fraction(0), 0])
                            continue
                        s = acc.setdefault(key, [Fraction(0), 0])
                        s[0] += v * weight
                        s[1] += weight
        return {pid: {key: (s[0] / s[1] if s[1] else None) for key, s in acc.items()} for pid, acc in sums.items()}

    def _current_player_info(self, pid: str) -> dict:
        p = self.state.season.players.get(pid)
        if p is None:  # オフの手続きの途中で読み込んだ状態では、引退した選手がシーズンの一覧にいない。終わったシーズンの写し(履歴)から引く
            for a in reversed(self.state.history):
                if pid in a.players:
                    return dict(a.players[pid])
            raise KeyError(pid)
        return {"name": p.name, "team_id": p.team_id, "role": p.role, "position": p.position, "age": p.age}

    def select_players(self, role: str, qualified: bool = True, league: int | None = None, team_id: str | None = None, season: str | int | None = None) -> list[str]:
        """個人成績に出す選手(試合に出た選手。規定到達者・リーグ・チームで絞り込む)。"""
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        rec = self._season_view(season).records
        group, owner = (rec.batters, rec.batter_team) if role == "batter" else (rec.pitchers, rec.pitcher_team)
        ids = list(group)
        if qualified:
            ok = set(qualified_batters(rec) if role == "batter" else qualified_pitchers(rec))
            ids = [pid for pid in ids if pid in ok]
        if league is not None:
            ids = [pid for pid in ids if self._team(owner[pid]).league_index == league]
        if team_id:
            ids = [pid for pid in ids if owner[pid] == team_id]
        return ids

    def _player_row(self, pid: str, team_id: str, view: "_SeasonView | None" = None) -> dict:
        p = view.info(pid) if view else self._current_player_info(pid)
        team = self._team(team_id)
        return {"player_id": pid, "name": p["name"], "team_id": team_id, "team_name": team.name, "position": POSITION_LABELS[p["position"]], "is_mine": team_id == self.state.my_team_id}

    def stats(
        self,
        role: str = "batter",
        kind: str = "basic",
        sort: str | None = None,
        order: str | None = None,
        qualified: bool = True,
        league: int | None = None,
        team_id: str | None = None,
        season: str | int | None = None,
    ) -> dict:
        """個人成績の表(D-116)。列・既定の並び順は指標の定義データの tables から(D-119)。
        season は None(今シーズン)、過去のシーズン番号、"career"(通算。F2。D-182)。

        sort は列(元の数か指標)の名前。表の列に限らず、その役割の元の数・全指標を使える(D-132)。
        表にない指標で並べたときは、extra_column にその列を返し、各行の values にも値を入れる(画面は名前の隣に出す)。
        order は "desc"(大きい順)か "asc"(小さい順)。省略時は、その指標の「よい」向き(打率なら高い順、防御率なら低い順)。
        値なし(分母が 0)の選手は、向きによらず最後に並べる。
        """
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        view = self._season_view(season)
        if kind == WAR_KIND:
            return self._war_stats(role, sort, order, qualified, league, team_id, view)
        if kind not in KIND_LABELS:
            raise ValueError(f"基本かセイバーを選んでください(値: {kind!r})")
        config = metrics_config()
        table = config["tables"][role][kind]
        sort = sort or table["sort"]
        if not is_sortable(config, role, sort):
            raise ValueError(f"並び順に使えない列です(値: {sort!r})")
        info = column_info(config, role, sort)
        extra = None if sort in table["columns"] else info
        shown_keys = list(table["columns"]) + ([sort] if extra else [])
        if order not in ("asc", "desc"):
            order = "desc" if info["better"] == "high" else "asc"
        rec = view.records
        group, owner = (rec.batters, rec.batter_team) if role == "batter" else (rec.pitchers, rec.pitcher_team)
        rows = []
        base = view.baselines
        for pid in self.select_players(role, qualified, league, team_id, view.key):
            values = _values(config, role, group[pid], base, view.park_factor(pid) if role == "batter" else None, (view.override or {}).get(pid))
            row = self._player_row(pid, owner[pid], view)
            row["values"] = {k: values[k][1] for k in shown_keys}
            row["_sort"] = values[sort][0]
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
        return {
            "role": role,
            "role_label": ROLE_LABELS[role],
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "columns": [column_info(config, role, k) for k in table["columns"]],
            "sort": info,
            "order": order,
            "extra_column": extra,
            "qualified": qualified,
            "qualify_rule": QUALIFY_RULES[role],
            "rows": rows,
            "day": self.state.season.day if view.key == "current" else view.day,
            "baseline_note": view.baseline_note if any(c["key"] in _BASELINE_METRICS for c in table_cols(config, role, kind)) else None,
            "season": view.key,
            "season_label": view.label,
        }

    def _war_stats(self, role: str, sort: str | None, order: str | None, qualified: bool, league: int | None, team_id: str | None, view: "_SeasonView") -> dict:
        """個人成績の「WAR」の表(③b。D-179)。列は WAR_COLUMNS。並び順は WAR の列だけ(既定は WAR の高い順)。"""
        columns = WAR_COLUMNS[role]
        default = "war" if role == "batter" else "war_ra"
        sort = sort if sort in _WAR_SORT_KEYS[role] else default
        info = next(c for c in columns if c["key"] == sort)
        if order not in ("asc", "desc"):
            order = "desc"
        lines = view.war
        rec = view.records
        owner = rec.batter_team if role == "batter" else rec.pitcher_team
        rows = []
        for pid in self.select_players(role, qualified, league, team_id, view.key):
            line = lines.get(pid)
            if line is None:
                continue
            values = _war_values(line)
            row = self._player_row(pid, owner[pid], view)
            row["values"] = {k: values[k][1] for k in values}
            row["_sort"] = values[sort][0]
            rows.append(row)
        rows.sort(key=lambda r: r["player_id"])
        rows.sort(key=lambda r: r["_sort"], reverse=order == "desc")
        for i, r in enumerate(rows):
            r.pop("_sort")
            r["rank"] = i + 1
        return {
            "role": role,
            "role_label": ROLE_LABELS[role],
            "kind": WAR_KIND,
            "kind_label": "WAR",
            "columns": columns,
            "sort": info,
            "order": order,
            "extra_column": None,
            "qualified": qualified,
            "qualify_rule": QUALIFY_RULES[role],
            "rows": rows,
            "day": self.state.season.day if view.key == "current" else view.day,
            "baseline_note": view.war_note,
            "terms": WAR_TERMS,
            "season": view.key,
            "season_label": view.label,
        }

    def _game_summary(self, n: int) -> dict:
        played = self.state.season.played[n]
        r = played.result
        names = self._team_names()
        tags = [t for t, on in (("延長", r.extra_innings), ("サヨナラ", r.walkoff), ("引き分け", r.tie)) if on]
        return {
            "game_no": n,
            "day": played.scheduled.day + 1,
            "league_index": played.scheduled.league_index,
            "away": {"team_id": r.away_team_id, "name": names[r.away_team_id], "runs": r.away_runs},
            "home": {"team_id": r.home_team_id, "name": names[r.home_team_id], "runs": r.home_runs},
            "stadium": self._team(r.home_team_id).stadium,
            "winner": r.winner,
            "innings": r.innings,
            "tags": tags,
            "is_mine": self.state.my_team_id in (r.home_team_id, r.away_team_id),
        }

    def games_on(self, day: int) -> dict:
        """その日(1から)の試合の一覧。まだ行っていない日は、空の一覧。"""
        games = [self._game_summary(i) for i, p in enumerate(self.state.season.played) if p.scheduled.day + 1 == day]
        return {"day": day, "last_day": self.state.season.day, "total_days": self.state.season.total_days, "games": games}

    def last_day_games(self) -> dict:
        """直近の日(最後に進めた日)の全試合(D-117)。"""
        return self.games_on(self.state.season.day)

    def game(self, game_no: int) -> dict:
        """1試合の文章ログと投手の成績(勝敗・セーブ・ホールドの印つき)。"""
        season = self.state.season
        if not 0 <= int(game_no) < len(season.played):
            raise ValueError(f"試合 {game_no} は、まだ行っていません")
        n = int(game_no)
        story = game_story(season.played[n].result, season.players, self._team_names(), self.records.decisions[n])
        return {"summary": self._game_summary(n), "story": story}

    def _unattached_label(self, player_id: str) -> str:
        """オフの間、球団を離れている選手の所属の表示(FA 宣言中か、自由契約)。"""
        proc = self.state.procedure
        if proc is not None and player_id in proc.fa_info and proc.fa_info[player_id]["status"] == "open":
            return "FA 宣言中"
        return "自由契約"

    def player(self, player_id: str) -> dict:
        """選手のページ:基本情報(公開用)、シーズン通算の成績(基本・セイバー)、試合ごとの成績(新しい順)。"""
        season = self.state.season
        if player_id in season.players:
            p = season.players[player_id]
            team = self._team(p.team_id) if p.team_id is not None else None
            info = public_player(p, team.name if team is not None else self._unattached_label(p.id))
            info.update(
                position_label=POSITION_LABELS[p.position],
                role_label=ROLE_LABELS[p.role],
                hand=BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
                is_mine=team is not None and team.id == self.state.my_team_id,
                retired=False,
                scouting=None if p.scouting is None else {**ScoutReport.from_dict(p.scouting).to_public(), "year": p.scouting.get("year"), "team_name": self._team(p.scouting["team_id"]).name},
                contract=self._contract_public(p),
            )
            role = p.role
        else:  # 引退した選手(過去シーズンの写しから。F2)
            past = next((a.players[player_id] for a in reversed(self.state.history) if player_id in a.players), None)
            if past is None:
                raise ValueError(f"選手 '{player_id}' はいません")
            team = self._team(past["team_id"])
            role = past["role"]
            info = {"id": player_id, "name": past["name"], "age": past["age"], "role": role, "position": past["position"], "team_name": team.name, "position_label": POSITION_LABELS[past["position"]], "role_label": ROLE_LABELS[role], "hand": None, "is_mine": team.id == self.state.my_team_id, "retired": True}
        config = metrics_config()
        cache = self.records
        total = (cache.total.batters if role == "batter" else cache.total.pitchers).get(player_id)
        season_block = None
        if total is not None:
            pf = self.player_park_factor(player_id) if role == "batter" else None
            values = _values(config, role, total, self.baselines()[0], pf)
            qualified = player_id in (qualified_batters(cache.total) if role == "batter" else qualified_pitchers(cache.total))
            line = self.war_lines().get(player_id)
            season_block = {
                "baseline_note": self.baseline_info()["text"],
                "park_factor": None if pf is None else f"{float(pf):.3f}",
                "war": None if line is None else {"columns": WAR_COLUMNS[role], "values": {k: v[1] for k, v in _war_values(line).items()}, "note": self.war_note(), "terms": WAR_TERMS},
                "qualified": qualified,
                "qualify_rule": QUALIFY_RULES[role],
                "tables": {
                    kind: {"columns": [column_info(config, role, k) for k in config["tables"][role][kind]["columns"]], "values": {k: values[k][1] for k in config["tables"][role][kind]["columns"]}}
                    for kind in KIND_LABELS
                },
            }
        game_cols = config["tables"][role]["game"]["columns"]
        games = []
        names = self._team_names()
        game_team_id = team.id if team is not None else next((a.players[player_id]["team_id"] for a in reversed(self.state.history) if player_id in a.players), None)
        for n in range(len(cache.per_game) - 1, -1, -1):
            group = cache.per_game[n].batters if role == "batter" else cache.per_game[n].pitchers
            if player_id not in group:
                continue
            values = _values(config, role, group[player_id])  # 試合ごとは元の数だけを出す
            s = self._game_summary(n)
            mine_home = s["home"]["team_id"] == game_team_id
            us, them = (s["home"], s["away"]) if mine_home else (s["away"], s["home"])
            d = cache.decisions[n]
            mark = "勝" if d.win == player_id else "敗" if d.loss == player_id else "S" if d.save == player_id else "H" if player_id in d.holds else ""
            games.append(
                {
                    "game_no": n,
                    "day": s["day"],
                    "opponent": names[them["team_id"]],
                    "home": mine_home,
                    "score": f"{us['runs']}-{them['runs']}",
                    "outcome": "分" if s["winner"] is None else ("勝" if s["winner"] == game_team_id else "負"),
                    "decision": mark,
                    "values": {k: values[k][1] for k in game_cols},
                }
            )
        return {
            "player": info,
            "season": season_block,
            "history": self._player_history(player_id, role, config),
            "game_columns": [column_info(config, role, k) for k in game_cols],
            "games": games,
        }

    def _player_history(self, player_id: str, role: str, config) -> dict | None:
        """選手のページの「年度別」の表(過去シーズン・今シーズン・通算。F2。D-182)。2シーズン目から。"""
        if not self.state.history:
            return None
        keys = [str(a.year) for a in self.state.history] + ["current", "career"]
        war_cols = WAR_COLUMNS[role]
        rows = []
        for key in keys:
            view = self._season_view(key)
            group = view.records.batters if role == "batter" else view.records.pitchers
            if player_id not in group:
                continue
            counts = group[player_id]
            info = view.info(player_id)
            values = _values(config, role, counts, view.baselines, view.park_factor(player_id) if role == "batter" else None, (view.override or {}).get(player_id))
            line = view.war.get(player_id)
            rows.append(
                {
                    "season": view.key,
                    "label": view.label if key != "current" else f"{self.state.year}シーズン目(進行中)",
                    "year": self.state.year if key == "current" else (None if key == "career" else int(key)),
                    "age": None if key == "career" else info["age"],
                    "team_name": "" if key == "career" else self._team(view.records.batter_team[player_id] if role == "batter" else view.records.pitcher_team[player_id]).name,
                    "position": "" if key == "career" else POSITION_LABELS[info["position"]],
                    "tables": {kind: {k: values[k][1] for k in config["tables"][role][kind]["columns"]} for kind in KIND_LABELS},
                    "war": None if line is None else {k: v[1] for k, v in _war_values(line).items()},
                }
            )
        return {
            "columns": {kind: [column_info(config, role, k) for k in config["tables"][role][kind]["columns"]] for kind in KIND_LABELS},
            "war_columns": war_cols,
            "rows": rows,
            "note": "過去のシーズンは確定した値、今シーズンは進行中の値、通算は元の数の合計から今シーズンの基準値で計算した値です。WAR の通算は各シーズンの合計です。",
        }

    def stadium(self, team_id: str) -> dict:
        """球場のページ(公開用。D-138):球場名、本拠地のチーム、本拠地での実際の結果(両チームの合計)。倍率の値は含めない。"""
        team = self._team(team_id)
        games = home_runs = runs = plate_appearances = 0
        for p in self.state.season.played:
            r = p.result
            if r.home_team_id != team_id:
                continue
            games += 1
            runs += r.home_runs + r.away_runs
            plate_appearances += len(r.log)
            home_runs += sum(1 for x in r.log if x.pa.result == "home_run")
        tally = self.records.park_tallies.get(team_id, ParkTally())

        def rate(c, key):
            v = raw_rate(c, key)
            return "-" if v is None else (f"{100 * float(v):.2f}%" if key == "home_run" else f"{float(v):.3f}")

        this_season = {}
        for key in FACTOR_KEYS:
            ratio = raw_ratio(tally, key)
            this_season[key] = {"label": FACTOR_LABELS[key], "home": rate(tally.home, key), "away": rate(tally.away, key), "ratio": "-" if ratio is None else f"{float(ratio):.3f}"}
        est = self.park_estimates()
        estimate = None
        if est is not None and team_id in est:
            e = est[team_id]
            estimate = {"seasons": e.seasons, **{k: f"{float(e.estimate[k]):.3f}" for k in FACTOR_KEYS}}
        return {
            "team_id": team_id,
            "name": team.stadium,
            "team_name": team.name,
            "league_name": self.state.league.league_names[team.league_index],
            "is_mine": team_id == self.state.my_team_id,
            "season_number": self.season_number(),
            "this_season": this_season,
            "estimate": estimate,
            "estimate_note": (
                "まだ推定できません。1シーズンだけでは、運のぶれが大きいためです。2シーズン目から、前のシーズンまでの結果で推定します。"
                if estimate is None
                else f"前のシーズンまで({estimate['seasons']}シーズン分)の本拠地とアウェイの比から推定し、1.0 に向けて縮めた値(各リーグの平均が 1.0)。「得点」は本塁打と BABIP の推定から組み立てた「1打席あたりの得点の出やすさ」で、wRC+・OPS+ の球場補正に使われます。BABIP 単独の推定は参考値です(運のぶれに埋もれやすい)。"
            ),
            "games": games,
            "home_runs": home_runs,
            "runs": runs,
            "plate_appearances": plate_appearances,
            "home_runs_per_game": f"{home_runs / games:.2f}" if games else "-",
            "runs_per_game": f"{runs / games:.2f}" if games else "-",
            "note": "本拠地で行った試合の、両チームを合わせた数です。球場の打ちやすさは、結果から推し量ってください(真の倍率は、答え合わせモードで見られます)。",
        }

    def team(self, team_id: str) -> dict:
        """チームのページ:チームの成績(試合・勝敗・得点・失点)と、選手の一覧(公開用の情報と出場数)。"""
        team = self._team(team_id)
        row = next(r for r in self.state.season.standings(team.league_index) if r.team_id == team_id)
        rec = self.records.total
        t = rec.teams.get(team_id, {})
        order = {k: i for i, k in enumerate(POSITION_LABELS)}
        players = []
        for p in sorted(team.players, key=lambda p: (order[p.position], p.id)):
            counts = (rec.batters if p.role == "batter" else rec.pitchers).get(p.id, {})
            players.append(
                {
                    "player_id": p.id,
                    "name": p.name,
                    "age": p.age,
                    "role": p.role,
                    "position": POSITION_LABELS[p.position],
                    "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
                    "games": counts.get("G", 0),
                }
            )
        lines = {pid: v for pid, v in self.war_lines().items() if v.team_id == team_id}
        totals = war_totals(lines)
        salaries = sorted(({"player_id": p.id, "name": p.name, "position": POSITION_LABELS[p.position], "age": p.age, "salary": int(p.contract["salary"]), "salary_text": f"{int(p.contract['salary']):,}", "remaining": remaining_years(p.contract, self.state.year)} for p in team.players if p.contract), key=lambda r: (-r["salary"], r["player_id"]))
        return {
            "team_id": team_id,
            "name": team.name,
            "budget": self.budget_info(team_id),
            "salaries": salaries,
            "stadium": team.stadium,
            "league_name": self.state.league.league_names[team.league_index],
            "is_mine": team_id == self.state.my_team_id,
            "rank": row.rank,
            "war": {
                "batters": _war_text(totals["batters"]),
                "pitchers_ra": _war_text(totals["pitchers_ra"]),
                "pitchers_fip": _war_text(totals["pitchers_fip"]),
                "total_ra": _war_text(totals["batters"] + totals["pitchers_ra"]),
                "note": self.war_note(),
                "terms": WAR_TERMS,
            },
            "record": {
                "games": t.get("G", 0),
                "wins": row.wins,
                "losses": row.losses,
                "ties": row.ties,
                "pct": _pct_text(row.pct, row.wins + row.losses),
                "games_behind": _gb_text(row.games_behind, row.rank == 1),
                "runs": t.get("R", 0),
                "runs_allowed": t.get("RA", 0),
            },
            "players": players,
        }


__all__ = ["Game", "SaveDataError", "TeamNameError", "check_team_names", "preview_teams"]
