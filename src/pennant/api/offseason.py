"""画面から呼ぶ操作の関数のうち、オフの手続きの流れ(段階・状態・次の手続きへ・おまかせ・オフの結果)。"""

from __future__ import annotations

from .. import draft as draftmod, fa as famod
from ..abilities import POSITION_LABELS
from ..draft import STAGES, STAGE_LABELS, stage_of
from ..season import Season, derive_seed
from .common import BATS_LABELS, ORIGIN_LABELS, ROLE_LABELS, THROWS_LABELS, _StatsCache


class OffseasonMixin:
    """Game の一部:オフの手続きの流れ(段階・状態・次の手続きへ・おまかせ・オフの結果)。"""

    def _mins(self):
        return draftmod.minimum_positions(self.state.season.game_config), draftmod.minimum_batters(self.state.season.game_config)

    def _finish_offseason(self, filled) -> None:
        """オフの手続きの完了:履歴に記録し、次のシーズンを作る。"""
        state = self.state
        proc = state.procedure
        season = state.season
        result = state.offseasons[-1]
        result.rookies = draftmod.joined_players(proc) + list(filled)
        for x in proc.released:
            state.transactions.append({"year": proc.year, "phase": "release", "round": 0, **x})
        for x in proc.picks:
            state.transactions.append({"year": proc.year, **x})
        for x in proc.filled:
            state.transactions.append({"year": proc.year, **x})  # 自動補充も履歴に残す(振り返りのため。D-216)
        for pid, x in proc.fa_info.items():
            state.transactions.append({"year": proc.year, "phase": "fa_declare", "round": 0, "team_id": x["former_team"], "player_id": pid, "name": x["name"], "role": x["role"], "position": x["position"], "age": x["age"], "salary": x.get("old_salary")})
        for x in proc.fa_results:
            state.transactions.append({"year": proc.year, "phase": "fa", **{k: v for k, v in x.items() if k != "offers"}})
        self.last_negotiations = proc.negotiations  # 終わった手続きの更改の交渉(保存しない。指紋 (p) と開発者向けの集計用)
        self.last_fa = {"info": proc.fa_info, "results": proc.fa_results, "log": proc.fa_log, "ranks": dict(proc.ranks), "budget_releases": list(proc.budget_releases)}  # 終わった FA(保存しない。指紋 (q) と集計用)
        state.procedure = None
        state.year += 1
        state.season = Season(state.league, derive_seed(season.seed, "next-season"), season.season_config, season.game_config, season.model, season.manager)
        self._cache = _StatsCache()
        self._park_estimates = None
        self._war = None
        self.dirty = True

    def _proc(self):
        proc = self.state.procedure
        if proc is None:
            raise ValueError("オフの手続きは進行中ではありません")
        return proc

    def _my_team(self):
        if self.state.my_team_id is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        return self._team(self.state.my_team_id)

    def _report_public(self, player, team_id: str) -> dict:
        proc = self._proc()
        return draftmod.scout_report(proc, player, team_id, self.state.scout_sd_of(team_id), self.state.draft_settings).to_public()

    def _player_brief(self, p, team_id: str | None, names: dict) -> dict:
        return {"player_id": p.id, "name": p.name, "role": p.role, "role_label": ROLE_LABELS[p.role], "position": p.position, "position_label": POSITION_LABELS[p.position], "age": p.age, "origin": ORIGIN_LABELS.get(p.origin, "") if p.origin else "", "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws), "team_id": team_id, "team_name": names.get(team_id, "") if team_id else ""}

    def offseason_view(self) -> dict:
        """オフの手続きの画面に出す情報(公開用。スカウト評価は操作する球団のもの)。"""
        state = self.state
        proc = self._proc()
        names = self._team_names()
        my = state.my_team_id
        mins, min_batters = self._mins()
        view = {
            "year": proc.year,
            "next_year": proc.year + 1,
            "phase": proc.phase,
            "phase_label": STAGE_LABELS[stage_of(proc.phase)],
            "stage": stage_of(proc.phase),
            "phases": [{"key": k, "label": STAGE_LABELS[k]} for k in STAGES],
            "order": [{"team_id": t, "team_name": names[t], "is_mine": t == my} for t in proc.order],
            "round": proc.round,
            "total_rounds": proc.total_rounds() if proc.phase in ("draft", "market") else 0,
            "current_team": proc.current_team() if proc.phase in ("draft", "market") else None,
            "is_my_turn": proc.phase in ("draft", "market") and proc.current_team() == my and not draftmod.phase_finished(proc),
            "phase_finished": draftmod.phase_finished(proc) if proc.phase in ("draft", "market") else proc.phase == "done",
            "my_team": None if my is None else {"team_id": my, "team_name": names[my], "players": len(self._team(my).players), "max": draftmod.MAX_ROSTER, "shortages": self._shortage_text(self._team(my).players, mins, min_batters)},
            "my_release_done": proc.my_release_done,
            "contracts": self._procedure_contracts(proc),
            "minimums": {"positions": dict(mins), "batters": min_batters, "labels": {pos: POSITION_LABELS[pos] for pos in mins}},
            "seasons": self.roster_seasons(),
            "scout_level": state.scout_level,
            "scout_sd": state.scout_sd_of(my) if my else None,
            "picks": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my} for x in proc.picks],
            "released": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "is_mine": x["team_id"] == my} for x in proc.released],
            "counts": {"candidates": len(proc.candidates), "market": len(proc.market), "released": len(proc.released), "picked": sum(1 for x in proc.picks if x["player_id"])},
            "rosters": [{"team_id": t.id, "team_name": t.name, "players": len(t.players), "is_mine": t.id == my} for t in state.league.teams],
            "note": "手続きは 契約更改 → 自由契約 → FA → ドラフト → 自由契約市場 → 完了(自動補充)の順です。途中で保存して、あとで続きから再開できます。「おまかせ」を押すと、残りを自動(AI と同じ方針)で進めます。",
        }
        if my is not None and stage_of(proc.phase) == "contract":
            view["renewal"] = self._renewal_info(proc)
        if proc.phase == "fa":
            view["fa"] = self._fa_info(proc)
        if my is not None:
            team = self._team(my)
            if proc.phase in ("draft", "market"):
                pool = draftmod.pool_of(proc)
                rows = []
                for p in pool:
                    row = self._player_brief(p, None, names)
                    row["scouting"] = self._report_public(p, my)
                    row["former_team"] = next((names.get(x["team_id"], "") for x in proc.released if x["player_id"] == p.id), "")
                    rows.append(row)
                rows.sort(key=lambda r: (-r["scouting"]["overall"], r["player_id"]))
                view["pool"] = rows
        return view

    def _shortage_text(self, players, mins, min_batters) -> list[str]:
        out = []
        for pos, n in draftmod.shortages(players, mins, min_batters).items():
            out.append(f"{'野手' if pos == 'batter' else POSITION_LABELS[pos]} があと {n} 人足りません")
        return out

    def offseason_next(self) -> dict:
        """「次の手続きへ」(D-271):自分の操作を終えて次の段階へ進む(AI の代行はしない)。
        契約:全員が決まり、標準以上で上限を超えていなければ、AI 球団の予算超過の解消と自由契約を行って FA へ。
        FA:自球団は追加の提示をせず(今のラウンドの提示は有効)、残りのラウンドを締める。ドラフト・市場:自球団の残りの番はパス。
        市場の次は完了(自動補充 → 次のシーズン)。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        ctx = self._contract_ctx()
        my = state.my_team_id
        stage = stage_of(proc.phase)
        if stage == "contract":
            if my is not None and draftmod.open_entries(proc, my):
                n = len(draftmod.open_entries(proc, my))
                raise ValueError(f"更改が決まっていない選手が {n} 人います。全員を更改か自由契約にしてから進めてください")
            if my is not None and ctx.hard() and draftmod.over_cap(self._my_team(), ctx) > 0:
                raise ValueError(f"総年俸が予算の上限を {draftmod.over_cap(self._my_team(), ctx):,} 万円超えています。自由契約で減らしてから進めてください")
            draftmod.finish_contract_stage(state.league, proc, ctx, sd, state.draft_settings, my, mins, min_batters, my_ai=False)
        elif stage == "fa":
            famod.run_all_rounds(state.league, proc, ctx, my, my_ai=False)
            draftmod.next_phase(proc)
        elif stage in ("draft", "market"):
            self._run_turns(my_ai=False)
            draftmod.next_phase(proc)
        return self._after_stage()

    def _run_turns(self, my_ai: bool) -> None:
        """ドラフト・市場の残りの番を最後まで。my_ai なら自球団の番も AI の方針、そうでなければ自球団の番はパス。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        ctx = self._contract_ctx()
        my = None if my_ai else state.my_team_id
        while draftmod.run_ai_turns(state.league, proc, sd, state.draft_settings, my, mins, min_batters, ctx=ctx):
            draftmod.pass_turn(proc, my, "pass")

    def _after_stage(self) -> dict:
        state = self.state
        proc = self._proc()
        if proc.phase == "done":
            mins, min_batters = self._mins()
            filled = draftmod.finalize(state.league, proc, state.gen_config, state.name_parts, state.draft_settings, state.scout_sd_map(), state.calibration, mins, min_batters, ctx=self._contract_ctx())
            self._finish_offseason(filled)
            self.dirty = True
            return {"finished": True, "summary": self.offseason_summary(), "status": self.status()}
        self.dirty = True
        return self.offseason_view()

    def offseason_stage_auto(self) -> dict:
        """「この段階をおまかせ」(D-271):今の段階だけを AI の方針で、自球団の分も代行して進め、次の段階の入口で止まる。
        戻り値の auto_log は、自球団の分として AI が行ったことの一覧。市場では完了まで進めて結果を返す。"""
        state = self.state
        proc = self._proc()
        my = state.my_team_id
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        ctx = self._contract_ctx()
        stage = stage_of(proc.phase)
        names = self._team_names()
        before_status = {pid: e["status"] for pid, e in proc.negotiations.items() if e["team_id"] == my}
        n_released, n_picks, n_fa = len(proc.released), len(proc.picks), len(proc.fa_results)
        if stage == "contract":
            draftmod.finish_contract_stage(state.league, proc, ctx, sd, state.draft_settings, my, mins, min_batters, my_ai=True)
        elif stage == "fa":
            famod.run_all_rounds(state.league, proc, ctx, my, my_ai=True)
            draftmod.next_phase(proc)
        elif stage in ("draft", "market"):
            self._run_turns(my_ai=True)
            draftmod.next_phase(proc)
        else:
            raise ValueError("この段階には、おまかせできる操作がありません")
        log = []
        for pid, old in before_status.items():
            e = proc.negotiations[pid]
            if e["status"] == old or old != "pending":
                continue
            if e["status"] == "accepted":
                o = e["offers"][-1]
                log.append({"kind": "renew", "player_id": pid, "name": e["name"], "text": f"更改:{e['name']}({o['years']} 年・{int(o['salary']):,} 万円)"})
            elif e["status"] == "declared":
                log.append({"kind": "declare", "player_id": pid, "name": e["name"], "text": f"FA 宣言:{e['name']}"})
            elif e["status"] == "released":
                log.append({"kind": "release", "player_id": pid, "name": e["name"], "text": f"自由契約(交渉決裂):{e['name']}"})
        for x in proc.released[n_released:]:
            if x["team_id"] == my and x.get("note") != "negotiation":
                why = "予算超過" if x.get("note") == "budget" else "自由契約"
                log.append({"kind": "release", "player_id": x["player_id"], "name": x["name"], "text": f"{why}:{x['name']}"})
        for x in proc.fa_results[n_fa:]:
            if x["team_id"] == my:
                log.append({"kind": "fa", "player_id": x["player_id"], "name": x["name"], "text": f"FA で獲得:{x['name']}({x['years']} 年・{int(x['salary']):,} 万円)"})
            elif x["former_team"] == my:
                log.append({"kind": "fa_out", "player_id": x["player_id"], "name": x["name"], "text": f"FA で移籍:{x['name']} → {names.get(x['team_id'], '')}"})
        for x in proc.picks[n_picks:]:
            if x["team_id"] == my and x["player_id"]:
                where = f"ドラフト {x['round']} 巡" if x["phase"] == "draft" else "市場"
                log.append({"kind": x["phase"], "player_id": x["player_id"], "name": x["name"], "text": f"{where}:{x['name']}"})
        out = self._after_stage()
        out["auto_log"] = {"stage": stage, "stage_label": STAGE_LABELS[stage], "items": log}
        return out

    def offseason_auto(self) -> dict:
        """「おまかせ」:残りの手続きを AI と同じ方針で最後まで進め、次のシーズンを始める(D-207)。"""
        state = self.state
        proc = self._proc()
        mins, min_batters = self._mins()
        filled = draftmod.complete(state.league, proc, state.gen_config, state.name_parts, state.draft_settings, state.scout_sd_map(), state.calibration, state.my_team_id, mins, min_batters, ctx=self._contract_ctx())
        self._finish_offseason(filled)
        return {"finished": True, "summary": self.offseason_summary(), "status": self.status()}

    def offseason_summary(self, year: int | None = None) -> dict:
        """オフの結果(公開用):引退した選手、入団した新人。能力の増減は答え合わせ用(answers.offseason_answers)。"""
        if not self.state.offseasons:
            return {"available": False, "years": []}
        if year is None:
            year = self.state.offseasons[-1].year
        r = next((o for o in self.state.offseasons if o.year == year), None)
        if r is None:
            raise ValueError(f"{year} シーズン目のオフの結果はありません")
        names = self._team_names()

        def note(n):
            return {"player_id": n.player_id, "name": n.name, "team_id": n.team_id, "team_name": names.get(n.team_id, n.team_id), "role": n.role, "role_label": ROLE_LABELS[n.role], "position": POSITION_LABELS[n.position], "age": n.age, "origin": ORIGIN_LABELS.get(n.origin, "") if n.origin else "", "is_mine": n.team_id == self.state.my_team_id}

        order = {k: i for i, k in enumerate(POSITION_LABELS)}
        retired = sorted((note(n) for n in r.retired), key=lambda d: (d["team_id"], order[next(k for k, v in POSITION_LABELS.items() if v == d["position"])], d["player_id"]))
        rookies = sorted((note(n) for n in r.rookies), key=lambda d: (d["team_id"], d["player_id"]))
        return {
            "available": True,
            "year": r.year,
            "next_year": r.year + 1,
            "years": [o.year for o in self.state.offseasons],
            "retired": retired,
            "rookies": rookies,
            "counts": {"retired": len(r.retired), "rookies": len(r.rookies), "players": len(self.state.league.all_players())},
            "note": f"{r.year}シーズン目の終わりに行ったオフの結果です。残った選手は年齢が1つ進み、能力が更新されました(能力の増減は、答え合わせモードがオンのときだけ見られます)。新人は、ドラフト・自由契約市場・自動補充で入った選手です。",
        }
