"""二軍の試合(D-446)。

一軍の公式戦の日に、同じ組み合わせで二軍どうしの試合を行う(オールスター・ポストシーズンの日はなし)。
  - 二軍は、一軍にいない支配下の選手と育成選手(けが人を除く)。顔ぶれ・スタメン・先発の順番は AI が一軍と同じ決まりで組む。
  - 人数が足りない(投手 7 人・野手 10 人・捕手 1 人に満たない)球団の試合は、その日は行わない。
  - 記録は選手ごとの元の数(Records)と順位(勝敗・対戦成績)だけ。試合の文章ログは残さない。
  - 乱数は derive_seed(シーズンのシード, "farm:<番号>")(番号は一軍の同じ組み合わせの試合の番号)。
"""

from __future__ import annotations

import dataclasses
import random

from .abilities import BATTER, PITCHER
from .fatigue import apply_game_fatigue
from .game import simulate_game
from .records import Records, game_records

MIN_PITCHERS = 7
MIN_BATTERS = 10
YOUNG_AGE = 25  # 出場と若手の成長(D-447):この年齢まで
PLAYING_ENOUGH = 250  # 一軍と二軍の打席(投手は対戦打者)の合計がこれ以上なら伸びやすく
PLAYING_FEW = 30  # これ未満なら伸びにくく
PLAYING_FACTORS = (1.15, 0.9)


class Farm:
    def __init__(self, season):
        self.season = season
        self.records = Records()
        self.standings: dict[str, list] = {t.id: [0, 0, 0, {}] for t in season.league.teams}  # 勝・敗・分・{相手: [勝, 敗, 分]}
        self.rotation: dict[str, int] = {t.id: 0 for t in season.league.teams}
        self.games = 0

    def roster(self, team):
        """その日の二軍の選手(一軍・けが人を除く支配下 + 育成)。"""
        s = self.season
        active = {p.id for p in s.actives[team.id].players}
        return [p for p in list(team.players) + list(team.development) if p.id not in active and not s.is_injured(p.id)]

    def play_day(self, day: int, games) -> list:
        """一軍のその日の組み合わせ(ScheduledGame)で二軍の試合を行う。戻り値は試合の結果の一覧。"""
        s = self.season
        out = []
        for g in games:
            setups = {}
            rng = random.Random(_seed(s.seed, g.number))
            for tid in (g.home_id, g.away_id):
                team = s.teams[tid]
                players = self.roster(team)
                pitchers = [p for p in players if p.role == PITCHER]
                batters = [p for p in players if p.role == BATTER]
                if len(pitchers) < MIN_PITCHERS or len(batters) < MIN_BATTERS or not any(p.position == "C" for p in batters):
                    setups = None
                    break
                farm_team = dataclasses.replace(team, players=players, development=[])
                active = s.manager.select_active(farm_team)
                setups[tid], self.rotation[tid] = s.manager.prepare(farm_team, rng, self.rotation[tid], active, None)
            if setups is None:
                continue
            fatigue = {p.id: p.state.fatigue for st in setups.values() for p in st.active.starters + st.active.relievers}
            result = simulate_game(setups[g.home_id], setups[g.away_id], rng, model=s.model, config=s.game_config, manager=s.manager, park=s.teams[g.home_id].park, running=True)
            apply_game_fatigue(result.batters_faced(), s.all_players(), s.game_config)
            self.records.add(game_records(result))
            self._record(result)
            self.games += 1
            s.check_injuries(result, fatigue, "farm", g.number, day)
            out.append(result)
        return out

    def _record(self, r) -> None:
        h, a = self.standings[r.home_team_id], self.standings[r.away_team_id]
        hv = h[3].setdefault(r.away_team_id, [0, 0, 0])
        av = a[3].setdefault(r.home_team_id, [0, 0, 0])
        if r.tie:
            for x in (h, a, hv, av):
                x[2] += 1
        elif r.home_runs > r.away_runs:
            h[0] += 1
            a[1] += 1
            hv[0] += 1
            av[1] += 1
        else:
            a[0] += 1
            h[1] += 1
            av[0] += 1
            hv[1] += 1

    def to_dict(self) -> dict:
        from .history import records_to_dict

        return {"records": records_to_dict(self.records), "standings": {tid: [w, l, t, {o: list(v) for o, v in vs.items()}] for tid, (w, l, t, vs) in self.standings.items()},
                "rotation": dict(self.rotation), "games": self.games}

    @classmethod
    def from_dict(cls, season, d: dict) -> "Farm":
        from .history import records_from_dict

        f = cls(season)
        f.records = records_from_dict(d["records"])
        for tid, (w, l, t, vs) in d["standings"].items():
            if tid not in f.standings:
                raise ValueError(f"知らない球団です({tid})")
            f.standings[tid] = [int(w), int(l), int(t), {str(o): [int(x) for x in v] for o, v in vs.items()}]
        for tid, v in d["rotation"].items():
            if tid in f.rotation:
                f.rotation[tid] = int(v)
        f.games = int(d.get("games", 0))
        return f


def playing_growth(players, *records) -> dict[str, float]:
    """出場と若手の成長(D-447):YOUNG_AGE 歳以下の選手の、一軍と二軍の打席(投手は対戦打者)の合計による伸びの倍率。"""
    out = {}
    for p in players:
        if p.age > YOUNG_AGE:
            continue
        n = 0
        for rec in records:
            if rec is None:
                continue
            c = (rec.pitchers if p.role == PITCHER else rec.batters).get(p.id)
            if c:
                n += int(c["BF"] if p.role == PITCHER else c["PA"])
        if n >= PLAYING_ENOUGH:
            out[p.id] = PLAYING_FACTORS[0]
        elif n < PLAYING_FEW:
            out[p.id] = PLAYING_FACTORS[1]
    return out


def _seed(seed: int, number: int) -> int:
    from .season import derive_seed

    return derive_seed(seed, f"farm:{number}")
