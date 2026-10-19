"""画面から呼ぶ操作の関数のうち、能力の表(作り直し①。D-335、D-338、D-347)。

プロの選手の見せる能力は、いつでも見せる(答え合わせモードはなくした。D-338)。
伸びやすさ・成長タイプ・生成時の型(隠し情報)は返さない。
"""

from __future__ import annotations

from .. import ability_model
from ..skills import public_skills
from ..abilities import ABILITY_LABELS, BATTER, abilities_for
from .common import KIND_LABELS, WAR_COLUMNS, metrics_config


def ability_columns(role: str) -> list[dict]:
    """能力の表の列(野手:弾道・ミート・パワー・走力・肩力・守備力・捕球、投手:球速・コントロール・スタミナ・変化球)。"""
    keys = (("trajectory",) if role == BATTER else ()) + abilities_for(role)
    return [{"key": k, "label": ABILITY_LABELS[k], "type": "ability"} for k in keys] + [{"key": "skills", "label": "特殊能力", "type": "ability"}]


def stats_slots(config, role: str) -> int:
    """個人成績の表の列の枠の数(D-483):基本・セイバー・WAR・能力のどの表示でも足りる数(表示にない列で並べたときの固定列の 1 つを含む)。"""
    counts = [len(config["tables"][role][k]["columns"]) for k in KIND_LABELS] + [len(WAR_COLUMNS[role]), len(ability_columns(role))]
    return max(counts) + 1


def skills_text(player) -> str:
    """特殊能力を 1 つの文字に(表の列。D-374)。なければ空。"""
    return "・".join(s["label"] for s in public_skills(player))


def _sort_value(config, player, key: str):
    if key == "trajectory":
        return ability_model.trajectory(config, player)
    if key == "skills":  # 特殊能力:良いものの数 − 悪いものの数
        return sum(1 if s["good"] else -1 for s in public_skills(player))
    return float(player.abilities[key])


class AbilitiesMixin:
    """Game の一部:能力の表・オフの能力の増減。"""

    def player_abilities(self, player) -> list[dict]:
        return ability_model.public_abilities(self.state.gen_config, player)

    def ability_columns(self, role: str = "batter") -> list[dict]:
        return ability_columns(role)

    def ability_table(self, role: str = "batter", sort: str | None = None, order: str = "desc", qualified: bool = True, league: int | None = None, team_id: str | None = None) -> dict:
        """能力の表(成績の「能力」。D-115 の形を引き継ぐ)。選手の選び方は個人成績と同じ。並べ替えは内部の値(丸める前)。
        sort には成績の列(元の数・指標・WAR の列)も使える(D-132、D-483)。そのときは extra_column にその列を返す。値なしの選手は最後。"""
        cfg = self.state.gen_config
        cols = ability_columns(role)
        keys = [c["key"] for c in cols]
        pids = self.select_players(role, qualified, league, team_id)
        cross = None
        if sort not in keys:
            cross = self._cross_sort(role, sort, pids, self._season_view(None)) if sort else None
            if cross is None:
                sort = keys[0]
        extra = cross[0] if cross else None
        if order not in ("asc", "desc"):
            order = ("desc" if extra.get("better", "high") == "high" else "asc") if extra else "desc"
        rec = self.records.total
        owner = rec.batter_team if role == "batter" else rec.pitcher_team
        rows = []
        for pid in pids:
            p = self.state.season.players[pid]
            row = self._player_row(pid, owner[pid])
            shown = {a["key"]: a["text"] for a in ability_model.public_abilities(cfg, p)}
            row["values"] = {**shown, "skills": skills_text(p)}
            if cross:
                row["values"][sort] = cross[1][pid][1]
                row["_sort"] = cross[1][pid][0]
            else:
                row["_sort"] = _sort_value(cfg, p, sort)
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
        sort_col = extra or next(c for c in cols if c["key"] == sort)
        return {"role": role, "columns": cols, "sort": sort_col, "order": order, "extra_column": extra, "slots": stats_slots(metrics_config(), role), "qualified": qualified, "rows": rows}

    def offseason_changes(self, year: int | None = None) -> dict:
        """オフの能力の増減(年度の確定の前後。見せる能力の目盛り(1〜100)での差。球速は km/h)。"""
        if not self.state.offseasons:
            return {"available": False, "players": []}
        if year is None:
            year = self.state.offseasons[-1].year
        r = next((o for o in self.state.offseasons if o.year == year), None)
        if r is None:
            raise ValueError(f"{year} シーズン目のオフの結果はありません")
        cfg = self.state.gen_config
        per_point = float(cfg["abilities"]["display"]["per_point"])
        kmh = float(cfg["abilities"]["velocity"]["per_point"])
        names = {t.id: t.name for t in self.state.league.teams}
        players = {p.id: p for p in self.state.league.all_players() + self.state.league.development_players()}  # 育成選手の増減も(D-375)
        rows = []
        for pid, changes in r.ability_changes.items():
            p = players.get(pid)
            if p is None:
                continue
            items = []
            for item in abilities_for(p.role):
                if item not in changes:
                    continue
                scale = kmh if item == "velocity" else per_point
                items.append({"key": item, "label": ABILITY_LABELS[item], "change": f"{changes[item] * scale:+.0f}"})
            mean = sum(changes.values()) / len(changes) * per_point if changes else 0.0
            rows.append({"player_id": pid, "name": p.name, "team_id": p.team_id, "team_name": names.get(p.team_id, p.team_id), "role": p.role, "position": p.position, "age": r.ages.get(pid, p.age), "mean_change": f"{mean:+.1f}", "items": items, "is_mine": p.team_id == self.state.my_team_id})
        rows.sort(key=lambda d: (d["team_id"], d["player_id"]))
        return {"available": True, "year": year, "players": rows}
