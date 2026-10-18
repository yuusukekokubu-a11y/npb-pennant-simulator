"""ポストシーズン(クライマックスシリーズと日本シリーズ。D-439〜D-441)。

レギュラーシーズンの後に、シーズンの日に続けて 1 日ずつ進める。
  - CS 1st ステージ:各リーグの 2 位 vs 3 位(すべて 2 位の球場)。CS ファイナル:1 位 vs 1st の勝者(1 位にアドバンテージ。
    すべて 1 位の球場)。日本シリーズ:両リーグの CS の勝者(2・3・2 で球場を交代。最初は奇数年が第一リーグ)。
  - 引き分けは数えない。CS で試合を使い切って決着がつかなければ順位が上の球団。日本シリーズは決着まで続ける。
  - ステージの間は休み(rest_days)。日本シリーズは travel_after 試合目の後に休み。2 リーグの CS は同じ日に並行する。
  - 試合の乱数は derive_seed(シーズンのシード, "post:<通し番号>")。成績・順位には入れない(Season._play の record=False)。
シリーズの状態は保存できる辞書(series)で持ち、試合は PlayedGame(games)で持つ。日程は状態から求める(保存しない)。
"""

from __future__ import annotations

import random

from .records import season_records
from .schedule import ScheduledGame

STAGE_KEYS = ("first", "final", "japan")
STAGE_LABELS = {"first": "CS 1st ステージ", "final": "CS ファイナルステージ", "japan": "日本シリーズ"}


def default_settings() -> dict:
    return {
        "first_stage": {"games": 3, "wins": 2},
        "final_stage": {"games": 6, "wins": 4, "advantage": 1},
        "japan_series": {"games": 7, "wins": 4, "travel_after": [2, 5]},
        "rest_days": [1, 1, 2],
    }


