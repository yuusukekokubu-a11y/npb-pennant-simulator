"""画面から呼ぶ操作の関数のうち、自由契約市場の提示(FA と同じ提示の方式で 1 ラウンド。①b。D-300)。"""

from __future__ import annotations

from .. import market as marketmod
from ..abilities import POSITION_LABELS
from ..contracts import team_salary
from ..fa import MAX_ROSTER


class MarketMixin:
    """Game の一部:自由契約市場の提示(提示・取り消し・締める)。"""

    def _market_info(self, proc) -> dict:
        """市場の段階の情報(公開用)。"""
        state = self.state
        names = self._team_names()
        my = state.my_team_id
        ctx = self._contract_ctx()
        team = self._team(my) if my else None
        committed = sum(int(o["salary"]) for o in proc.market_offers.values())
        cap = ctx.cap(my) if my else None
        by_id = {p.id: p for p in proc.market}
        results = [{**x, "former_team_name": names.get(x["former_team"], "") if x.get("former_team") else "候補", "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == my} for x in proc.market_results]
        return {
            "done": bool(proc.market_done), "uses_offers": marketmod.uses_offers(ctx),
            "counts": {"pool": len(proc.market), "signed": len(proc.market_results), "offers": len(proc.market_offers), "mine": sum(1 for x in proc.market_results if x["team_id"] == my)},
            "offers": [{"player_id": pid, "name": by_id[pid].name if pid in by_id else "", "years": o["years"], "salary": o["salary"], "salary_text": f"{int(o['salary']):,} 万円"} for pid, o in proc.market_offers.items()],
            "committed": committed, "committed_text": f"{committed:,} 万円",
            "space": None if team is None else MAX_ROSTER - len(team.players) - len(proc.market_offers),
            "total": None if team is None else team_salary(team), "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "none_max_ratio": float(state.negotiation_settings.money_none["max_ratio"]) if state.money_rule == "none" else None,
            "max_years": state.contract_settings.max_years, "min_years": state.contract_settings.min_years, "minimum_salary": state.contract_settings.minimum, "rounding": state.contract_settings.rounding,
            "results": results,
        }

    def _market_open_player(self, player_id: str):
        proc = self._proc()
        if proc.phase != "market" or proc.market_done:
            raise ValueError("今は市場で提示できる段階ではありません")
        player = next((p for p in proc.market if p.id == str(player_id)), None)
        if player is None:
            raise ValueError("その選手は、市場で交渉できる選手ではありません")
        return proc, player

    def offseason_market_offer(self, player_id: str, years: int, salary: int | None = None) -> dict:
        """あなたの球団の市場の提示(出し直しは上書き)。年俸の決まりは FA と同じ(「なし」は算定の 1.0〜1.3 倍)。"""
        state = self.state
        proc, player = self._market_open_player(player_id)
        team = self._my_team()
        cs = state.contract_settings
        try:
            years = int(years)
        except (TypeError, ValueError):
            raise ValueError("年数は整数で入れてください") from None
        if not cs.min_years <= years <= cs.max_years:
            raise ValueError(f"年数は {cs.min_years}〜{cs.max_years} 年にしてください(値: {years})")
        ctx = self._contract_ctx()
        calc = marketmod.offer_terms(player, team.id, ctx)[0]
        if salary in (None, ""):
            salary = calc
        elif state.money_rule == "none":
            salary = self._none_salary(salary, calc)
        else:
            try:
                salary = int(salary)
            except (TypeError, ValueError):
                raise ValueError("年俸は整数(万円)で入れてください") from None
            if salary < cs.minimum:
                raise ValueError(f"年俸は最低年俸({cs.minimum:,} 万円)以上にしてください")
            if salary > cs.base_budget:
                raise ValueError(f"年俸が大きすぎます({cs.base_budget:,} 万円まで)")
        others = {pid: o for pid, o in proc.market_offers.items() if pid != player.id}
        if len(team.players) + len(others) + 1 > MAX_ROSTER:
            raise ValueError("空き枠が足りません(70 人。提示中の選手も数えます)")
        cap = ctx.cap(team.id)
        if ctx.hard() and cap is not None and team_salary(team) + sum(int(o["salary"]) for o in others.values()) + salary > cap:
            room = cap - team_salary(team) - sum(int(o["salary"]) for o in others.values())
            raise ValueError(f"総年俸と提示中の年俸の合計が予算の上限を超えます(この選手に出せるのは {max(0, room):,} 万円まで)")
        proc.market_offers[player.id] = {"years": years, "salary": int(salary)}
        self.dirty = True
        return self.offseason_view()

    def offseason_market_cancel(self, player_id: str) -> dict:
        proc, player = self._market_open_player(player_id)
        proc.market_offers.pop(player.id, None)
        self.dirty = True
        return self.offseason_view()

    def offseason_market_close(self) -> dict:
        """市場を締める:AI 球団の提示と合わせて、選手が選ぶ。戻り値は画面の情報と、成立した契約。"""
        state = self.state
        proc = self._proc()
        if proc.phase != "market" or proc.market_done:
            raise ValueError("今は市場を締められる段階ではありません")
        ctx = self._contract_ctx()
        signed = marketmod.close_market(state.league, proc, ctx, state.my_team_id, state.scout_sd_map(), state.draft_settings, my_ai=False)
        self.dirty = True
        view = self.offseason_view()
        names = self._team_names()
        view["last_market"] = {"signed": [{**x, "team_name": names.get(x["team_id"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == state.my_team_id} for x in signed]}
        return view
