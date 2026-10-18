"""画面から呼ぶ操作の関数のうち、ポストシーズン(組み合わせ表・試合の一覧。D-439〜D-441)。"""

from __future__ import annotations

from ..postseason import STAGE_LABELS

POST_OFFSET = 100000  # 試合の番号:これ以上はポストシーズンの試合(POST_OFFSET + 通し番号)


class PostseasonMixin:
    """Game の一部:ポストシーズン。"""

    def _series_label(self, s: dict) -> str:
        lnames = self.state.league.league_names
        if s["stage"] == "japan":
            return STAGE_LABELS["japan"]
        ln = lnames[s["league"]] if s["league"] < len(lnames) else f"リーグ {s['league'] + 1}"
        return f"{ln} {STAGE_LABELS[s['stage']]}"

    def _series_view(self, s: dict, names: dict) -> dict:
        post = self.state.season.postseason
        my = self.state.my_team_id
        teams = []
        for k, tid in enumerate(s["teams"]):
            teams.append({"team_id": tid, "name": names.get(tid, tid), "seed": s["seeds"][k] if s["seeds"] else None, "wins": s["wins"][k], "is_mine": tid == my, "winner": s["winner"] == tid})
        games = []
        for n in s["games"]:
            r = post.games[n].result
            games.append({"game_no": POST_OFFSET + n, "day": post.games[n].scheduled.day + 1, "home": names.get(r.home_team_id, ""), "away": names.get(r.away_team_id, ""), "score": f"{r.away_runs}-{r.home_runs}", "tie": r.tie})
        mvp = None
        if s["mvp"]:
            m = s["mvp"]
            mvp = {"name": m["name"], "team": names.get(m["team_id"], ""), "role": m["role"], "text": _mvp_text(m)}
        return {"stage": s["stage"], "label": self._series_label(s), "teams": teams, "ties": s["ties"], "advantage": int(post.cfg["final_stage"].get("advantage", 0)) if s["stage"] == "final" else 0, "games": games, "winner": names.get(s["winner"]) if s["winner"] else None, "mvp": mvp}

    def postseason_view(self) -> dict | None:
        """組み合わせ表(D-440):ステージの順に各シリーズの勝敗・試合・勝ち抜け・MVP と、日本一。始まる前は None。"""
        post = self.state.season.postseason
        if post is None:
            return None
        names = self._team_names()
        series = [self._series_view(s, names) for s in post.series]
        nxt = post.next_day()
        return {
            "series": series,
            "champion": names.get(post.champion) if post.champion else None,
            "champion_is_mine": post.champion is not None and post.champion == self.state.my_team_id,
            "finished": post.finished,
            "next_day": None if nxt is None else nxt + 1,
            "next_label": None if nxt is None else "・".join(self._post_game_label(s, i) for s, i in post.games_on(nxt)),
        }

    def _post_game_label(self, s: dict, i: int) -> str:
        return f"{self._series_label(s)} 第{i + 1}戦"

    def _post_day_kind(self, day: int) -> dict:
        post = self.state.season.postseason
        if post is None:
            return {"kind": "off", "label": "休み"}
        games = post.games_on(day) if day >= self.state.season.day else [(s, i) for s in post.series for i, n in enumerate(s["games"]) if post.games[n].scheduled.day == day]
        if not games:
            return {"kind": "off", "label": "休み(ポストシーズン)"}
        return {"kind": "postseason", "label": "・".join(self._post_game_label(s, i) for s, i in games)}

    def _post_game_summary(self, k: int) -> dict:
        post = self.state.season.postseason
        played = post.games[k]
        r = played.result
        names = self._team_names()
        s = next(x for x in post.series if k in x["games"])
        tags = [self._post_game_label(s, s["games"].index(k))] + [t for t, on in (("延長", r.extra_innings), ("サヨナラ", r.walkoff), ("引き分け", r.tie)) if on]
        return {
            "game_no": POST_OFFSET + k,
            "day": played.scheduled.day + 1,
            "league_index": played.scheduled.league_index,
            "away": {"team_id": r.away_team_id, "name": names[r.away_team_id], "runs": r.away_runs},
            "home": {"team_id": r.home_team_id, "name": names[r.home_team_id], "runs": r.home_runs},
            "stadium": self._team(r.home_team_id).stadium,
            "winner": r.winner,
            "innings": r.innings,
            "tags": tags,
            "is_mine": self.state.my_team_id in (r.home_team_id, r.away_team_id),
            "postseason": True,
        }


def _mvp_text(m: dict) -> str:
    if m["role"] == "batter":
        return f"{m['H']}安打 {m['HR']}本塁打 {m['RBI']}打点"
    outs = int(m["OUTS"])
    return f"{outs // 3}回{'' if outs % 3 == 0 else f'{outs % 3}/3'} 自責点{m['ER']}" + (f" {m['W']}勝" if m["W"] else "") + (f" {m['SV']}セーブ" if m["SV"] else "")
