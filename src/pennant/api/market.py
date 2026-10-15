"""画面から呼ぶ操作の関数のうち、自由契約市場(獲得の希望・取り消し・締める。作り直し③。D-370)。

年俸は自動(プロの選手は算定、指名されなかった候補は最低年俸。1 年)。希望は入れた順が優先の順。
市場を締めると、前年の順位が低い球団から 1 人ずつ獲得する(market.close_market)。
"""

from __future__ import annotations

from .. import market as marketmod
from ..abilities import POSITION_LABELS
from ..contracts import team_salary
from ..fa import MAX_ROSTER


class MarketMixin:
    """Game の一部:自由契約市場(獲得の希望・取り消し・締める)。"""

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
            "offers": [{"player_id": pid, "rank": i, "name": by_id[pid].name if pid in by_id else "", "years": o["years"], "salary": o["salary"], "salary_text": f"{int(o['salary']):,} 万円"} for i, (pid, o) in enumerate(proc.market_offers.items(), start=1)],
            "committed": committed, "committed_text": f"{committed:,} 万円",
            "space": None if team is None else MAX_ROSTER - len(team.players) - len(proc.market_offers),
            "total": None if team is None else team_salary(team), "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "order": [{"team_id": t, "team_name": names.get(t, ""), "is_mine": t == my} for t in proc.order],
            "results": results,
        }

    def _market_open_player(self, player_id: str):
        proc = self._proc()
        if proc.phase != "market" or proc.market_done:
            raise ValueError("今は市場で獲得を希望できる段階ではありません")
        player = next((p for p in proc.market if p.id == str(player_id)), None)
        if player is None:
            raise ValueError("その選手は、市場で獲得できる選手ではありません")
        return proc, player

    def offseason_market_offer(self, player_id: str, years: int | None = None, salary: int | None = None) -> dict:
        """あなたの球団の獲得の希望(D-370)。年俸と年数は自動(years・salary は前の形の呼び出しのため受け取るが使わない)。
        すでに希望している選手は、そのまま(順番は変えない)。"""
        proc, player = self._market_open_player(player_id)
        team = self._my_team()
        if player.id in proc.market_offers:
            return self.offseason_view()
        if len(team.players) + len(proc.market_offers) + 1 > MAX_ROSTER:
            raise ValueError("空き枠が足りません(70 人。希望中の選手も数えます)")
        ctx = self._contract_ctx()
        salary, years, _ = marketmod.market_terms(proc, player, team.id, ctx)
        cap = ctx.cap(team.id)
        if marketmod.over_cap(team, salary, 0, ctx):  # 最低年俸の選手は資金に関わらず(D-386)
            raise ValueError(f"この選手の年俸({salary:,} 万円)を足すと、資金(年俸の合計の上限)を超えます(あと {max(0, cap - team_salary(team)):,} 万円まで)")
        proc.market_offers[player.id] = {"years": int(years), "salary": int(salary)}
        self.dirty = True
        return self.offseason_view()

    def offseason_market_cancel(self, player_id: str) -> dict:
        proc, player = self._market_open_player(player_id)
        proc.market_offers.pop(player.id, None)
        self.dirty = True
        return self.offseason_view()

    def offseason_market_close(self) -> dict:
        """市場を締める:AI 球団の希望と合わせて、前年の順位が低い球団から 1 人ずつ獲得する。戻り値は画面の情報と、成立した契約。"""
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
