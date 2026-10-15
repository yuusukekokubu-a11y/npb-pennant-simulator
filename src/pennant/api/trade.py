"""画面から呼ぶ操作の関数のうち、トレード(D-378):交換の提案・AI からの提案への答え・トレードの画面・シーズン中の決まった日の AI の動き。

- シーズン中は全日程の 3 分の 2(trade.json の deadline_ratio)の日まで。オフはオフの手続きの「トレード」の段階。
- あなたから持ちかけると、相手(AI)はその場で受けるか断るかを答える(trade.ai_answer)。
- AI どうしのトレードと AI からの提案は、シーズン中は決まった日(trade.season_check_days)、オフはトレードの段階の始め(trade.start_offseason_trades)。
- シーズン中に成立したら、両球団の一軍を選び直す(Season.reselect_actives)。
"""

from __future__ import annotations

import random

from .. import ability_model, draft as draftmod, trade as trademod
from ..abilities import POSITION_LABELS
from ..season import derive_seed
from .common import BATS_LABELS, THROWS_LABELS


class TradeMixin:
    """Game の一部:トレード。"""

    def _trade_window(self) -> tuple[str | None, str]:
        """今トレードできるか:("season" / "offseason", "") か (None, できない理由)。"""
        state = self.state
        proc = state.procedure
        if not state.rules.trade:
            return None, "この新規開始の設定では、トレードはありません"
        if proc is not None:
            if proc.phase == "trade":
                return "offseason", ""
            return None, "オフはトレードの段階(市場の後)でできます"
        season = state.season
        if season.is_over:
            return None, "シーズンが終わりました。オフのトレードの段階でできます"
        if season.day >= trademod.deadline_day(season.total_days, state.trade_settings):
            return None, f"シーズン中のトレードの期限({trademod.deadline_day(season.total_days, state.trade_settings)}日目まで)を過ぎました。オフのトレードの段階でできます"
        return "season", ""

    def _trade_ctx(self):
        state = self.state
        ctx = draftmod.state_context(state, state.procedure)
        if state.procedure is None:
            rates = state.contract_rates
            ctx.rate = float(rates.get(str(state.year)) or (max(rates.values()) if rates else 0.0))
        return ctx

    def _trade_locked(self) -> set[str]:
        state = self.state
        if state.procedure is not None:
            return trademod.locked_ids(state.procedure)
        return trademod.locked_ids(None, state.season.trades)

    def _trade_records(self) -> list[dict]:
        state = self.state
        return state.procedure.trades if state.procedure is not None else state.season.trades

    def _trade_offers(self) -> list[dict]:
        state = self.state
        return state.procedure.trade_offers if state.procedure is not None else state.season.trade_offers

    def _trade_player(self, p, locked: set[str]) -> dict:
        cfg = self.state.gen_config
        shown = ability_model.display(cfg, ability_model.overall(p))
        salary = int(p.contract["salary"]) if p.contract else None
        return {"player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age,
                "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws), "overall": f"{ability_model.rank(cfg, shown)} {shown}",
                "overall_value": shown, "salary": salary, "salary_text": "-" if salary is None else f"{salary:,}", "foreign": p.foreign, "locked": p.id in locked}

    def _trade_public(self, rec: dict) -> dict:
        names = self._team_names()
        return {**rec, "team_a_name": names.get(rec["team_a"], ""), "team_b_name": names.get(rec["team_b"], ""),
                "is_mine": self.state.my_team_id in (rec["team_a"], rec["team_b"]),
                "text": f"{names.get(rec['team_a'], '')} の {'・'.join(rec['give_names'])} ⇔ {names.get(rec['team_b'], '')} の {'・'.join(rec['get_names'])}"}

    def _offer_public(self, offer: dict) -> dict:
        names = self._team_names()
        return {**offer, "team_name": names.get(offer["team_id"], ""), "text": f"{names.get(offer['team_id'], '')} から:{'・'.join(offer['give_names'])} と、あなたの {'・'.join(offer['get_names'])} の交換"}

    def trade_view(self, team_id: str | None = None) -> dict:
        """トレードの画面(公開用):できるか・期限・自球団と相手の選手(出せない選手の印)・AI からの提案・このシーズン(オフ)のトレード。"""
        state = self.state
        my = state.my_team_id
        if my is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        window, reason = self._trade_window()
        locked = self._trade_locked()
        teams = [{"team_id": t.id, "team_name": t.name} for t in state.league.teams if t.id != my]
        partner = team_id if team_id in {t["team_id"] for t in teams} else teams[0]["team_id"]
        order = {k: i for i, k in enumerate(POSITION_LABELS)}

        def players(tid):
            return [self._trade_player(p, locked) for p in sorted(self._team(tid).players, key=lambda p: (order[p.position], p.id))]

        season = state.season
        return {
            "window": window, "reason": reason, "max_players": state.trade_settings.max_players,
            "deadline_day": trademod.deadline_day(season.total_days, state.trade_settings), "day": season.day,
            "teams": teams, "partner": partner, "mine": players(my), "theirs": players(partner),
            "offers": [self._offer_public(x) for x in self._trade_offers()],
            "trades": [self._trade_public(x) for x in self._trade_records()],
            "history": [x for x in state.transactions if x.get("phase") == "trade"],
        }

    def _do_trade(self, team_a, team_b, gives, gets, by: str) -> dict:
        """交換を行い、記録を残す(シーズン中は一軍を選び直し、入退団の記録に足す)。"""
        state = self.state
        proc = state.procedure
        season = state.season
        day = None if proc is not None else season.day
        year = proc.year if proc is not None else state.year
        rec = trademod.execute(team_a, team_b, gives, gets, year, day, by)
        self._trade_records().append(rec)
        if proc is None:
            season.reselect_actives([team_a.id, team_b.id])
            self._add_trade_transactions(rec)
            self._cache_clear_after_trade()
        self.dirty = True
        return rec

    def _cache_clear_after_trade(self) -> None:
        from .common import _StatsCache

        self._cache = _StatsCache()
        self._war = None

    def _add_trade_transactions(self, rec: dict) -> None:
        """入退団の記録に、動いた選手を 1 人ずつ足す(phase="trade")。"""
        players = {p.id: p for p in self.state.league.all_players()}
        for pid, to, frm in [(x, rec["team_b"], rec["team_a"]) for x in rec["gives"]] + [(x, rec["team_a"], rec["team_b"]) for x in rec["gets"]]:
            p = players[pid]
            self.state.transactions.append({"year": rec["year"], "phase": "trade", "round": 0, "day": rec["day"], "team_id": to, "from_team": frm, "player_id": pid, "name": p.name, "role": p.role, "position": p.position, "age": p.age})

    def trade_propose(self, team_id: str, gives: list[str], gets: list[str]) -> dict:
        """あなたから交換を持ちかける。相手(AI)はその場で答える。戻り値は画面の情報と結果({accepted, message})。"""
        state = self.state
        window, reason = self._trade_window()
        if window is None:
            raise ValueError(reason)
        mine = self._team(state.my_team_id)
        other = self._team(str(team_id))
        gives = [str(x) for x in gives]
        gets = [str(x) for x in gets]
        ctx = self._trade_ctx()
        mins, min_batters = self._mins()
        why = trademod.check(mine, other, gives, gets, ctx, self._trade_locked(), mins, min_batters, state.trade_settings)
        if why:
            raise ValueError(why)
        give_p = [p for p in mine.players if p.id in gives]
        get_p = [p for p in other.players if p.id in gets]
        ok, text = trademod.ai_answer(other, get_p, give_p, ctx, ctx.rate or 1.0, state.gen_config, state.trade_settings)
        result = {"accepted": ok, "message": f"{other.name}:{text}"}
        if ok:
            rec = self._do_trade(mine, other, gives, gets, "you")
            result["trade"] = self._trade_public(rec)
        view = self.trade_view(other.id)
        view["result"] = result
        return view

    def trade_offer_answer(self, offer_id: str, accept: bool) -> dict:
        """AI からの提案に答える。受けたときに条件を満たさなくなっていれば、理由を出して断ったことにする。"""
        state = self.state
        window, reason = self._trade_window()
        if window is None:
            raise ValueError(reason)
        offers = self._trade_offers()
        offer = next((x for x in offers if x["id"] == str(offer_id)), None)
        if offer is None:
            raise ValueError("その提案はもうありません")
        offers.remove(offer)
        self.dirty = True
        result = {"accepted": False, "message": "提案を断りました"}
        if accept:
            mine = self._team(state.my_team_id)
            other = self._team(offer["team_id"])
            mins, min_batters = self._mins()
            why = trademod.check(mine, other, offer["gets"], offer["gives"], self._trade_ctx(), self._trade_locked(), mins, min_batters, state.trade_settings)
            if why:
                result = {"accepted": False, "message": f"この提案は成り立たなくなりました:{why}"}
            else:
                rec = self._do_trade(mine, other, offer["gets"], offer["gives"], "offer")
                result = {"accepted": True, "message": "提案を受けて、トレードが成立しました", "trade": self._trade_public(rec)}
        view = self.trade_view(offer["team_id"])
        view["result"] = result
        return view

    def _season_trade_check(self) -> None:
        """シーズン中の決まった日の AI の動き:前の提案は消え、AI どうしのトレードと、あなたへの提案を作る(D-378)。"""
        state = self.state
        season = state.season
        s = state.trade_settings
        if not state.rules.trade:  # 新規開始の設定でトレードなし(D-381)
            return
        if season.day >= trademod.deadline_day(season.total_days, s):
            season.trade_offers = []
            return
        rng = random.Random(derive_seed(season.seed, f"trade:{season.day}"))
        ctx = self._trade_ctx()
        if not ctx.rate:
            return
        mins, min_batters = self._mins()
        locked = trademod.locked_ids(None, season.trades)
        season.trade_offers = []
        for rec in trademod.ai_trades(rng, state.league, state.my_team_id, int(s.ai("season_ai_trades")), ctx, ctx.rate, state.gen_config, locked, mins, min_batters, state.year, season.day, s):
            season.trades.append(rec)
            season.reselect_actives([rec["team_a"], rec["team_b"]])
            self._add_trade_transactions(rec)
        if state.my_team_id is not None and rng.random() < float(s.ai("season_proposal_probability")):
            prop = trademod.make_proposal(rng, state.league, state.my_team_id, ctx, ctx.rate, state.gen_config, locked, mins, min_batters, state.year, season.day, 1, s)
            if prop is not None:
                season.trade_offers.append(prop)
        self._cache_clear_after_trade()

    def _trade_status(self) -> dict | None:
        """進行の画面に出すトレードの状態(自球団があるときだけ):できるか・提案の件数。"""
        if self.state.my_team_id is None or not self.state.rules.trade:
            return None
        window, _ = self._trade_window()
        return {"open": window is not None, "offers": len(self._trade_offers()) if window else 0}
