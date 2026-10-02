"""チーム編成と采配の簡易ルール(D-019、D-060、D-061)。

一軍の選抜・スタメン・打順・休養・先発投手・継投を決める。
ここにあるのは「簡易ルール」(SimpleManager)で、同じ形(Manager)を持つクラスを作れば差し替えられる。
使うのは能力値と状態だけ。生成時の型・成長タイプ・潜在能力は使わない(D-043)。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .abilities import BATTER, FIELDER_POSITIONS, FIELDING_ITEMS, PITCHER
from .fatigue import can_relieve, can_start, starter_batters_limit
from .game_config import GameConfig, load_game_config
from .models import Player, Team
from .stats import overall

DH = "DH"
RELIEF_ROLES = ("closer", "setup", "middle", "mopup")
RELIEF_ROLE_LABELS = {"closer": "抑え", "setup": "中継ぎ(勝ち)", "middle": "中継ぎ", "mopup": "敗戦処理"}


@dataclass
class ActiveRoster:
    """一軍(29人)。"""

    starters: list[Player]  # 先発ローテーションの順
    relievers: list[Player]
    relief_roles: dict[str, str]  # 救援投手の ID → 役割(抑え・中継ぎ・敗戦処理)
    batters: list[Player]

    @property
    def players(self) -> list[Player]:
        return self.starters + self.relievers + self.batters


@dataclass
class LineupSlot:
    order: int  # 打順(1〜9)
    player: Player  # 守備位置が本来と違うときは、守備の能力を下げた写し
    position: str  # 守備位置(C, 1B, ..., または DH)
    replaced_id: str | None = None  # 休養した主力の ID(控えが入ったとき)


@dataclass
class Lineup:
    slots: list[LineupSlot]
    bench: list[Player] = field(default_factory=list)

    def defense(self) -> dict[str, Player]:
        """守備につく野手8人(指名打者は含まない。D-046)。"""
        return {s.position: s.player for s in self.slots if s.position != DH}


@dataclass
class TeamSetup:
    """1試合に臨むチームの編成。"""

    team_id: str
    name: str
    active: ActiveRoster
    lineup: Lineup
    starter: Player


@dataclass
class PitchingSituation:
    """継投を決めるときの状況(守っているチームから見た値)。"""

    inning: int
    lead: int  # 守っているチームのリード(負けていればマイナス)
    used_ids: set[str]  # この試合ですでに投げた投手


class Manager(Protocol):
    """采配の形(差し替え用)。"""

    def select_active(self, team: Team) -> ActiveRoster: ...

    def starting_lineup(self, active: ActiveRoster, rng: random.Random) -> Lineup: ...

    def choose_starter(self, active: ActiveRoster, rotation_index: int) -> tuple[Player, int]: ...

    def starter_limit(self, starter: Player) -> float: ...

    def run_limit(self) -> int: ...

    def choose_reliever(self, active: ActiveRoster, situation: PitchingSituation) -> Player | None: ...


def weighted_score(player: Player, weights: Mapping[str, float]) -> float:
    total = sum(weights.values())
    return sum(player.ratings[i] * w for i, w in weights.items()) / total


def with_fielding_penalty(player: Player, penalty: float) -> Player:
    """守備の能力だけを penalty 点下げた写し(本来と違うポジションを守るとき。守備適性は F3)。"""
    if penalty <= 0:
        return player
    clone = copy.copy(player)
    clone.ratings = dict(player.ratings)
    for item in FIELDING_ITEMS:
        clone.ratings[item] -= penalty
    return clone


class SimpleManager:
    """簡易ルールの監督。数値は設定ファイル(game.json)から読む。"""

    def __init__(self, config: GameConfig | None = None):
        self.config = config or load_game_config()

    # ---- 一軍の選抜 ----

    def select_active(self, team: Team) -> ActiveRoster:
        ar = self.config["active_roster"]
        pitchers = [p for p in team.players if p.role == PITCHER]
        batters = [p for p in team.players if p.role == BATTER]

        def best(pool, n, chosen):
            pool = sorted((p for p in pool if p.id not in chosen), key=overall, reverse=True)
            return pool[:n]

        chosen: set[str] = set()
        starters = best([p for p in pitchers if p.position == "SP"], ar["starters"], chosen)
        chosen |= {p.id for p in starters}
        if len(starters) < ar["starters"]:  # 先発型が足りなければ、ほかの投手で埋める
            starters += best(pitchers, ar["starters"] - len(starters), chosen)
            chosen |= {p.id for p in starters}
        relievers = best([p for p in pitchers if p.position == "RP"], ar["relievers"], chosen)
        chosen |= {p.id for p in relievers}
        if len(relievers) < ar["relievers"]:
            relievers += best(pitchers, ar["relievers"] - len(relievers), chosen)
            chosen |= {p.id for p in relievers}

        squad = best([p for p in batters if p.position == "C"], ar["catchers"], chosen)
        chosen |= {p.id for p in squad}
        for pos in FIELDER_POSITIONS:
            if pos == "C":
                continue
            top = best([p for p in batters if p.position == pos], 1, chosen)
            squad += top
            chosen |= {p.id for p in top}
        squad += best([p for p in batters if p.position != "C"], ar["fielders"] - len(squad), chosen)

        roles = self._relief_roles(relievers)
        return ActiveRoster(starters=starters, relievers=relievers, relief_roles=roles, batters=squad)

    def _relief_roles(self, relievers: list[Player]) -> dict[str, str]:
        """総合値の高い順に、抑え → 中継ぎ(勝ち)→ 中継ぎ、低い投手を敗戦処理にする。"""
        p = self.config["pitching"]
        ranked = sorted(relievers, key=overall, reverse=True)
        roles = {}
        for i, r in enumerate(ranked):
            if i < p["closer_count"]:
                roles[r.id] = "closer"
            elif i < p["closer_count"] + p["setup_count"]:
                roles[r.id] = "setup"
            elif i >= len(ranked) - p["mopup_count"]:
                roles[r.id] = "mopup"
            else:
                roles[r.id] = "middle"
        return roles

    # ---- スタメン・打順・休養 ----

    def regular_lineup(self, active: ActiveRoster) -> list[tuple[Player, str]]:
        """休養なしのスタメン9人(選手, 守備位置)。各ポジションで総合値が最も高い野手+指名打者。"""
        lw = self.config["lineup"]
        starters: list[tuple[Player, str]] = []
        used: set[str] = set()
        for pos in FIELDER_POSITIONS:
            cands = [p for p in active.batters if p.position == pos and p.id not in used]
            if not cands:
                cands = [p for p in active.batters if p.id not in used]
            best = max(cands, key=overall)
            starters.append((best, pos))
            used.add(best.id)
        rest = [p for p in active.batters if p.id not in used]
        dh = max(rest, key=lambda p: weighted_score(p, lw["offense"]))
        starters.append((dh, DH))
        return starters

    def batting_order(self, nine: list[tuple[Player, str]]) -> list[tuple[Player, str]]:
        """打順:中軸(3〜5番)は長打の高い3人、1・2番は出塁の高い2人(足の速いほうが1番)、6〜9番は総合的な打撃の順。"""
        lw = self.config["lineup"]
        pool = list(nine)
        by_slug = sorted(pool, key=lambda x: weighted_score(x[0], lw["slugging"]), reverse=True)
        cleanup = by_slug[:3]
        pool = [x for x in pool if x not in cleanup]
        top = sorted(pool, key=lambda x: weighted_score(x[0], lw["on_base"]), reverse=True)[:2]
        top.sort(key=lambda x: x[0].ratings["speed"], reverse=True)
        pool = [x for x in pool if x not in top]
        bottom = sorted(pool, key=lambda x: weighted_score(x[0], lw["offense"]), reverse=True)
        return [top[0], top[1], cleanup[1], cleanup[0], cleanup[2]] + bottom

    def starting_lineup(self, active: ActiveRoster, rng: random.Random) -> Lineup:
        """打順を決めたうえで、主力を一定の確率で休ませ、控えを同じ打順・ポジションに入れる。"""
        rest_cfg = self.config["rest"]
        lw = self.config["lineup"]
        ordered = self.batting_order(self.regular_lineup(active))
        in_lineup = {p.id for p, _ in ordered}
        bench = [p for p in active.batters if p.id not in in_lineup]
        slots = []
        for order, (player, pos) in enumerate(ordered, start=1):
            prob = rest_cfg["catcher_probability"] if pos == "C" else rest_cfg["probability"]
            if bench and rng.random() < prob:
                sub, penalty = self._substitute(pos, bench, lw)
                if sub is not None:
                    bench.remove(sub)
                    slots.append(LineupSlot(order, with_fielding_penalty(sub, penalty), pos, replaced_id=player.id))
                    continue
            slots.append(LineupSlot(order, player, pos))
        return Lineup(slots=slots, bench=bench)

    def _substitute(self, pos: str, bench: list[Player], lw) -> tuple[Player | None, float]:
        """控えを選ぶ:同じポジション → 近いポジション(守備の能力を少し下げる)→ だれでも(大きく下げる)。"""
        rest_cfg = self.config["rest"]
        if pos == DH:
            return max(bench, key=lambda p: weighted_score(p, lw["offense"])), 0.0
        same = [p for p in bench if p.position == pos]
        if same:
            return max(same, key=overall), 0.0
        near = [p for p in bench if p.position in rest_cfg["near_positions"].get(pos, [])]
        if near:
            return max(near, key=overall), rest_cfg["near_position_penalty"]
        if pos == "C":
            return None, 0.0  # 捕手の控えがいなければ休ませない
        return max(bench, key=overall), rest_cfg["far_position_penalty"]

    # ---- 投手 ----

    def choose_starter(self, active: ActiveRoster, rotation_index: int) -> tuple[Player, int]:
        """ローテーションの順で、登板できる(疲労が上限以下の)先発を選ぶ。いなければ最も疲労の少ない先発。"""
        n = len(active.starters)
        for k in range(n):
            i = (rotation_index + k) % n
            if can_start(active.starters[i], self.config):
                return active.starters[i], (i + 1) % n
        p = min(active.starters, key=lambda x: x.state.fatigue)
        return p, (active.starters.index(p) + 1) % n

    def starter_limit(self, starter: Player) -> float:
        return starter_batters_limit(starter, self.config)

    def run_limit(self) -> int:
        return self.config["pitching"]["starter_run_limit"]

    def preferred_roles(self, situation: PitchingSituation) -> list[str]:
        """場面(イニングと点差)から、使いたい救援の役割の順を決める。"""
        p = self.config["pitching"]
        lead = situation.lead
        close = 0 <= lead <= p["close_game_max_lead"]
        last_innings = self.config["rules"]["innings"]
        if situation.inning >= last_innings and close and lead > 0:
            return ["closer", "setup", "middle", "mopup"]
        if situation.inning >= last_innings and lead == 0:
            return ["closer", "setup", "middle", "mopup"]
        if situation.inning >= p["setup_from_inning"] and close:
            return ["setup", "middle", "closer", "mopup"]
        if lead <= -p["mopup_deficit"]:
            return ["mopup", "middle", "setup", "closer"]
        return ["middle", "mopup", "setup", "closer"]

    def choose_reliever(self, active: ActiveRoster, situation: PitchingSituation) -> Player | None:
        """役割の優先順に、まだ投げていない・疲労が上限以下の救援から、疲労の少ない投手を選ぶ。"""
        for role in self.preferred_roles(situation):
            cands = [
                r
                for r in active.relievers
                if active.relief_roles.get(r.id) == role
                and r.id not in situation.used_ids
                and can_relieve(r, self.config)
            ]
            if cands:
                return min(cands, key=lambda r: (r.state.fatigue, -overall(r)))
        # 救援が使い切られた・疲れているときは、疲労の少ない先発を救援に回す(今日の先発・登板済みは除く)
        spare = [p for p in active.starters if p.id not in situation.used_ids and can_relieve(p, self.config)]
        if spare:
            return min(spare, key=lambda p: (p.state.fatigue, -overall(p)))
        return None  # だれもいなければ、今の投手が続投する

    # ---- まとめ ----

    def prepare(self, team: Team, rng: random.Random, rotation_index: int, active: ActiveRoster | None = None):
        """1試合分の編成を作る。戻り値:(TeamSetup, 次のローテーションの位置)。"""
        active = active or self.select_active(team)
        lineup = self.starting_lineup(active, rng)
        starter, nxt = self.choose_starter(active, rotation_index)
        return TeamSetup(team_id=team.id, name=team.name, active=active, lineup=lineup, starter=starter), nxt
