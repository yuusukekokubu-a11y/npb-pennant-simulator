"""画面から呼ぶ操作の関数のうち、試合(直近の試合・日付ごとの試合・試合のページ)。"""

from __future__ import annotations

from ..game_stats import game_story


class GamesMixin:
    """Game の一部:試合(直近の試合・日付ごとの試合・試合のページ)。"""

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

    def _game_summary(self, n: int) -> dict:
        played = self.state.season.played[n]
        r = played.result
        names = self._team_names()
        tags = [t for t, on in (("交流戦", played.scheduled.interleague), ("延長", r.extra_innings), ("サヨナラ", r.walkoff), ("引き分け", r.tie)) if on]
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
        season = self.state.season
        games = [self._game_summary(i) for i, p in enumerate(season.played) if p.scheduled.day + 1 == day]
        kind = self._day_kind(day - 1) if 1 <= day <= season.day else None  # 休み・オールスターの日(D-435、D-436)
        stars = [a for a in self.allstar_results() if a["day"] == day]
        shown = lambda d: season.is_game_day(d - 1) or (d - 1) in season.schedule.allstar_days  # 試合のある日(1 から)
        prev = next((d for d in range(min(day, season.day + 1) - 1, 0, -1) if shown(d)), None)  # 前後の試合のある日(休みを飛ばす)
        nxt = next((d for d in range(max(day, 0) + 1, season.day + 1) if shown(d)), None)
        return {"day": day, "last_day": season.day, "total_days": season.total_days, "games": games, "day_kind": kind, "allstar": stars[0] if stars else None, "prev_day": prev, "next_day": nxt}

    def last_day_games(self) -> dict:
        """直近の試合の日(最後に進めた日から、休みの日を除いて)の全試合(D-117、D-435)。"""
        season = self.state.season
        day = season.day
        while day > 1 and not season.is_game_day(day - 1):
            day -= 1
        return self.games_on(day)

    def game(self, game_no: int) -> dict:
        """1試合の文章ログと投手の成績(勝敗・セーブ・ホールドの印つき)。"""
        season = self.state.season
        if not 0 <= int(game_no) < len(season.played):
            raise ValueError(f"試合 {game_no} は、まだ行っていません")
        n = int(game_no)
        story = game_story(season.played[n].result, season.players, self._team_names(), self.records.decisions[n])
        return {"summary": self._game_summary(n), "story": story}
