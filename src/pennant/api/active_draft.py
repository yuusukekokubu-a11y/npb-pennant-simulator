"""画面から呼ぶ操作の関数のうち、現役ドラフト(D-380):候補を出す・指名する・段階の情報。

- 段階に入ったら、あなたの候補は AI のおすすめが入った状態(まだ決まっていない)。「この 2 人を出す」で全球団の候補が決まり、
  前年の順位が低い球団から指名が進む。あなたの番で止まり、候補の表から 1 人選ぶ。
- 「次の手続きへ」「この段階をおまかせ」は、残り(候補・指名)を AI の方針で決める。
"""

from __future__ import annotations

from .. import active_draft as admod
from ..abilities import POSITION_LABELS


class ActiveDraftMixin:
    """Game の一部:現役ドラフト。"""

    def _active_proc(self):
        proc = self._proc()
        if proc.phase != "active_draft":
            raise ValueError("今は現役ドラフトの段階ではありません")
        return proc

    def _active_draft_info(self, proc) -> dict:
        """現役ドラフトの段階の情報(公開用)。"""
        state = self.state
        my = state.my_team_id
        names = self._team_names()
        ctx = self._contract_ctx()
        mins, min_batters = self._mins()
        ts = state.trade_settings
        players = {p.id: p for p in state.league.all_players()}
        out = {"offers_done": proc.active_offers_done, "max_offers": int(admod.settings_of(ts)["offers"]), "current_team": None, "is_my_turn": False, "finished": False}
        if my is not None and not proc.active_offers_done:
            team = self._team(my)
            first = admod.first_team_ids_for(proc, team, ctx, ts)
            elig = admod.eligible(team, proc, ctx, mins, min_batters, first, ts)
            rec = admod.ai_offers(team, proc, ctx, mins, min_batters, first, ts)
            out["eligible"] = [self._trade_player(p, set()) for p in elig]
            out["recommended"] = rec
        if proc.active_offers_done:
            tid = admod.current_picker(proc)
            out["current_team"] = tid
            out["current_team_name"] = names.get(tid, "") if tid else ""
            out["is_my_turn"] = tid is not None and tid == my
            out["finished"] = tid is None
            out["offers"] = [{"team_id": t, "team_name": names.get(t, ""), "is_mine": t == my, "taken": t in {x["from_team"] for x in proc.active_picks},
                              "players": [self._trade_player(players[pid], set()) | {"team_id": players[pid].team_id} for pid in proc.active_offers.get(t, []) if pid in players]}
                             for t in proc.order]
            if out["is_my_turn"]:
                options = admod.choices(state.league, proc, my)
                best = admod.ai_pick(self._team(my), options, proc, ctx, state.gen_config, ts)
                out["choices"] = [self._trade_player(p, set()) | {"team_id": p.team_id, "team_name": names.get(p.team_id, "")} for p in options]
                out["recommended_pick"] = best.id if best else None
        out["picks"] = [{**x, "team_name": names.get(x["team_id"], ""), "from_team_name": names.get(x.get("from_team"), ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": my in (x["team_id"], x.get("from_team"))} for x in proc.active_picks if x.get("player_id")]
        return out

    def active_draft_offer(self, player_ids: list[str]) -> dict:
        """あなたの候補を決めて(全球団の候補が決まる)、あなたの番まで指名を進める。戻り値は手続きの画面の情報。"""
        state = self.state
        proc = self._active_proc()
        if state.my_team_id is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        if proc.active_offers_done:
            raise ValueError("候補はもう決まっています")
        mins, min_batters = self._mins()
        ctx = self._contract_ctx()
        admod.set_offers(state.league, proc, ctx, mins, min_batters, state.my_team_id, [str(x) for x in player_ids], state.trade_settings)
        admod.run_turns(state.league, proc, ctx, state.gen_config, state.my_team_id, None, state.trade_settings)
        self.dirty = True
        return self.offseason_view()

    def active_draft_pick(self, player_id: str) -> dict:
        """あなたの番で 1 人指名して、次のあなたの番(なければ最後)まで進める。"""
        state = self.state
        proc = self._active_proc()
        if not proc.active_offers_done:
            raise ValueError("先に、出す候補を決めてください")
        if admod.current_picker(proc) != state.my_team_id:
            raise ValueError("今はあなたの指名の番ではありません")
        admod.run_turns(state.league, proc, self._contract_ctx(), state.gen_config, state.my_team_id, str(player_id), state.trade_settings)
        self.dirty = True
        return self.offseason_view()

    def _active_draft_finish(self) -> None:
        """残りの候補・指名を AI の方針で決める(「次の手続きへ」「この段階をおまかせ」)。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        ctx = self._contract_ctx()
        admod.set_offers(state.league, proc, ctx, mins, min_batters, state.my_team_id, None, state.trade_settings)
        admod.run_turns(state.league, proc, ctx, state.gen_config, None, None, state.trade_settings)

    def _add_active_draft_transactions(self, proc) -> None:
        for x in proc.active_picks:
            if x.get("player_id"):
                self.state.transactions.append({"year": proc.year, "phase": "active_draft", "round": 0, **{k: v for k, v in x.items() if k not in ("year", "by")}})
