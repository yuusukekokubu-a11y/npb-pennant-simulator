"""画面から呼ぶ操作の関数のうち、外国人市場(D-376)と、育成選手を支配下に上げる操作(D-375)。

外国人市場:候補は評価(A〜E)と一言だけを見せる。年俸は評価の段階の表、契約は 1 年。獲得の希望は入れた順が優先の順で、
締めると前年の順位が低い球団から 1 人ずつ獲得する(foreign.close_foreign)。
支配下に上げる:市場の段階で、70 人に空きがあれば自球団の育成選手を支配下に移す(年俸は最低年俸、1 年)。
"""

from __future__ import annotations

from .. import ability_model, development as devmod, foreign as foreignmod
from ..abilities import POSITION_LABELS
from ..contracts import team_salary
from ..fa import MAX_ROSTER


class ForeignMixin:
    """Game の一部:外国人市場と、育成選手を支配下に上げる操作。"""

    def _foreign_info(self, proc) -> dict:
        """外国人市場の段階の情報(公開用)。"""
        state = self.state
        names = self._team_names()
        my = state.my_team_id
        ctx = self._contract_ctx()
        team = self._team(my) if my else None
        by_id = {p.id: p for p in proc.foreign_pool}
        committed = sum(int(proc.foreign_salary.get(pid, 0)) for pid in proc.foreign_offers)
        cap = ctx.cap(my) if my else None
        results = [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == my} for x in proc.foreign_results]
        return {
            "done": bool(proc.foreign_done),
            "counts": {"pool": len(proc.foreign_pool), "signed": len(proc.foreign_results), "offers": len(proc.foreign_offers), "mine": sum(1 for x in proc.foreign_results if x["team_id"] == my)},
            "offers": [{"player_id": pid, "rank": i, "name": by_id[pid].name if pid in by_id else "", "salary": int(proc.foreign_salary.get(pid, 0)), "salary_text": f"{int(proc.foreign_salary.get(pid, 0)):,} 万円"} for i, pid in enumerate(proc.foreign_offers, start=1)],
            "committed": committed, "committed_text": f"{committed:,} 万円",
            "space": None if team is None else MAX_ROSTER - len(team.players) - len(proc.foreign_offers),
            "my_foreign": None if team is None else foreignmod.foreign_count(team),
            "active_max": state.season.game_config["active_roster"].get("foreign_max"),
            "total": None if team is None else team_salary(team), "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "salary_table": [{"grade": g, "salary": int(v), "salary_text": f"{int(v):,} 万円"} for g, v in state.draft_settings.foreign["salary"].items()],
            "results": results,
        }

    def _foreign_open_player(self, player_id: str):
        proc = self._proc()
        if proc.phase != "foreign" or proc.foreign_done:
            raise ValueError("今は外国人市場で獲得を希望できる段階ではありません")
        player = next((p for p in proc.foreign_pool if p.id == str(player_id)), None)
        if player is None:
            raise ValueError("その選手は、外国人市場の候補ではありません")
        return proc, player

    def offseason_foreign_offer(self, player_id: str) -> dict:
        """外国人市場の獲得の希望(入れた順が優先)。すでに希望している選手は、そのまま。"""
        proc, player = self._foreign_open_player(player_id)
        team = self._my_team()
        if player.id in proc.foreign_offers:
            return self.offseason_view()
        if len(team.players) + len(proc.foreign_offers) + 1 > MAX_ROSTER:
            raise ValueError("空き枠が足りません(70 人。希望中の選手も数えます)")
        ctx = self._contract_ctx()
        salary = int(proc.foreign_salary.get(player.id, 0))
        cap = ctx.cap(team.id)
        if ctx.hard() and cap is not None and team_salary(team) + salary > cap and not foreignmod.min_exempt(team, proc, player.id, self.state.draft_settings):
            raise ValueError(f"この選手の年俸({salary:,} 万円)を足すと、資金(年俸の合計の上限)を超えます(あと {max(0, cap - team_salary(team)):,} 万円まで)")
        proc.foreign_offers[player.id] = {}
        self.dirty = True
        return self.offseason_view()

    def offseason_foreign_cancel(self, player_id: str) -> dict:
        proc, player = self._foreign_open_player(player_id)
        proc.foreign_offers.pop(player.id, None)
        self.dirty = True
        return self.offseason_view()

    def offseason_foreign_close(self) -> dict:
        """外国人市場を締める:AI 球団の希望と合わせて、前年の順位が低い球団から 1 人ずつ獲得する。"""
        state = self.state
        proc = self._proc()
        if proc.phase != "foreign" or proc.foreign_done:
            raise ValueError("今は外国人市場を締められる段階ではありません")
        mins, min_batters = self._mins()
        signed = foreignmod.close_foreign(state.league, proc, self._contract_ctx(), state.my_team_id, state.scout_sd_map(), state.draft_settings, mins, min_batters, my_ai=False)
        self.dirty = True
        view = self.offseason_view()
        names = self._team_names()
        view["last_foreign"] = {"signed": [{**x, "team_name": names.get(x["team_id"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == state.my_team_id} for x in signed]}
        return view

    def _development_info(self, proc) -> dict | None:
        """自球団の育成選手(市場の段階で支配下に上げられる。D-375)。"""
        my = self.state.my_team_id
        if my is None:
            return None
        team = self._team(my)
        cfg = self.state.gen_config
        limit = self.state.draft_settings.dev_seasons
        rows = []
        for p in sorted(team.development, key=lambda p: (-ability_model.overall(p), p.id)):
            shown = ability_model.display(cfg, ability_model.overall(p))
            rows.append({"player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                         "overall": f"{ability_model.rank(cfg, shown)} {shown}", "dev_seasons": int(p.dev_seasons or 0), "left": max(0, limit - int(p.dev_seasons or 0))})
        can = proc.phase == "market" and not proc.market_done
        return {"players": rows, "count": len(team.development), "max": int(self.state.draft_settings.development["max"]), "space": MAX_ROSTER - len(team.players),
                "can_promote": can and len(team.players) < MAX_ROSTER, "promoted": [x for x in proc.promotions if x["team_id"] == my]}

    def offseason_promote(self, player_id: str) -> dict:
        """自球団の育成選手を支配下に上げる(市場の段階。70 人に空きがあるとき。D-375)。"""
        proc = self._proc()
        if proc.phase != "market" or proc.market_done:
            raise ValueError("支配下に上げられるのは、市場の段階(締める前)だけです")
        team = self._my_team()
        player = next((p for p in team.development if p.id == str(player_id)), None)
        if player is None:
            raise ValueError("その選手は、自球団の育成選手ではありません")
        if len(team.players) + len(proc.market_offers) >= MAX_ROSTER:
            raise ValueError("支配下の枠(70 人。市場で希望中の選手も数えます)に空きがありません")
        ctx = self._contract_ctx()
        if not devmod.promote_fits(team, player, ctx):
            raise ValueError("支配下に上げる(年俸は最低年俸)と、資金(年俸の合計の上限)を超えます")
        devmod.promote(team, player, proc, ctx, "you")
        self.dirty = True
        return self.offseason_view()
