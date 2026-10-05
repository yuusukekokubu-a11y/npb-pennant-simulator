"""画面から呼ぶ操作の関数のうち、契約(予算・契約更改・契約の画面の表・自由契約)。"""

from __future__ import annotations

from fractions import Fraction

from .. import draft as draftmod, fa as famod
from ..abilities import POSITION_LABELS
from ..contracts import (
    RULE_LABELS,
    RULE_NOTES,
    TIER_LABELS,
    budget_of,
    cap_of,
    is_hard,
    remaining_years,
    team_salary,
)
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


class ContractMixin:
    """Game の一部:契約(予算・契約更改・契約の画面の表・自由契約)。"""

    def budget_info(self, team_id: str) -> dict:
        """球団の総年俸・予算・上限・使用率(公開情報。なしのときは総年俸だけ)。"""
        state = self.state
        team = self._team(team_id)
        total = team_salary(team)
        tier = state.budget_tiers.get(team_id)
        budget = budget_of(state.money_rule, tier, state.contract_settings)
        cap = cap_of(state.money_rule, tier, state.contract_settings)
        return {
            "team_id": team_id, "rule": state.money_rule, "rule_label": RULE_LABELS[state.money_rule], "total": total, "total_text": f"{total:,} 万円",
            "budget": budget, "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "tier": tier, "tier_label": TIER_LABELS.get(tier) if tier else None,
            "usage": None if cap is None else round(100.0 * total / cap, 1), "over": None if cap is None else max(0, total - cap), "hard": is_hard(state.money_rule),
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
        """手続きの画面に出す契約の情報:ルール、自球団の総年俸と予算、更改の結果、予算超過で自由契約になった選手。"""
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
            "rule": state.money_rule, "rule_label": RULE_LABELS[state.money_rule], "rule_note": RULE_NOTES[state.money_rule], "hard": ctx.hard(),
            "rate": proc.rate, "rate_text": f"{proc.rate / 10000:.2f} 億円 / WAR" if proc.rate else "-",
            "mine": mine,
            "renewals": [renew(x) for x in proc.renewals],
            "budget_releases": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my, "salary_text": f"{x['salary']:,} 万円"} for x in proc.budget_releases],
            "negotiation_releases": [{"player_id": e["player_id"], "name": e["name"], "team_id": e["team_id"], "team_name": names.get(e["team_id"], ""), "position_label": POSITION_LABELS.get(e["position"], ""), "age": e["age"], "is_mine": e["team_id"] == my, "offers": len(e["offers"]), "reason": self._renewal_status({**e, "status": "pending"})[2] if e["offers"] else ""} for e in proc.negotiations.values() if e["status"] == "released"],
            "teams": [{"team_id": t.id, "team_name": t.name, "is_mine": t.id == my, **{k: v for k, v in self.budget_info(t.id).items() if k in ("total", "total_text", "cap", "cap_text", "usage", "tier_label")}} for t in state.league.teams],
            "note": "契約が満了した選手に、見込みの WAR(直近 3 シーズンの加重平均。履歴がなければスカウト評価)から算定した年俸で提示し、選手が志望で受けるか断るかを決めました(AI 球団は、断られたら見込みの高い選手にだけ条件を上げて再提示し、ほかは自由契約)。" + ("標準以上では、予算の上限を超える球団は、見込みの WAR あたりの年俸が高い選手から自由契約にして超過を解消します(あなたの球団は自由契約の画面で自分で選びます)。" if ctx.hard() else ""),
        }

    def _contract_ctx(self):
        """進行中の手続きの契約の文脈(評価は手続きのシードと区切りで引く)。"""
        return draftmod.state_context(self.state, self.state.procedure)

    def _renewal_status(self, e) -> tuple[int, str, str]:
        """交渉の状態の (並び順, 状態の文, 理由の文)。理由は最後に断られた理由(公開。D-245)。"""
        neg = self.state.negotiation_settings
        if e["status"] == "accepted":
            last = e["offers"][-1] if e["offers"] else {"years": e.get("ai_years", 1), "salary": e["auto_salary"]}
            return 2, f"更改済({last['years']} 年・{int(last['salary']):,})", ""
        if e["status"] == "released":
            return 3, "自由契約", ""
        if e["status"] == "declared":
            reason = e["offers"][-1].get("reason") if e["offers"] else None
            return 3, "FA 宣言", neg.reason(reason) if reason in neg.axes else ""
        if not e["offers"]:
            return 1, "未提示", ""
        left = draftmod.offers_left(e, neg)
        reason = e["offers"][-1].get("reason")
        return 0, f"断られた(残り {left} 回)", neg.reason(reason) if reason in neg.axes else "条件が合わない"

    def _renewal_public(self, e) -> dict:
        neg = self.state.negotiation_settings
        order, status, reason = self._renewal_status(e)
        return {
            "player_id": e["player_id"], "name": e["name"], "role": e["role"], "position": e["position"], "position_label": POSITION_LABELS.get(e["position"], ""), "age": e["age"],
            "old_salary": e["old_salary"], "old_text": "-" if e["old_salary"] is None else f"{int(e['old_salary']):,} 万円",
            "auto_salary": int(e["auto_salary"]), "auto_text": f"{int(e['auto_salary']):,} 万円", "expected": e["expected"],
            "status": e["status"] if e["status"] != "pending" else ("refused" if e["offers"] else "pending"), "status_label": status, "reason": reason,
            "offers": [{"years": o["years"], "salary": o["salary"], "salary_text": f"{int(o['salary']):,} 万円", "accepted": o["accepted"], "reason": "" if o["accepted"] else (neg.reason(o["reason"]) if o.get("reason") in neg.axes else "条件が合わない")} for o in e["offers"]],
            "offers_left": draftmod.offers_left(e, neg), "max_offers": neg.max_offers,
        }

    def _renewal_info(self, proc) -> dict:
        """契約更改の段階の、自球団の情報(公開用)。"""
        state = self.state
        my = state.my_team_id
        ctx = self._contract_ctx()
        entries = [e for e in proc.negotiations.values() if e["team_id"] == my]
        counts = {"pending": 0, "refused": 0, "accepted": 0, "released": 0, "declared": 0}
        for e in entries:
            counts[e["status"] if e["status"] in ("accepted", "released", "declared") else "refused" if e["offers"] else "pending"] += 1
        team = self._team(my)
        cap = ctx.cap(my)
        status = {pid: st for pid, (st, _, _) in self._contract_statuses(proc).items()}
        roster_counts = {k: sum(1 for v in status.values() if v == k) for k in CONTRACT_STATUS_LABELS}
        over = draftmod.over_cap(team, ctx) if ctx.hard() else 0
        open_n = counts["pending"] + counts["refused"]
        return {
            "counts": counts, "total": len(entries), "open": open_n, "fa_required": int(famod.fa_settings(state.negotiation_settings)["seasons_required"]),
            "unoffered": counts["pending"],
            "roster_counts": roster_counts, "status_labels": dict(CONTRACT_STATUS_LABELS), "players": len(team.players), "over": over, "over_text": f"{over:,} 万円",
            "can_next": open_n == 0 and over <= 0,
            "none_max_ratio": float(state.negotiation_settings.money_none["max_ratio"]) if state.money_rule == "none" else None,
            "salary_editable": True, "max_years": state.contract_settings.max_years, "min_years": state.contract_settings.min_years,
            "minimum_salary": state.contract_settings.minimum, "rounding": state.contract_settings.rounding, "max_offers": state.negotiation_settings.max_offers,
            "projected_total": draftmod.projected_total(team, proc), "projected_text": f"{draftmod.projected_total(team, proc):,} 万円", "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "released": [self._renewal_public(e) for e in entries if e["status"] == "released"],
            "declared": [self._renewal_public(e) for e in entries if e["status"] == "declared"],
            "axes": [{"key": k, "label": state.negotiation_settings.label(k), "reason": state.negotiation_settings.reason(k)} for k in state.negotiation_settings.axes],
        }

    def _renewal_table(self, role: str, kind: str, sort: str | None, order: str | None, season: str | None) -> dict:
        proc = self._proc()
        usage = ROSTER_BASE_COLUMNS[role][2]
        cols = [
            ROSTER_BASE_COLUMNS[role][0], ROSTER_BASE_COLUMNS[role][1], usage,
            {"key": "salary", "label": "現在の年俸", "type": "count", "better": "high"},
            {"key": "auto", "label": "自動案の年俸", "type": "count", "better": "high"},
            {"key": "status", "label": "状態", "type": "text", "better": "low"},
            {"key": "reason", "label": "理由", "type": "text", "better": "low"},
            {"key": "fa", "label": "FA 権", "type": "count", "better": "high"},
        ]
        extra = {}
        for e in proc.negotiations.values():
            if e["team_id"] != self.state.my_team_id or e["status"] == "released":
                continue
            o, status, reason = self._renewal_status(e)
            old = e["old_salary"]
            pl = next((q for q in self._my_team().players if q.id == e["player_id"]), None)
            seasons = famod.seasons_of(pl) if pl is not None else 0
            extra[e["player_id"]] = {"salary": (None, "—") if old is None else (int(old), f"{int(old):,}"), "auto": (int(e["auto_salary"]), f"{int(e['auto_salary']):,}"), "status": (o, status), "reason": (reason or "~", reason or ""), "fa": (seasons, f"あり({seasons})" if e["context"].get("fa_holder") else str(seasons))}
        if not sort:
            sort, order = "status", "asc"
        table = self.roster_table(self.offseason_players("renewal"), role, kind, sort, order, season, base_columns=cols, extra_values=extra)
        pub = {e["player_id"]: self._renewal_public(e) for e in proc.negotiations.values() if e["team_id"] == self.state.my_team_id}
        for r in table["rows"]:
            r["renewal"] = pub.get(r["player_id"])
        table["phase"] = "renewal"
        return table

    def _renewal_entry(self, player_id: str):
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        e = proc.negotiations.get(str(player_id))
        if e is None or e["team_id"] != team.id:
            raise ValueError("その選手は、自球団の更改の対象ではありません")
        if e["status"] != "pending":
            raise ValueError("その選手の更改は、もう決まっています")
        return proc, team, e

    def offseason_renew_auto(self) -> dict:
        """「自動案でまとめて更改」:未提示の全員に自動案(1 年・算定した年俸)を提示する(D-244)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        ctx = self._contract_ctx()
        default_years = self.state.contract_settings.default_years
        for e in [e for e in draftmod.open_entries(proc, team.id) if not e["offers"]]:
            draftmod.make_offer(team, e, default_years, int(e["auto_salary"]), proc, ctx)
        self.dirty = True
        return self.offseason_view()

    def offseason_offer(self, player_id: str, years: int, salary: int | None = None) -> dict:
        """個別の提示(年数 1〜5、年俸はゆるい以上で変えられる。D-244、D-246、D-252)。戻り値は画面の情報と、この提示の答え。"""
        state = self.state
        proc, team, e = self._renewal_entry(player_id)
        cs = state.contract_settings
        try:
            years = int(years)
        except (TypeError, ValueError):
            raise ValueError("年数は整数で入れてください") from None
        if not cs.min_years <= years <= cs.max_years:
            raise ValueError(f"年数は {cs.min_years}〜{cs.max_years} 年にしてください(値: {years})")
        auto = int(e["auto_salary"])
        if salary is None or salary == "":
            salary = auto
        elif state.money_rule == "none":
            salary = self._none_salary(salary, auto)
        else:
            try:
                salary = int(salary)
            except (TypeError, ValueError):
                raise ValueError("年俸は整数(万円)で入れてください") from None
            if salary < cs.minimum:
                raise ValueError(f"年俸は最低年俸({cs.minimum:,} 万円)以上にしてください")
            if salary > cs.base_budget:
                raise ValueError(f"年俸が大きすぎます({cs.base_budget:,} 万円まで)")
            ctx = self._contract_ctx()
            cap = ctx.cap(team.id)
            if ctx.hard() and salary > auto and cap is not None and draftmod.projected_total(team, proc) - auto + salary > cap:
                room = cap - (draftmod.projected_total(team, proc) - auto)
                raise ValueError(f"この年俸では、見込みの総年俸が予算の上限を超えます(この選手に出せるのは {max(auto, room):,} 万円まで)")
        if draftmod.offers_left(e, state.negotiation_settings) <= 0:
            raise ValueError("提示の回数を使い切りました")
        rec = draftmod.make_offer(team, e, years, int(salary), proc, self._contract_ctx())
        self.dirty = True
        view = self.offseason_view()
        view["last_offer"] = {"player_id": e["player_id"], "name": e["name"], "accepted": rec["accepted"], "years": rec["years"], "salary": rec["salary"], "reason": "" if rec["accepted"] else state.negotiation_settings.reason(rec["reason"]) if rec.get("reason") in state.negotiation_settings.axes else "条件が合わない", "released": e["status"] == "released"}
        return view

    def _none_salary(self, salary, calc: int) -> int:
        """お金のルール「なし」の提示の年俸:算定の 1.0〜1.3 倍(設定値)。D-273。"""
        try:
            salary = int(salary)
        except (TypeError, ValueError):
            raise ValueError("年俸は整数(万円)で入れてください") from None
        ratio = float(self.state.negotiation_settings.money_none["max_ratio"])
        top = int(calc * ratio)
        if salary < calc:
            raise ValueError(f"お金のルール「なし」では、算定({calc:,} 万円)より低い年俸は出せません")
        if salary > top:
            raise ValueError(f"お金のルール「なし」では、年俸は算定の {ratio:g} 倍({top:,} 万円)までです")
        return salary

    def offseason_renew_release(self, player_id: str) -> dict:
        """交渉をやめて自由契約にする(市場へ)。"""
        proc, team, e = self._renewal_entry(player_id)
        draftmod.release_entry(team, e, proc)
        self.dirty = True
        return self.offseason_view()

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
        neg = self.state.negotiation_settings
        team_ids = {p.id for p in self._my_team().players}
        released = {x["player_id"] for x in proc.released if x["team_id"] == my}
        out = {}
        for p in self._contract_players(proc):
            e = proc.negotiations.get(p.id)
            if e is not None and e["team_id"] == my:
                if e["status"] == "accepted":
                    out[p.id] = ("accepted", 3, "更改済")
                elif e["status"] == "declared":
                    out[p.id] = ("declared", 4, "FA 宣言")
                elif e["status"] == "released":
                    out[p.id] = ("released", 5, "自由契約")
                elif e["offers"]:
                    out[p.id] = ("refused", 0, f"保留(残り {draftmod.offers_left(e, neg)} 回)")
                else:
                    out[p.id] = ("unoffered", 1, "未提示")
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
        """契約の画面の表(公開用。D-272):自球団の全選手。列は 名前・選んだ指標(既定は WAR)・状態・今回の提示・ポジション・年齢・今の契約・出場(D-284)
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
        neg = self.state.negotiation_settings
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
                cells["contract"] = (old, "—" if old is None else f"{int(old):,}(満了)")
                if e["status"] == "accepted" or e["offers"]:
                    last = e["offers"][-1] if e["offers"] else {"years": e.get("ai_years", 1), "salary": e["auto_salary"]}
                    cells["offer"] = (int(last["salary"]), f"{int(last['salary']):,}・{last['years']} 年")
                else:
                    cells["offer"] = (int(e["auto_salary"]), f"{int(e['auto_salary']):,}・{self.state.contract_settings.default_years} 年(案)")
            elif p.contract and p.id in in_team:
                n = remaining_years(p.contract, next_year)
                cells["contract"] = (int(p.contract["salary"]), f"{int(p.contract['salary']):,}(残り {n} 年)")
                cells["offer"] = (None, "—")
            else:
                cells["contract"] = (None, "—")
                cells["offer"] = (None, "—")
            cells["pos"] = (positions.index(p.position), POSITION_LABELS[p.position])
            cells["age"] = (p.age, f"{p.age}歳")
            cells["status"] = (st_order, st_label)
            keys = [c["key"] for c in columns]
            values = {k: (cells.get(k) or (None, "—"))[1] for k in keys}
            pending = e is not None and e["status"] == "pending"
            rows.append({
                "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
                "values": values, "status": st, "in_team": p.id in in_team,
                "can_release": pending or (p.id in in_team and (p.id in ok or over)),
                "can_offer": pending and draftmod.offers_left(e, neg) > 0,
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
        """契約の画面の「自由契約にする」(D-272):交渉中の選手は交渉をやめて、契約が残る選手(複数年の途中・更改済)は残りの契約を消して、市場へ。
        最低人数を割る選手は外せない(標準以上で上限を超えている間は外せる。D-255)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        pid = str(player_id)
        e = proc.negotiations.get(pid)
        if e is not None and e["team_id"] == team.id and e["status"] == "pending":
            draftmod.release_entry(team, e, proc)
            self.dirty = True
            return self.offseason_view()
        return self.offseason_release([pid])
