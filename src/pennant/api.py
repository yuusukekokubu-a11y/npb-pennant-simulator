"""画面から呼ぶ「操作の関数」(最小のブラウザ画面。D-080、D-107、D-108)。

計算本体の側にあり、画面からは独立している。戻り値は、画面がそのまま使える JSON にできる形
(辞書・リスト・文字列・数・真偽値・None)。bytes はセーブデータの中身だけ。

公開用の関数だけを置く:選手の能力値・能力の見積もり・隠し情報は返さない(D-108)。
答え合わせ用の関数は、成績の画面(②)で別に作る。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

from .config import load_generation_config, load_name_parts
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


class Game:
    """遊んでいる1つのゲーム(画面は、これを1つ持って操作する)。"""

    def __init__(self, state: GameState, dirty: bool):
        self.state = state
        self.dirty = dirty  # 未保存の変更があるか

    # ---- 始める・開く・保存する ----

    @classmethod
    def new(
        cls, seed: int, team_names: Sequence[str | None], my_team_index: int, season_seed: int | None = None
    ) -> "Game":
        """新しいリーグを作る。seed はリーグ(選手の生成)の、season_seed はシーズン(日程と試合)のシード
        (省略時は seed と同じ)。球団名・自球団に問題があれば TeamNameError(理由つき)。"""
        gen, parts = load_generation_config(), load_name_parts()
        league = new_league(seed, team_names, gen, parts)
        if isinstance(my_team_index, bool) or not isinstance(my_team_index, int) or not 0 <= my_team_index < len(league.teams):
            raise TeamNameError([f"自球団の選び方が正しくありません(値: {my_team_index!r})"])
        season = Season(league, seed if season_seed is None else season_seed)
        return cls(GameState(season, gen, parts, "", league.teams[my_team_index].id), dirty=True)

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


__all__ = ["Game", "SaveDataError", "TeamNameError", "check_team_names", "preview_teams"]
