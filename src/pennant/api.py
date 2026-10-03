"""画面から呼ぶ「操作の関数」(最小のブラウザ画面。D-080、D-107、D-108)。

計算本体の側にあり、画面からは独立している。戻り値は、画面がそのまま使える JSON にできる形
(辞書・リスト・文字列・数・真偽値・None)。bytes はセーブデータの中身だけ。

公開用の関数だけを置く:選手の能力値・能力の見積もり・隠し情報は返さない(D-108)。
答え合わせ用の関数は、別のモジュール(answers.py)に置く(D-114)。

成績の画面(②。D-114):個人成績・選手・試合・チームの詳細。集計(試合ごとの元の数)は、
進めた試合の分だけ足していき、日が進むまで使い回す(_StatsCache)。
"""

from __future__ import annotations

import math
from collections import Counter
from fractions import Fraction
from datetime import date, datetime
from typing import Sequence

from .abilities import POSITION_LABELS
from .baselines import Baselines, RunTally, blend, compute_baselines, load_baseline_settings, tally_game, trial_baselines
from .config import load_generation_config, load_name_parts
from .decisions import Decisions, decide
from .game_stats import game_story
from .metrics import MetricsConfig, compute, format_value, formula_text, innings_text, load_metrics_config
from .parkfactors import FACTOR_KEYS, FACTOR_LABELS, ParkEstimate, ParkTally, add_game, estimate_parks, load_park_settings, player_park_factor, raw_ratio, season_tallies
from .war import WarLine, load_war_settings, war_for_results, war_totals
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


def _values(config: MetricsConfig, role: str, counts, baselines: Baselines | None = None, park_factor: Fraction | None = None) -> dict:
    """元の数と指標の値(並べ替え用の数と、表示用の文字)。park_factor は選手ごとの球場補正(式の pf。D-142)。"""
    values = dict(baselines.values) if baselines else None
    if values is not None and park_factor is not None:
        values["pf"] = park_factor
    metrics = compute(config, role, counts, values)
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


