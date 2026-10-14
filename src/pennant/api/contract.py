"""画面から呼ぶ操作の関数のうち、契約(資金・契約更改の結果・契約の画面の表・自由契約)。作り直し②で更改は査定の自動に(D-353〜D-361)。"""

from __future__ import annotations

from fractions import Fraction

from .. import draft as draftmod, fa as famod
from ..abilities import POSITION_LABELS
from ..contracts import TIER_LABELS, base_funds, remaining_years, team_salary
from ..draft import stage_of
from ..metrics import innings_text
from .common import (
    BATS_LABELS,
    CONTRACT_COLUMNS,
    CONTRACT_GROUPS,
    CONTRACT_REASONS,
    CONTRACT_STATUS_LABELS,
    KIND_LABELS,
    ROSTER_BASE_COLUMNS,
    THROWS_LABELS,
    WAR_COLUMNS,
    WAR_KIND,
    _WAR_SORT_KEYS,
    _values,
    _war_values,
    column_info,
    is_sortable,
    metrics_config,
    table_cols,
)


def _change_text(old: int | None, new: int) -> str:
    if old is None:
        return "新規"
    d = int(new) - int(old)
    return "±0" if d == 0 else f"{d:+,}"


class ContractMixin:
    """Game の一部:契約(資金・契約更改の結果・契約の画面の表・自由契約)。"""

    def budget_info(self, team_id: str) -> dict:
        """球団の総年俸・資金(年俸の合計の上限)・規模・使用率(公開情報。D-353)。"""
        state = self.state
        team = self._team(team_id)
        total = team_salary(team)
        tier = state.budget_tiers.get(team_id)
        cap = state.team_funds.get(team_id)
        cap = None if cap is None else int(cap)
        return {
            "team_id": team_id, "total": total, "total_text": f"{total:,} 万円",
            "budget": base_funds(tier, state.contract_settings) if tier else None, "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "tier": tier, "tier_label": TIER_LABELS.get(tier) if tier else None,
            "usage": None if not cap else round(100.0 * total / cap, 1), "over": None if cap is None else max(0, total - cap), "hard": cap is not None,
            "rate": state.contract_rates.get(str(state.year)) or (max(state.contract_rates.values()) if state.contract_rates else None),
        }

    def _contract_public(self, p, year: int | None = None) -> dict | None:
        c = p.contract
        if not c:
            return None
        y = self.state.year if year is None else year
        need = int(famod.fa_settings(self.state.negotiation_settings)["seasons_required"])
        seasons = famod.seasons_of(p)
        return {"fa_seasons": seasons, "fa_required": need, "fa_holder": seasons >= need, "fa_text": f"FA 権あり(一軍 {seasons} シーズン)" if seasons >= need else f"一軍 {seasons} シーズン(FA 権まであと {need - seasons})", "salary": int(c["salary"]), "salary_text": f"{int(c['salary']):,} 万円", "until": int(c["until"]), "remaining": remaining_years(c, y), "history": [{"year": h["year"], "salary": h["salary"], "salary_text": f"{int(h['salary']):,} 万円", "years": h["years"], "reason": h["reason"], "reason_label": CONTRACT_REASONS.get(h["reason"], h["reason"]), "offers": h.get("offers")} for h in c.get("history", [])]}

    def _procedure_contracts(self, proc) -> dict:
        """手続きの画面に出す契約の情報:自球団の総年俸と資金、更改の結果、資金の超過で自由契約になった選手、各球団の資金。"""
        state = self.state
        names = self._team_names()
        my = state.my_team_id
        ctx = draftmod.state_context(state, proc)
        mine = None
        if my is not None:
            info = self.budget_info(my)
            info["over_now"] = draftmod.over_cap(self._team(my), ctx)
            info["blocked"] = ctx.hard() and info["over_now"] > 0
            mine = info
        renew = lambda x: {**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my, "old_text": None if x.get("old_salary") is None else f"{x['old_salary']:,} 万円", "salary_text": f"{x['salary']:,} 万円", "change": None if x.get("old_salary") is None else x["salary"] - x["old_salary"]}
        return {
            "hard": ctx.hard(),
            "rate": proc.rate, "rate_text": f"{proc.rate / 10000:.2f} 億円 / WAR" if proc.rate else "-",
            "mine": mine,
            "renewals": [renew(x) for x in proc.renewals],
            "budget_releases": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my, "salary_text": f"{x['salary']:,} 万円"} for x in proc.budget_releases],
            "teams": [{"team_id": t.id, "team_name": t.name, "is_mine": t.id == my, **{k: v for k, v in self.budget_info(t.id).items() if k in ("total", "total_text", "cap", "cap_text", "usage", "tier_label")}} for t in state.league.teams],
            "note": "契約が満了した選手は、成績(出番が少なければ能力も)から査定した年俸で自動的に更改しました(1 年で下がるのは 25% まで)。FA 権を持つ選手の一部は FA を宣言しました。総年俸が資金を超える球団は、見込みの WAR あたりの年俸が高い選手から自由契約にして超過を解消します(あなたの球団は契約の画面で自分で選びます)。",
        }

    def _contract_ctx(self):
        """進行中の手続きの契約の文脈(評価は手続きのシードと区切りで引く)。"""
        return draftmod.state_context(self.state, self.state.procedure)

    def _renewal_status(self, e) -> tuple[int, str]:
        """更改の状態の (並び順, 状態の文)。"""
        if e["status"] == "accepted":
            return 2, f"更改済({int(e.get('ai_years', 1))} 年・{int(e['auto_salary']):,})"
        if e["status"] == "declared":
            return 3, "FA 宣言"
        return 3, "自由契約"

    def _renewal_public(self, e) -> dict:
        order, status = self._renewal_status(e)
        old = e["old_salary"]
        new = int(e["auto_salary"])
        return {
            "player_id": e["player_id"], "name": e["name"], "role": e["role"], "position": e["position"], "position_label": POSITION_LABELS.get(e["position"], ""), "age": e["age"],
            "old_salary": old, "old_text": "-" if old is None else f"{int(old):,} 万円",
            "new_salary": new, "new_text": f"{new:,} 万円", "years": int(e.get("ai_years", 1)), "change": None if old is None else new - int(old), "change_text": _change_text(old, new),
            "expected": e["expected"], "status": e["status"], "status_label": status,
        }

    def _renewal_info(self, proc) -> dict:
        """契約の段階の、自球団の情報(公開用)。"""
        state = self.state
        my = state.my_team_id
        ctx = self._contract_ctx()
        entries = [e for e in proc.negotiations.values() if e["team_id"] == my]
        counts = {"accepted": 0, "released": 0, "declared": 0}
        for e in entries:
            counts[e["status"] if e["status"] in counts else "released"] += 1
        team = self._team(my)
        cap = ctx.cap(my)
        status = {pid: st for pid, (st, _, _) in self._contract_statuses(proc).items()}
        roster_counts = {k: sum(1 for v in status.values() if v == k) for k in CONTRACT_STATUS_LABELS}
        over = draftmod.over_cap(team, ctx) if ctx.hard() else 0
        total = team_salary(team)
        return {
            "counts": counts, "total": len(entries), "fa_required": int(famod.fa_settings(state.negotiation_settings)["seasons_required"]),
            "roster_counts": roster_counts, "status_labels": dict(CONTRACT_STATUS_LABELS), "players": len(team.players), "over": over, "over_text": f"{over:,} 万円",
            "can_next": over <= 0,
            "team_total": total, "team_total_text": f"{total:,} 万円", "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "declared": [self._renewal_public(e) for e in entries if e["status"] == "declared"],
        }

    def _renewal_table(self, role: str, kind: str, sort: str | None, order: str | None, season: str | None) -> dict:
        proc = self._proc()
        usage = ROSTER_BASE_COLUMNS[role][2]
        cols = [
            ROSTER_BASE_COLUMNS[role][0], ROSTER_BASE_COLUMNS[role][1], usage,
            {"key": "salary", "label": "前の年俸", "type": "count", "better": "high"},
            {"key": "new", "label": "新しい年俸", "type": "count", "better": "high"},
            {"key": "change", "label": "増減", "type": "count", "better": "high"},
            {"key": "status", "label": "状態", "type": "text", "better": "low"},
            {"key": "fa", "label": "FA 権", "type": "count", "better": "high"},
        ]
        extra = {}
        players = {p.id: p for p in self._contract_players(proc)}
        for e in proc.negotiations.values():
            if e["team_id"] != self.state.my_team_id or e["player_id"] not in players:
                continue
            o, status = self._renewal_status(e)
            old = e["old_salary"]
            new = int(e["auto_salary"])
            seasons = famod.seasons_of(players[e["player_id"]])
            extra[e["player_id"]] = {"salary": (None, "—") if old is None else (int(old), f"{int(old):,}"), "new": (new, f"{new:,}"), "change": (None if old is None else new - int(old), _change_text(old, new)), "status": (o, status), "fa": (seasons, f"あり({seasons})" if e["context"].get("fa_holder") else str(seasons))}
        if not sort:
            sort, order = "status", "asc"
        table = self.roster_table(self.offseason_players("renewal"), role, kind, sort, order, season, base_columns=cols, extra_values=extra)
        pub = {e["player_id"]: self._renewal_public(e) for e in proc.negotiations.values() if e["team_id"] == self.state.my_team_id}
        for r in table["rows"]:
            r["renewal"] = pub.get(r["player_id"])
        table["phase"] = "renewal"
        return table

    def _over_hard_cap(self, team) -> bool:
        ctx = self._contract_ctx()
        return ctx.hard() and draftmod.over_cap(team, ctx) > 0

    def _contract_players(self, proc) -> list:
        """契約の画面の選手:自球団の今の選手と、この手続きで自球団を離れた選手(自由契約・FA 宣言)。"""
        my = self.state.my_team_id
        team = self._my_team()
        gone = {x["player_id"] for x in proc.released if x["team_id"] == my}
        gone |= {pid for pid, x in proc.fa_info.items() if x["former_team"] == my}
        out = list(team.players)
        seen = {p.id for p in out}
        for p in list(proc.market) + list(proc.fa_pool):
            if p.id in gone and p.id not in seen:
                out.append(p)
                seen.add(p.id)
        return out

    def _contract_statuses(self, proc) -> dict:
        """選手 ID → (状態のキー, 並び順, 表示)。"""
        my = self.state.my_team_id
        team_ids = {p.id for p in self._my_team().players}
        released = {x["player_id"] for x in proc.released if x["team_id"] == my}
        out = {}
        for p in self._contract_players(proc):
            e = proc.negotiations.get(p.id)
            if p.id in released and p.id not in team_ids:
                out[p.id] = ("released", 5, "自由契約")  # 更改済のあとで自由契約にした選手も「自由契約」(更改の記録は更改済のまま)
            elif e is not None and e["team_id"] == my and e["status"] == "declared":
                out[p.id] = ("declared", 4, "FA 宣言")
            elif e is not None and e["team_id"] == my and e["status"] == "accepted":
                out[p.id] = ("accepted", 3, "更改済")
            elif p.id in team_ids:
                out[p.id] = ("contracted", 2, "契約中")
            elif p.id in released:
                out[p.id] = ("released", 5, "自由契約")
            else:
                out[p.id] = ("declared", 4, "FA 宣言")
        return out

    def _metric_cells(self, p, view, config) -> dict:
        """選手の成績の値(キー → (並べ替え用の数, 表示))。出場(usage)と WAR を含む。"""
        role = p.role
        group = view.records.batters if role == "batter" else view.records.pitchers
        counts = group.get(p.id)
        cells = dict(_values(config, role, counts, view.baselines, view.park_factor(p.id) if role == "batter" else None, (view.override or {}).get(p.id))) if counts is not None else {}
        line = view.war.get(p.id)
        if line is not None:
            cells.update(_war_values(line))
            cells["war_all"] = cells["war"] if role == "batter" else cells["war_ra"]
        if counts is None:
            cells["usage"] = (None, "—")
        elif role == "batter":
            cells["usage"] = (Fraction(int(counts.get("PA", 0))), f"{int(counts.get('PA', 0))} 打席")
        else:
            cells["usage"] = (Fraction(int(counts.get("OUTS", 0))), f"{innings_text(int(counts.get('OUTS', 0)))} 回")
        return cells

    def contract_table(self, group: str = "all", kind: str = "war", sort: str | None = None, order: str | None = None, season: str | None = None, status: str = "all") -> dict:
        """契約の画面の表(公開用。D-272、D-358):自球団の全選手。列は 名前・選んだ指標(既定は WAR)・状態・新しい年俸・増減・ポジション・年齢・今の契約・出場
        (投手・捕手・内野手・外野手を選んだときは、選んだ種類の成績の列も)。初期は WAR の低い順。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        if group not in CONTRACT_GROUPS:
            raise ValueError(f"全員・投手・捕手・内野手・外野手のどれかを選んでください(値: {group!r})")
        if status not in ("all", *CONTRACT_STATUS_LABELS):
            raise ValueError(f"状態の絞り込みが正しくありません(値: {status!r})")
        if season not in (None, "current") and season not in [x["key"] for x in self.roster_seasons()]:
            raise ValueError(f"このシーズンは選べません(値: {season!r})")
        view = self._season_view(None if season in (None, "current") else season)
        config = metrics_config()
        my = self.state.my_team_id
        role = None if group == "all" else ("pitcher" if group == "pitcher" else "batter")
        war_col = {"key": "war_all", "label": "WAR", "type": "metric", "category": "war", "better": "high"}
        if role is None:
            kind = "war"
            kind_cols: list[dict] = []
            default_metric = war_col
        else:
            if kind == WAR_KIND:
                kind_cols = list(WAR_COLUMNS[role])
            elif kind in KIND_LABELS:
                kind_cols = table_cols(config, role, kind)
            else:
                raise ValueError(f"基本・セイバー・WAR を選んでください(値: {kind!r})")
            kind_cols = [c for c in kind_cols if c["key"] not in ("PA", "OUTS", "plate_appearances", "innings")]
            default_metric = next(c for c in WAR_COLUMNS[role] if c["key"] == ("war" if role == "batter" else "war_ra"))
        base = {c["key"]: c for c in CONTRACT_COLUMNS}
        if not sort:
            sort, order = default_metric["key"], order or "asc"
        if sort in base:
            metric = default_metric
            info = base[sort]
        elif sort == "war_all" or (role is not None and sort in [c["key"] for c in kind_cols]):
            metric = war_col if sort == "war_all" else next(c for c in kind_cols if c["key"] == sort)
            info = metric
        elif role is not None and sort in _WAR_SORT_KEYS[role]:
            metric = info = next(c for c in WAR_COLUMNS[role] if c["key"] == sort)
        elif role is not None and is_sortable(config, role, sort):
            metric = info = column_info(config, role, sort)
        else:
            raise ValueError(f"並び順に使えない列です(値: {sort!r})")
        if order not in ("asc", "desc"):
            order = "desc" if info.get("better", "high") == "high" else "asc"
        columns = [metric] + CONTRACT_COLUMNS + [c for c in kind_cols if c["key"] != metric["key"]]
        statuses = self._contract_statuses(proc)
        team = self._my_team()
        mins, min_batters = self._mins()
        ok = draftmod.releasable(team.players, mins, min_batters)
        over = self._over_hard_cap(team)
        in_team = {p.id for p in team.players}
        next_year = proc.year + 1
        positions = list(POSITION_LABELS)
        rows = []
        for p in self._contract_players(proc):
            if role is not None and p.position not in CONTRACT_GROUPS[group][1]:
                continue
            st, st_order, st_label = statuses[p.id]
            if status != "all" and st != status:
                continue
            cells = self._metric_cells(p, view, config)
            e = proc.negotiations.get(p.id) if p.id in proc.negotiations and proc.negotiations[p.id]["team_id"] == my else None
            if e is not None:
                old = e["old_salary"]
                new = int(e["auto_salary"])
                cells["contract"] = (old, "—" if old is None else f"{int(old):,}(満了)")
                if e["status"] == "accepted" and p.id in in_team:
                    cells["new"] = (new, f"{new:,}・{int(e.get('ai_years', 1))} 年")
                    cells["change"] = (None, "新規") if old is None else (new - int(old), _change_text(old, new))
                else:
                    cells["new"] = (None, "—")
                    cells["change"] = (None, "—")
            elif p.contract and p.id in in_team:
                n = remaining_years(p.contract, next_year)
                cells["contract"] = (int(p.contract["salary"]), f"{int(p.contract['salary']):,}(残り {n} 年)")
                cells["new"] = (int(p.contract["salary"]), f"{int(p.contract['salary']):,}(契約中)")
                cells["change"] = (0, "±0")
            else:
                cells["contract"] = (None, "—")
                cells["new"] = (None, "—")
                cells["change"] = (None, "—")
            cells["pos"] = (positions.index(p.position), POSITION_LABELS[p.position])
            cells["age"] = (p.age, f"{p.age}歳")
            cells["status"] = (st_order, st_label)
            keys = [c["key"] for c in columns]
            values = {k: (cells.get(k) or (None, "—"))[1] for k in keys}
            rows.append({
                "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
                "values": values, "status": st, "in_team": p.id in in_team,
                "can_release": p.id in in_team and (p.id in ok or over),
                "renewal": self._renewal_public(e) if e is not None else None,
                "contract": self._contract_public(p, next_year) if p.id in in_team and p.contract else None,
                "_sort": (cells.get(sort) or (None, ""))[0],
            })
        present = sorted((r for r in rows if r["_sort"] is not None), key=lambda r: r["player_id"])
        present.sort(key=lambda r: r["_sort"], reverse=order == "desc")
        missing = sorted((r for r in rows if r["_sort"] is None), key=lambda r: r["player_id"])
        rows = present + missing
        for r in rows:
            r.pop("_sort")
        return {
            "phase": "contract", "group": group, "groups": [{"key": k, "label": v[0]} for k, v in CONTRACT_GROUPS.items()],
            "kind": kind, "kinds": [] if role is None else [{"key": k, "label": v} for k, v in KIND_LABELS.items()] + [{"key": WAR_KIND, "label": "WAR"}],
            "status": status, "statuses": [{"key": "all", "label": "全部"}] + [{"key": k, "label": v} for k, v in CONTRACT_STATUS_LABELS.items()],
            "columns": columns, "sort": info, "order": order, "extra_column": None, "rows": rows,
            "season": view.key, "season_label": view.label, "seasons": self.roster_seasons(),
        }

    def offseason_contract_release(self, player_id: str) -> dict:
        """契約の画面の「自由契約にする」(D-272):契約が残る選手(更改済・複数年の途中)の残りの契約を消して、市場へ。
        最低人数を割る選手は外せない(資金を超えている間は外せる。D-255、D-361)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        return self.offseason_release([str(player_id)])