class Postseason:
    def __init__(self, season, settings: dict):
        self.season = season
        self.cfg = settings
        self.series: list[dict] = []
        self.games: list = []  # PlayedGame(scheduled.number はポストシーズンの通し番号)
        self.start_day = season.total_days

    # ---- 始める ----

    def begin(self) -> None:
        """レギュラーシーズンの最終順位から CS を組む(1st がなければファイナルから)。"""
        rest = self.cfg["rest_days"]
        start = self.start_day + int(rest[0])
        firsts = []
        for li in self.season.league_indexes():
            order = [r.team_id for r in self.season.standings(li)]
            if len(order) >= 3:
                firsts.append(self._new("first", li, order[1], order[2], start, order))
        if firsts:
            self.series += firsts
        else:
            self._open_final(start)

    def _new(self, stage: str, league, hi: str, lo: str, start: int, order=None) -> dict:
        c = self._stage_cfg(stage)
        adv = int(c.get("advantage", 0))
        return {
            "stage": stage,
            "league": league,
            "teams": [hi, lo],  # 順位が上(CS のホーム)、下。日本シリーズは [最初にホームの球団, もう一方]
            "seeds": [] if order is None else [order.index(hi) + 1, order.index(lo) + 1],
            "wins": [adv, 0],
            "ties": 0,
            "start": int(start),
            "games": [],  # ポストシーズンの通し番号
            "winner": None,
            "mvp": None,
        }

    def _stage_cfg(self, stage: str) -> dict:
        return self.cfg[{"first": "first_stage", "final": "final_stage", "japan": "japan_series"}[stage]]

    def _open_final(self, start: int) -> None:
        for li in self.season.league_indexes():
            order = [r.team_id for r in self.season.standings(li)]
            first = next((s for s in self.series if s["stage"] == "first" and s["league"] == li), None)
            lo = first["winner"] if first else order[1]
            s = self._new("final", li, order[0], lo, start, order)
            self.series.append(s)

    def _open_japan(self, start: int) -> None:
        finals = {s["league"]: s["winner"] for s in self.series if s["stage"] == "final"}
        idx = sorted(finals)
        first_home = idx[0] if self.season.year % 2 == 1 else idx[-1]
        other = next(i for i in idx if i != first_home)
        self.series.append(self._new("japan", None, finals[first_home], finals[other], start))

    # ---- 日程 ----

    def game_day(self, s: dict, i: int) -> int:
        """シリーズの i 試合目(0 から)の日。"""
        travel = self._stage_cfg(s["stage"]).get("travel_after", []) if s["stage"] == "japan" else []
        return s["start"] + i + sum(1 for t in travel if int(t) <= i)

    def next_game(self, s: dict) -> int | None:
        if s["winner"] is not None:
            return None
        return self.game_day(s, len(s["games"]))

    def home_of(self, s: dict, i: int) -> int:
        """i 試合目のホーム(teams の 0 か 1)。CS は順位が上、日本シリーズは 2・3・2。"""
        if s["stage"] != "japan":
            return 0
        return 0 if i % 7 in (0, 1, 5, 6) else 1

    @property
    def finished(self) -> bool:
        return any(s["stage"] == "japan" and s["winner"] is not None for s in self.series)

    @property
    def champion(self) -> str | None:
        return next((s["winner"] for s in self.series if s["stage"] == "japan" and s["winner"] is not None), None)

    def next_day(self) -> int | None:
        days = [d for d in (self.next_game(s) for s in self.series) if d is not None]
        return min(days) if days else None

    def games_on(self, day: int) -> list[tuple[dict, int]]:
        return [(s, len(s["games"])) for s in self.series if self.next_game(s) == day]

    # ---- 進める ----

    def play_day(self, day: int) -> list:
        played = []
        for s, i in self.games_on(day):
            h = self.home_of(s, i)
            home, away = s["teams"][h], s["teams"][1 - h]
            number = len(self.games)
            g = ScheduledGame(number, day, self.season.teams[home].league_index, home, away)
            pg = self.season._play(g, rng=self.rng(number), record=False)
            self.games.append(pg)
            s["games"].append(number)
            r = pg.result
            if r.tie:
                s["ties"] += 1
            else:
                s["wins"][s["teams"].index(r.winner)] += 1
            self._decide(s)
            played.append(pg)
        self._advance_stages(day)
        return played

    def rng(self, number: int) -> random.Random:
        from .season import derive_seed

        return random.Random(derive_seed(self.season.seed, f"post:{number}"))

    def _decide(self, s: dict) -> None:
        c = self._stage_cfg(s["stage"])
        need = int(c["wins"])
        for k in (0, 1):
            if s["wins"][k] >= need:
                s["winner"] = s["teams"][k]
        if s["winner"] is None and s["stage"] != "japan" and len(s["games"]) >= int(c["games"]):
            s["winner"] = s["teams"][0]  # 試合を使い切ったら順位が上の球団
        if s["winner"] is not None:
            s["mvp"] = self._mvp(s)

    def _advance_stages(self, day: int) -> None:
        stages = {s["stage"] for s in self.series}
        rest = self.cfg["rest_days"]
        current = [s for s in self.series if s["stage"] == max(stages, key=STAGE_KEYS.index)]
        if not all(s["winner"] is not None for s in current):
            return
        last = max(self.games[s["games"][-1]].scheduled.day for s in current)
        stage = current[0]["stage"]
        if stage == "first":
            self._open_final(last + int(rest[1]) + 1)
        elif stage == "final":
            self._open_japan(last + int(rest[2]) + 1)

    # ---- MVP ----

    def _mvp(self, s: dict) -> dict | None:
        rec = season_records(self.games[n].result for n in s["games"])
        tid = s["winner"]
        cands = []
        for pid, c in rec.batters.items():
            if rec.batter_team.get(pid) == tid:
                cands.append((c["TB"] + c["RBI"], 0, pid, "batter"))
        for pid, c in rec.pitchers.items():
            if rec.pitcher_team.get(pid) == tid:
                cands.append((c["OUTS"] / 3 - 2 * c["ER"] + 3 * c["W"] + 2 * c["SV"], 1, pid, "pitcher"))
        if not cands:
            return None
        score, _, pid, role = sorted(cands, key=lambda x: (-x[0], x[1], x[2]))[0]
        p = self.season.players.get(pid)
        line = rec.batters.get(pid) if role == "batter" else rec.pitchers.get(pid)
        stats = {k: int(line[k]) for k in (("H", "HR", "RBI") if role == "batter" else ("W", "SV", "OUTS", "ER"))}
        return {"id": pid, "name": p.name if p else pid, "team_id": tid, "role": role, **stats}

    # ---- 保存 ----

    def to_dict(self) -> dict:
        return {"series": [dict(s, teams=list(s["teams"]), seeds=list(s["seeds"]), wins=list(s["wins"]), games=list(s["games"])) for s in self.series]}

    def summary(self) -> dict:
        """履歴に残す形(試合の記録を除く)。"""
        return {"champion": self.champion, "series": [{k: (list(v) if isinstance(v, list) else v) for k, v in s.items() if k not in ("games", "start")} | {"games": len(s["games"])} for s in self.series]}
