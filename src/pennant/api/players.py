"""画面から呼ぶ操作の関数のうち、選手のページ(年度別の成績・所属のない選手の表示)。"""

from __future__ import annotations

from ..abilities import POSITION_LABELS
from ..newgame import public_player
from ..records import qualified_batters, qualified_pitchers
from ..scouting import ScoutReport
from .common import (
    BATS_LABELS,
    KIND_LABELS,
    QUALIFY_RULES,
    ROLE_LABELS,
    THROWS_LABELS,
    WAR_COLUMNS,
    WAR_TERMS,
    _values,
    _war_values,
    column_info,
    metrics_config,
)


class PlayersMixin:
    """Game の一部:選手のページ(年度別の成績・所属のない選手の表示)。"""

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
