"""画面から呼ぶ操作の関数のうち、判断の画面の表(FA・ドラフト・市場。①b。D-296〜D-299)と、加入後の序列・手薄なポジション・自球団の状況。"""

from __future__ import annotations

import math
from fractions import Fraction

from .. import draft as draftmod
from ..abilities import POSITION_LABELS
from ..draft import stage_of
from ..market import offer_terms
from .common import (
    BATS_LABELS,
    CONTRACT_GROUPS,
    KIND_LABELS,
    ORIGIN_LABELS,
    THROWS_LABELS,
    WAR_COLUMNS,
    WAR_KIND,
    _WAR_SORT_KEYS,
    column_info,
    is_sortable,
    metrics_config,
    table_cols,
)

DECISION_STAGES = ("fa", "draft", "market")
GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
SHOW_LABELS = {"fa": ("未契約だけ", "契約した選手も"), "draft": ("未指名だけ", "指名済みも"), "market": ("未契約だけ", "契約した選手も")}
THIN_BOTTOM = 9  # 手薄の印:一軍相当の見込みの WAR が、12 球団の中で下から 4 番目以内(自球団より強い球団が 9 つ以上。D-303)
THIN_SHORT = 2  # 手薄の印:人数が人数の目安より 2 人以上少ない(目安の合計が 70 人なので、1 人の差はどこかに必ず出る。D-303)

COL = {
    "age": {"key": "age", "label": "年齢", "description": "今の年齢", "type": "metric", "better": "low"},
    "origin": {"key": "origin", "label": "出身", "description": "高卒・大卒・社会人・独立リーグ", "type": "text", "better": "low"},
    "pos": {"key": "pos", "label": "ポジション", "description": "守備位置。◆は自球団の手薄なポジション", "type": "text", "better": "low"},
    "depth": {"key": "depth", "label": "加入後の序列", "description": "加入したら、自球団の同じポジションで何番手になるか(自球団の評価。志望の出場機会と同じ計算)。● は一軍の枠の目安に入る", "type": "count", "better": "low"},
    "calc": {"key": "calc", "label": "算定年俸", "description": "見込みの WAR から算定した年俸(万円。自球団の評価)", "type": "count", "better": "high"},
    "usage": {"key": "usage", "label": "出場", "description": "選んだシーズンの打席数(投手は投球回)", "type": "count", "better": "high"},
    "former": {"key": "former", "label": "前の所属", "description": "FA を宣言した球団・手放した球団(指名されなかった候補は「候補」)", "type": "text", "better": "low"},
    "status": {"key": "status", "label": "状態", "description": "未契約・提示中・契約(球団・年数・年俸)", "type": "text", "better": "low"},
    "overall": {"key": "overall", "label": "総合(推定)", "description": "自球団のスカウトの総合の推定値 ± ふれ幅(真の値が約 80% の確率で入る幅)", "type": "metric", "better": "high"},
    "ceiling": {"key": "ceiling", "label": "天井", "description": "潜在能力の見立て(S〜D。上位 5% が S)", "type": "metric", "better": "high"},
}
STAGE_COLUMNS = {
    "fa": ["age", "pos", "depth", "calc", "usage", "former", "status"],
    "draft": ["overall", "ceiling", "age", "origin", "pos", "depth"],
    "market": ["overall", "ceiling", "age", "pos", "depth", "calc", "usage", "former", "status"],
}
WAR_ALL = {"key": "war_all", "label": "WAR", "description": "野手は WAR、投手は WAR(失点版)", "type": "metric", "category": "war", "better": "high"}


