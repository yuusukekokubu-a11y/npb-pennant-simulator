"""画面から呼ぶ操作の関数のうち、成績の表(個人成績・WAR・成績つきの選手の表)。"""

from __future__ import annotations

from fractions import Fraction

from ..abilities import POSITION_LABELS
from ..contracts import remaining_years
from ..metrics import innings_text
from ..records import qualified_batters, qualified_pitchers
from .common import (
    BATS_LABELS,
    KIND_LABELS,
    QUALIFY_RULES,
    ROLE_LABELS,
    ROSTER_BASE_COLUMNS,
    THROWS_LABELS,
    WAR_COLUMNS,
    WAR_KIND,
    _BASELINE_METRICS,
    _SeasonView,
    _WAR_SORT_KEYS,
    _values,
    _war_values,
    column_info,
    is_sortable,
    metrics_config,
    table_cols,
)


class StatsMixin:
    """Game の一部:成績の表(個人成績・WAR・成績つきの選手の表)。"""

    def select_players(self, role: str, qualified: bool = True, league: int | None = None, team_id: str | None = None, season: str | int | None = None) -> list[str]:
        """個人成績に出す選手(試合に出た選手。規定到達者・リーグ・チームで絞り込む)。"""
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        rec = self._season_view(season).records
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

    def _player_row(self, pid: str, team_id: str, view: "_SeasonView | None" = None) -> dict:
        p = view.info(pid) if view else self._current_player_info(pid)
        team = self._team(team_id)
        return {"player_id": pid, "name": p["name"], "team_id": team_id, "team_name": team.name, "position": POSITION_LABELS[p["position"]], "is_mine": team_id == self.state.my_team_id}

    def stats(
        self,
        role: str = "batter",
        kind: str = "basic",
        sort: str | None = None,
        order: str | None = None,
        qualified: bool = True,
        league: int | None = None,
        team_id: str | None = None,
        season: str | int | None = None,
    ) -> dict:
        """個人成績の表(D-116)。列・既定の並び順は指標の定義データの tables から(D-119)。
        season は None(今シーズン)、過去のシーズン番号、"career"(通算。F2。D-182)。

        sort は列(元の数か指標)の名前。表の列に限らず、その役割の元の数・全指標を使える(D-132)。
        表にない指標で並べたときは、extra_column にその列を返し、各行の values にも値を入れる(画面は名前の隣に出す)。
        order は "desc"(大きい順)か "asc"(小さい順)。省略時は、その指標の「よい」向き(打率なら高い順、防御率なら低い順)。
        値なし(分母が 0)の選手は、向きによらず最後に並べる。
        """
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        view = self._season_view(season)
        if kind == WAR_KIND:
            return self._war_stats(role, sort, order, qualified, league, team_id, view)
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
        rec = view.records
        group, owner = (rec.batters, rec.batter_team) if role == "batter" else (rec.pitchers, rec.pitcher_team)
        rows = []
        base = view.baselines
        for pid in self.select_players(role, qualified, league, team_id, view.key):
            values = _values(config, role, group[pid], base, view.park_factor(pid) if role == "batter" else None, (view.override or {}).get(pid))
            row = self._player_row(pid, owner[pid], view)
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
            "day": self.state.season.day if view.key == "current" else view.day,
            "baseline_note": view.baseline_note if any(c["key"] in _BASELINE_METRICS for c in table_cols(config, role, kind)) else None,
            "season": view.key,
            "season_label": view.label,
        }

    def _war_stats(self, role: str, sort: str | None, order: str | None, qualified: bool, league: int | None, team_id: str | None, view: "_SeasonView") -> dict:
        """個人成績の「WAR」の表(③b。D-179)。列は WAR_COLUMNS。並び順は WAR の列だけ(既定は WAR の高い順)。"""
        columns = WAR_COLUMNS[role]
        default = "war" if role == "batter" else "war_ra"
        sort = sort if sort in _WAR_SORT_KEYS[role] else default
        info = next(c for c in columns if c["key"] == sort)
        if order not in ("asc", "desc"):
            order = "desc"
        lines = view.war
        rec = view.records
        owner = rec.batter_team if role == "batter" else rec.pitcher_team
        rows = []
        for pid in self.select_players(role, qualified, league, team_id, view.key):
            line = lines.get(pid)
            if line is None:
                continue
            values = _war_values(line)
            row = self._player_row(pid, owner[pid], view)
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
            "day": self.state.season.day if view.key == "current" else view.day,
            "baseline_note": view.war_note,
            "season": view.key,
            "season_label": view.label,
        }

    def roster_seasons(self) -> list[dict]:
        """選手の一覧の表で選べるシーズン(今シーズンと、2 シーズン目以降は通算)。"""
        out = [{"key": "current", "label": f"今シーズン({self.state.year}シーズン目)"}]
        if len(self.state.history) > (1 if self.state.procedure is not None else 0):  # 手続き中は、終わったシーズンがすでに履歴にある
            out.append({"key": "career", "label": "通算"})
        return out

    def roster_table(self, players: list, role: str, kind: str = "basic", sort: str | None = None, order: str | None = None, season: str | None = None, base_columns: list | None = None, extra_values: dict | None = None) -> dict:
        """任意の選手の一覧(自球団の全選手、市場の選手)を、個人成績と同じ列・値で表にする(D-222)。
        基本の列(ポジション・年齢・打席か投球回)+ 選んだ種類(基本・セイバー・WAR)の列。並び順は全部の列と、その役割の全指標・WAR の列から選べ、
        表にない指標で並べたときは extra_column で返す(D-131 と同じ)。成績のない選手の値は「—」で、並び順によらず最後。初期は WAR の低い順。"""
        if role not in ROLE_LABELS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        if season not in (None, "current") and season not in [x["key"] for x in self.roster_seasons()]:
            raise ValueError(f"このシーズンは選べません(値: {season!r})")
        view = self._season_view(None if season in (None, "current") else season)
        config = metrics_config()
        base_cols = base_columns if base_columns is not None else ROSTER_BASE_COLUMNS[role]
        if kind == WAR_KIND:
            cols = list(WAR_COLUMNS[role])
        elif kind in KIND_LABELS:
            cols = table_cols(config, role, kind)
        else:
            raise ValueError(f"基本・セイバー・WAR を選んでください(値: {kind!r})")
        cols = [c for c in cols if c["key"] not in ("PA", "OUTS", "plate_appearances", "innings")]  # 打席・投球回は基本の列にあるので重ねない
        shown = {c["key"]: c for c in base_cols + cols}
        if not sort:  # 初期は WAR の低い順(手放す候補が上に来る。D-222)
            sort = "war" if role == "batter" else "war_ra"
            order = order or "asc"
        if sort in shown:
            info, extra = shown[sort], None
        elif sort in _WAR_SORT_KEYS[role]:
            info = next(c for c in WAR_COLUMNS[role] if c["key"] == sort)
            extra = info
        elif is_sortable(config, role, sort):
            info = column_info(config, role, sort)
            extra = info
        else:
            raise ValueError(f"並び順に使えない列です(値: {sort!r})")
        if order not in ("asc", "desc"):
            order = "desc" if info.get("better", "high") == "high" else "asc"
        keys = [c["key"] for c in base_cols + cols] + ([sort] if extra else [])
        rec = view.records
        group = rec.batters if role == "batter" else rec.pitchers
        names = self._team_names()
        positions = list(POSITION_LABELS)
        rows = []
        for p in players:
            if p.role != role:
                continue
            counts = group.get(p.id)
            metrics = _values(config, role, counts, view.baselines, view.park_factor(p.id) if role == "batter" else None, (view.override or {}).get(p.id)) if counts is not None else {}
            line = view.war.get(p.id)
            war = _war_values(line) if line is not None else {}
            if counts is None:
                usage = (None, "—")
            elif role == "batter":
                usage = (Fraction(int(counts.get("PA", 0))), str(int(counts.get("PA", 0))))
            else:
                usage = (Fraction(int(counts.get("OUTS", 0))), innings_text(int(counts.get("OUTS", 0))))
            base = {"pos": (positions.index(p.position), POSITION_LABELS[p.position]), "age": (p.age, f"{p.age}歳"), "usage": usage}
            if p.contract:
                base["salary"] = (int(p.contract["salary"]), f"{int(p.contract['salary']):,}")
                base["years"] = (remaining_years(p.contract, self.state.year + (1 if self.state.procedure is not None else 0)), str(remaining_years(p.contract, self.state.year + (1 if self.state.procedure is not None else 0))))
            else:
                base["salary"] = (None, "—")
                base["years"] = (None, "—")
            cell = {}
            ext = (extra_values or {}).get(p.id, {})
            for k in keys:
                v = ext.get(k) or base.get(k) or metrics.get(k) or war.get(k)
                cell[k] = v if v is not None else (None, "—")
            row = {
                "player_id": p.id, "name": p.name, "role": role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if role == "batter" else THROWS_LABELS.get(p.throws),
                "team_id": p.team_id, "team_name": names.get(p.team_id, "") if p.team_id else "", "has_stats": counts is not None,
                "values": {k: v[1] for k, v in cell.items()}, "_sort": cell[sort][0],
            }
            rows.append(row)
        present = [r for r in rows if r["_sort"] is not None]
        missing = [r for r in rows if r["_sort"] is None]
        present.sort(key=lambda r: r["player_id"])
        present.sort(key=lambda r: r["_sort"], reverse=order == "desc")
        missing.sort(key=lambda r: r["player_id"])
        rows = present + missing
        for r in rows:
            r.pop("_sort")
        return {
            "role": role, "role_label": ROLE_LABELS[role], "kind": kind, "kind_label": "WAR" if kind == WAR_KIND else KIND_LABELS[kind],
            "columns": base_cols + cols, "sort": info, "order": order, "extra_column": extra, "rows": rows,
            "season": view.key, "season_label": view.label, "seasons": self.roster_seasons(),
            "baseline_note": view.baseline_note if any(c["key"] in _BASELINE_METRICS for c in cols) else None,
        }
