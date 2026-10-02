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


def _values(config: MetricsConfig, role: str, counts, baselines: Baselines | None = None) -> dict:
    """元の数と指標の値(並べ替え用の数と、表示用の文字)。"""
    metrics = compute(config, role, counts, baselines.values if baselines else None)
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
        self._baselines: tuple[int, Baselines, object] | None = None

    def update(self, season: Season) -> "_StatsCache":
        for played in season.played[len(self.per_game) :]:
            d = decide(played.result)
            rec = game_records(played.result, d)
            self.per_game.append(rec)
            self.decisions.append(d)
            self.total.add(rec)
            self.tally.add(tally_game(played.result))
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
        """画面に出す、基準値の混ぜ方の説明(D-126)。"""
        _, w = self.baselines()
        source = SOURCE_LABELS.get(self.state.baselines.source, "出発点の値")
        pct = math.floor(w * 100 + Fraction(1, 2))
        return {
            "source": self.state.baselines.source,
            "source_label": source,
            "weight_percent": pct,
            "plate_appearances": _sum(self.records.total.batters)["PA"],
            "text": f"wOBA などの基準値は、{source}に、今シーズンの値を混ぜて使っています(今シーズンの比重 {pct}%。打席が増えるほど上がり、シーズンの最後で約75%)。",
        }

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
            values = _values(config, role, group[pid], base)
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
            values = _values(config, role, total, self.baselines()[0])
            qualified = player_id in (qualified_batters(cache.total) if role == "batter" else qualified_pitchers(cache.total))
            season_block = {
                "baseline_note": self.baseline_info()["text"],
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
        return {
            "team_id": team_id,
            "name": team.name,
            "stadium": team.stadium,
            "league_name": self.state.league.league_names[team.league_index],
            "is_mine": team_id == self.state.my_team_id,
            "rank": row.rank,
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