class DecisionMixin:
    """Game の一部:判断の画面の表(FA・ドラフト・市場)と、加入後の序列・手薄なポジション・自球団の状況。"""

    def _depth(self, player, team, ctx) -> tuple[int, bool]:
        """加入後の序列(1 から)と、一軍の枠の目安に入るか(志望の出場機会の軸と同じ計算。D-250、D-297)。"""
        est = ctx.scout(player, team.id)[0]
        rank = sum(1 for q in team.players if q.id != player.id and q.position == player.position and ctx.scout(q, team.id)[0] > est)
        slots = (ctx.slots or {}).get(player.position, 1.0)
        return rank + 1, rank < slots

    def _thin_positions(self, ctx) -> dict[str, list[str]]:
        """自球団の手薄なポジション(D-303):人数が人数の目安より 2 人以上少ない、または一軍相当(一軍の枠の目安の人数)の見込みの WAR の合計が
        12 球団の中で下から 4 番目以内。見込みは自球団の評価(履歴のある選手は球団によらない)。戻り値はポジション → 理由の一覧。"""
        my = self.state.my_team_id
        team = self._team(my)
        target = {**self.state.gen_config["roster"]["pitchers"], **self.state.gen_config["roster"]["fielders"]}
        strength: dict[str, dict[str, float]] = {}
        for t in self.state.league.teams:
            for pos in target:
                k = max(1, math.ceil((ctx.slots or {}).get(pos, 1.0)))
                exp = sorted((ctx.expected(p, my)[0] for p in t.players if p.position == pos), reverse=True)[:k]
                strength.setdefault(pos, {})[t.id] = sum(exp)
        out: dict[str, list[str]] = {}
        for pos, n in target.items():
            reasons = []
            have = sum(1 for p in team.players if p.position == pos)
            if have <= int(n) - THIN_SHORT:
                reasons.append(f"人数 {have}(目安 {int(n)})")
            mine = strength[pos][my]
            if sum(1 for tid, v in strength[pos].items() if tid != my and v > mine) >= THIN_BOTTOM:
                reasons.append("一軍相当の見込みが下位")
            if reasons:
                out[pos] = reasons
        return out

    def team_outlook(self) -> dict:
        """自球団の状況のパネル(公開用。D-298):ポジション別の人数(目安・最低人数・手薄の印)、主な選手と WAR・年齢、空き枠、予算。"""
        proc = self._proc()
        my = self.state.my_team_id
        team = self._my_team()
        ctx = self._contract_ctx()
        view = self._season_view(None)
        target = {**self.state.gen_config["roster"]["pitchers"], **self.state.gen_config["roster"]["fielders"]}
        mins, _ = self._mins()
        thin = self._thin_positions(ctx)
        rows = []
        for pos, label in POSITION_LABELS.items():
            if pos not in target:
                continue
            players = [p for p in team.players if p.position == pos]
            ranked = []
            for p in players:
                line = view.war.get(p.id)
                war = None if line is None else float(line.war if p.role == "batter" else line.war_ra)
                ranked.append((war if war is not None else -99.0, p))
            ranked.sort(key=lambda x: (-x[0], x[1].id))
            top = [{"player_id": p.id, "name": p.name, "age": p.age, "war": None if w == -99.0 else round(w, 1), "war_text": "—" if w == -99.0 else f"{w:.1f}"} for w, p in ranked[:3]]
            rows.append({"position": pos, "label": label, "count": len(players), "target": int(target[pos]), "minimum": int(mins.get(pos, 0)), "thin": pos in thin, "thin_reasons": thin.get(pos, []), "top": top, "age_mean": round(sum(p.age for p in players) / len(players), 1) if players else None})
        pending = len(proc.fa_offers) if proc.phase == "fa" else len(proc.market_offers) if proc.phase == "market" else 0
        b = self.budget_info(my)
        return {"team_name": team.name, "players": len(team.players), "max": draftmod.MAX_ROSTER, "space": draftmod.MAX_ROSTER - len(team.players) - pending, "pending_offers": pending, "positions": rows, "budget": b, "season_label": view.label}

    def _decision_players(self, stage: str, show: str) -> list[tuple]:
        """表に出す選手:(選手, 今の所属の球団 ID または None)。"""
        proc = self._proc()
        league_players = {p.id: (p, t.id) for t in self.state.league.teams for p in t.players}
        if stage == "fa":
            out = [(p, None) for p in proc.fa_pool]
            if show == "all":
                out += [league_players[pid] for pid in proc.fa_info if pid in league_players]
            return out
        if stage == "draft":
            out = [(p, None) for p in proc.candidates]
            if show == "all":
                out += [league_players[x["player_id"]] for x in proc.picks if x["phase"] == "draft" and x["player_id"] in league_players]
            return out
        out = [(p, None) for p in proc.market]
        if show == "all":
            out += [league_players[x["player_id"]] for x in proc.picks if x["phase"] == "market" and x["player_id"] in league_players]
        return out

    def decision_table(self, stage: str, group: str = "all", kind: str = "war", sort: str | None = None, order: str | None = None, season: str | None = None, show: str = "open") -> dict:
        """FA・ドラフト・市場の表(公開用。D-296〜D-299)。列は判断に使う順。並べ替えは列のキー(見出しのタップ)。
        group は 全員・投手・捕手・内野手・外野手。全員は共通の列だけ、それ以外は kind(基本・セイバー・WAR)の指標も。"""
        proc = self._proc()
        if stage not in DECISION_STAGES or stage_of(proc.phase) != stage:
            raise ValueError("今はその段階ではありません")
        if group not in CONTRACT_GROUPS:
            raise ValueError(f"全員・投手・捕手・内野手・外野手のどれかを選んでください(値: {group!r})")
        if show not in ("open", "all"):
            raise ValueError(f"表示する選手の選び方が正しくありません(値: {show!r})")
        if season not in (None, "current") and season not in [x["key"] for x in self.roster_seasons()]:
            raise ValueError(f"このシーズンは選べません(値: {season!r})")
        state = self.state
        my = state.my_team_id
        team = self._my_team()
        ctx = self._contract_ctx()
        view = self._season_view(None if season in (None, "current") else season)
        config = metrics_config()
        role = None if group == "all" else ("pitcher" if group == "pitcher" else "batter")
        with_stats = stage != "draft"
        kind_cols: list[dict] = []
        metric = None
        if with_stats:
            if role is None:
                kind, metric = "war", WAR_ALL
            else:
                if kind == WAR_KIND:
                    kind_cols = list(WAR_COLUMNS[role])
                elif kind in KIND_LABELS:
                    kind_cols = table_cols(config, role, kind)
                else:
                    raise ValueError(f"基本・セイバー・WAR を選んでください(値: {kind!r})")
                kind_cols = [c for c in kind_cols if c["key"] not in ("PA", "OUTS", "plate_appearances", "innings")]
                metric = next(c for c in WAR_COLUMNS[role] if c["key"] == ("war" if role == "batter" else "war_ra"))
        base = [dict(COL[k]) for k in STAGE_COLUMNS[stage]]
        if stage == "draft" and show == "all":
            base.append({**COL["status"], "description": "未指名・指名(球団・巡)"})
        columns = ([metric] if metric else []) + base + [c for c in kind_cols if not metric or c["key"] != metric["key"]]
        keys = {c["key"]: c for c in columns}
        if not sort:
            sort = metric["key"] if metric else "overall"
            order = "desc"
        if sort in keys:
            info = keys[sort]
        elif with_stats and sort == "war_all":
            info = WAR_ALL
        elif role is not None and sort in _WAR_SORT_KEYS[role]:
            info = next(c for c in WAR_COLUMNS[role] if c["key"] == sort)
        elif role is not None and is_sortable(config, role, sort):
            info = column_info(config, role, sort)
        else:
            raise ValueError(f"並び順に使えない列です(値: {sort!r})")
        if order not in ("asc", "desc"):
            order = "desc" if info.get("better", "high") == "high" else "asc"
        names = self._team_names()
        thin = self._thin_positions(ctx)
        positions = list(POSITION_LABELS)
        former = {x["player_id"]: x["team_id"] for x in proc.released}
        former.update({pid: x["former_team"] for pid, x in proc.fa_info.items()})
        picks = {x["player_id"]: x for x in proc.picks if x["player_id"]}
        rows = []
        for p, holder in self._decision_players(stage, show):
            if role is not None and p.position not in CONTRACT_GROUPS[group][1]:
                continue
            cells = self._metric_cells(p, view, config) if with_stats else {}
            cells["age"] = (p.age, f"{p.age}歳")
            cells["pos"] = (positions.index(p.position), POSITION_LABELS[p.position] + (" ◆" if p.position in thin else ""))
            depth, in_slots = self._depth(p, team, ctx)
            cells["depth"] = (depth, f"{depth} 番手" + (" ●" if in_slots else ""))
            sc = self._report_public(p, my)
            cells["overall"] = (Fraction(str(sc["overall"])), sc["overall_text"])
            cells["ceiling"] = (GRADE_ORDER.get(sc["ceiling"], 0), sc["ceiling"])
            cells["origin"] = (p.origin or "", ORIGIN_LABELS.get(p.origin, "") if p.origin else "—")
            row = {"player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                   "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws), "thin": p.position in thin, "in_slots": in_slots,
                   "scouting": sc, "open": holder is None}
            if stage == "fa":
                info_fa = proc.fa_info[p.id]
                calc = int(info_fa["calc_salary"])
                o, st = self._fa_row_status(p.id, info_fa, names)
                cells["status"] = (o, st)
                cells["former"] = (names.get(info_fa["former_team"], ""), names.get(info_fa["former_team"], ""))
                row["offer"] = proc.fa_offers.get(p.id)
                row["deal"] = {"calc_salary": calc, "calc_text": f"{calc:,} 万円", "expected": round(float(info_fa["expected"]), 2), "former_team_name": names.get(info_fa["former_team"], ""), "status": info_fa["status"]}
            elif stage == "market":
                calc, years, exp = offer_terms(p, my, ctx)
                mine = proc.market_offers.get(p.id)
                if holder is not None:
                    x = picks.get(p.id, {})
                    cells["status"] = (3, f"契約({names.get(holder, '')}・{x.get('years', 1)} 年・{int(x.get('salary') or 0):,})")
                elif mine:
                    cells["status"] = (0, f"提示中({mine['years']} 年・{int(mine['salary']):,})")
                else:
                    cells["status"] = (1, "未契約")
                fid = former.get(p.id)
                cells["former"] = (names.get(fid, "") if fid else "~", names.get(fid, "") if fid else "候補")
                row["offer"] = mine
                row["deal"] = {"calc_salary": calc, "calc_text": f"{calc:,} 万円", "expected": round(exp, 2), "former_team_name": names.get(fid, "") if fid else "", "status": "signed" if holder else "open"}
            else:
                if holder is not None:
                    x = picks.get(p.id, {})
                    cells["status"] = (1, f"指名({names.get(holder, '')}・{x.get('round', '-')} 巡)")
                else:
                    cells["status"] = (0, "未指名")
            if stage != "draft":
                cells["calc"] = (row["deal"]["calc_salary"], f"{row['deal']['calc_salary']:,}")
            row["values"] = {c["key"]: (cells.get(c["key"]) or (None, "—"))[1] for c in columns}
            row["_sort"] = (cells.get(sort) or (None, ""))[0]
            rows.append(row)
        present = sorted((r for r in rows if r["_sort"] is not None), key=lambda r: r["player_id"])
        present.sort(key=lambda r: r["_sort"], reverse=order == "desc")
        missing = sorted((r for r in rows if r["_sort"] is None), key=lambda r: r["player_id"])
        rows = present + missing
        for r in rows:
            r.pop("_sort")
        open_label, all_label = SHOW_LABELS[stage]
        return {
            "stage": stage, "group": group, "groups": [{"key": k, "label": v[0]} for k, v in CONTRACT_GROUPS.items()],
            "kind": kind if with_stats else None, "kinds": [] if role is None or not with_stats else [{"key": k, "label": v} for k, v in KIND_LABELS.items()] + [{"key": WAR_KIND, "label": "WAR"}],
            "show": show, "shows": [{"key": "open", "label": open_label}, {"key": "all", "label": all_label}],
            "columns": columns, "sort": info, "order": order, "rows": rows, "thin": {pos: {"label": POSITION_LABELS[pos], "reasons": r} for pos, r in thin.items()},
            "season": view.key, "season_label": view.label, "seasons": self.roster_seasons() if with_stats else [],
        }