class Game:
    """遊んでいる1つのゲーム(画面は、これを1つ持って操作する)。"""

    def __init__(self, state: GameState, dirty: bool):
        self.state = state
        self.dirty = dirty  # 未保存の変更があるか
        self._cache = _StatsCache()
        self._park_estimates: tuple[int, dict[str, ParkEstimate] | None] | None = None
        self._war: tuple[int, dict[str, WarLine]] | None = None  # (試合数, WAR の表)。試合数が変わるまで覚えておく(D-179)

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
        """シーズンを終えて、球場 × シーズンの集計を履歴に足す(F2 の「年度の確定」の最小の形。D-146)。戻り値は履歴の数。"""
        if not self.state.season.is_over:
            raise ValueError("シーズンがまだ終わっていません")
        self.state.park_history.append(season_tallies(p.result for p in self.state.season.played))
        self._park_estimates = None
        self.dirty = True
        return len(self.state.park_history)

    # ---- 始める・開く・保存する ----

    @classmethod
    def new(
        cls,
        seed: int,
        team_names: Sequence[str | None],
        my_team_index: int,
        season_seed: int | None = None,
        baselines: str = "trial",
        progress=None,
    ) -> "Game":
        """新しいリーグを作る。seed はリーグ(選手の生成)の、season_seed はシーズン(日程と試合)のシード
        (省略時は seed と同じ)。球団名・自球団に問題があれば TeamNameError(理由つき)。

        baselines は初年度の指標の基準値の求め方:"trial"(見えない試運転のシーズンで求める。既定)か
        "default"(設定ファイルの既定値。速い)。progress には試運転の (終わった日数, 全日数) を知らせる(D-121、D-122)。
        """
        if baselines not in BASELINE_MODES:
            raise ValueError(f"基準値の求め方は trial か default です(値: {baselines!r})")
        gen, parts = load_generation_config(), load_name_parts()
        league = new_league(seed, team_names, gen, parts)
        if isinstance(my_team_index, bool) or not isinstance(my_team_index, int) or not 0 <= my_team_index < len(league.teams):
            raise TeamNameError([f"自球団の選び方が正しくありません(値: {my_team_index!r})"])
        settings = load_baseline_settings()
        prior = trial_baselines(league, settings, progress) if baselines == "trial" else settings.default_baselines()
        season = Season(league, seed if season_seed is None else season_seed)
        state = GameState(season, gen, parts, "", league.teams[my_team_index].id, prior, settings)
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
        }

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

    def select_players(self, role: str, qualified: bool = True, league: int | None = None, team_id: str | None = None) -> list[str]:
        """個人成績に出す選手(試合に出た選手。規定到達者・リーグ・チームで絞り込む)。"""
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        rec = self.records.total
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

    def _player_row(self, pid: str, team_id: str) -> dict:
        p = self.state.season.players[pid]
        team = self._team(team_id)
        return {"player_id": pid, "name": p.name, "team_id": team_id, "team_name": team.name, "position": POSITION_LABELS[p.position], "is_mine": team_id == self.state.my_team_id}

    def stats(
        self,
        role: str = "batter",
        kind: str = "basic",
        sort: str | None = None,
        order: str | None = None,
        qualified: bool = True,
        league: int | None = None,
        team_id: str | None = None,
    ) -> dict:
        """個人成績の表(D-116)。列・既定の並び順は指標の定義データの tables から(D-119)。

        sort は列(元の数か指標)の名前。表の列に限らず、その役割の元の数・全指標を使える(D-132)。
        表にない指標で並べたときは、extra_column にその列を返し、各行の values にも値を入れる(画面は名前の隣に出す)。
        order は "desc"(大きい順)か "asc"(小さい順)。省略時は、その指標の「よい」向き(打率なら高い順、防御率なら低い順)。
        値なし(分母が 0)の選手は、向きによらず最後に並べる。
        """
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        if kind == WAR_KIND:
            return self._war_stats(role, sort, order, qualified, league, team_id)
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
        rec = self.records.total
        group, owner = (rec.batters, rec.batter_team) if role == "batter" else (rec.pitchers, rec.pitcher_team)
        rows = []
        base, _ = self.baselines()
        for pid in self.select_players(role, qualified, league, team_id):
            values = _values(config, role, group[pid], base, self.player_park_factor(pid) if role == "batter" else None)
            row = self._player_row(pid, owner[pid])
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
            "day": self.state.season.day,
            "baseline_note": self.baseline_info()["text"] if any(c["key"] in _BASELINE_METRICS for c in table_cols(config, role, kind)) else None,
        }

    def _war_stats(self, role: str, sort: str | None, order: str | None, qualified: bool, league: int | None, team_id: str | None) -> dict:
        """個人成績の「WAR」の表(③b。D-179)。列は WAR_COLUMNS。並び順は WAR の列だけ(既定は WAR の高い順)。"""
        columns = WAR_COLUMNS[role]
        default = "war" if role == "batter" else "war_ra"
        sort = sort if sort in _WAR_SORT_KEYS[role] else default
        info = next(c for c in columns if c["key"] == sort)
        if order not in ("asc", "desc"):
            order = "desc"
        lines = self.war_lines()
        rec = self.records.total
        owner = rec.batter_team if role == "batter" else rec.pitcher_team
        rows = []
        for pid in self.select_players(role, qualified, league, team_id):
            line = lines.get(pid)
            if line is None:
                continue
            values = _war_values(line)
            row = self._player_row(pid, owner[pid])
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
            "day": self.state.season.day,
            "baseline_note": self.war_note(),
            "terms": WAR_TERMS,
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

    def player(self, player_id: str) -> dict:
        """選手のページ:基本情報(公開用)、シーズン通算の成績(基本・セイバー)、試合ごとの成績(新しい順)。"""
        season = self.state.season
        if player_id not in season.players:
            raise ValueError(f"選手 '{player_id}' はいません")
        p = season.players[player_id]
        team = self._team(p.team_id)
        info = public_player(p, team.name)
        info.update(
            position_label=POSITION_LABELS[p.position],
            role_label=ROLE_LABELS[p.role],
            hand=BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
            is_mine=team.id == self.state.my_team_id,
        )
        config = metrics_config()
        role = p.role
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
        for n in range(len(cache.per_game) - 1, -1, -1):
            group = cache.per_game[n].batters if role == "batter" else cache.per_game[n].pitchers
            if player_id not in group:
                continue
            values = _values(config, role, group[player_id])  # 試合ごとは元の数だけを出す
            s = self._game_summary(n)
            mine_home = s["home"]["team_id"] == team.id
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
                    "outcome": "分" if s["winner"] is None else ("勝" if s["winner"] == team.id else "負"),
                    "decision": mark,
                    "values": {k: values[k][1] for k in game_cols},
                }
            )
        return {
            "player": info,
            "season": season_block,
            "game_columns": [column_info(config, role, k) for k in game_cols],
            "games": games,
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
        return {
            "team_id": team_id,
            "name": team.name,
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
