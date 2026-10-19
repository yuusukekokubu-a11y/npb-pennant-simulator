"""画面から呼ぶ操作の関数のうち、球団職員(D-474〜D-477):オフの「職員」の段階(希望・解任・締める)、
チームのページの職員と入れ替え・AI に戻す、記録室の監督の成績。腕は数を出さず、ランクだけを返す(D-473)。"""

from __future__ import annotations

from .. import staff as staffmod
from ..draft import stage_of


class StaffMixin:
    """Game の一部:球団職員。"""

    def _staff_season(self) -> int:
        """職員の契約・年俸を数えるシーズン(オフの手続きの間は次のシーズン)。"""
        proc = self.state.procedure
        return proc.year + 1 if proc is not None else self.state.year

    def _staff_public(self, m: dict, season: int | None = None) -> dict:
        s = staffmod.default_settings()
        names = self._team_names()
        season = self._staff_season() if season is None else season
        until = m.get("until")
        player = self.state.season.players.get(m["player_id"]) if m.get("player_id") else None
        return {
            "staff_id": m["id"], "name": staffmod.name_of(m), "role": m["role"], "role_label": s.label(m["role"]), "rank": s.rank(int(m["skill"])), "age": int(m["age"]),
            "trait": m.get("trait", "none"), "trait_label": s.trait_label(m.get("trait", "none")), "former": m.get("origin") == "former",
            "player_id": m.get("player_id") if player is not None else None, "salary": int(m["salary"]), "salary_text": f"{int(m['salary']):,} 万円",
            "years": int(m["years"]), "until": until, "remaining": None if until is None else max(0, int(until) - season + 1),
            "former_team_name": names.get(m.get("former_team"), "") if m.get("former_team") else "",
            "left": m.get("left", ""),
        }

    def _staff_table(self, team) -> list[dict]:
        return [self._staff_public(team.staff[r]) for r in staffmod.ROLES if r in (team.staff or {})]

    def _staff_room(self, team) -> int | None:
        return staffmod.room(team, self.state.team_funds, self._staff_season(), self.state.facility_settings)

    # ---- オフの「職員」の段階(D-474、D-475) ----

    def _staff_info(self, proc) -> dict:
        """職員の段階の画面の情報:自球団の職員・空き・市場・希望・出来事・結果。"""
        state = self.state
        s = staffmod.default_settings()
        names = self._team_names()
        my = state.my_team_id
        team = self._team(my) if my is not None else None
        room = self._staff_room(team) if team is not None else None
        vac = staffmod.vacancies(team) if team is not None else []
        offers = list(proc.staff_offers)
        pool = []
        for m in state.league.staff_free:
            x = self._staff_public(m)
            x["offer"] = m["id"] in offers
            x["offer_rank"] = offers.index(m["id"]) + 1 if m["id"] in offers else None
            x["open"] = team is not None and not proc.staff_done and m["role"] in vac and (room is None or int(m["salary"]) <= room)
            pool.append(x)
        order = {r: i for i, r in enumerate(staffmod.ROLES)}
        pool.sort(key=lambda x: (order[x["role"]], "SABCDE".index(x["rank"]), x["salary"], x["staff_id"]))
        return {
            "done": proc.staff_done,
            "roles": [{"key": r, "label": s.label(r)} for r in staffmod.ROLES],
            "mine": [] if team is None else self._staff_table(team),
            "vacancies": [{"key": r, "label": s.label(r)} for r in vac],
            "room": room, "room_text": None if room is None else f"{room:,} 万円",
            "pool": pool,
            "offers": [x for x in pool if x["offer"]],
            "events": [{**e, "team_name": names.get(e.get("team_id"), ""), "role_label": s.label(e["role"]), "is_mine": e.get("team_id") == my} for e in proc.staff_events],
            "results": [{**r, "team_name": names.get(r["team_id"], ""), "role_label": s.label(r["role"]), "rank": s.rank(int(r["skill"])), "salary_text": f"{int(r['salary']):,} 万円", "is_mine": r["team_id"] == my} for r in proc.staff_results],
            "counts": {"pool": len(pool), "offers": len(offers), "vacancies": len(vac), "hired": len(proc.staff_results)},
        }

    def _staff_stage(self):
        proc = self._proc()
        if proc.phase != "staff":
            raise ValueError("職員の段階ではありません")
        if proc.staff_done:
            raise ValueError("職員の市場はもう締めました")
        return proc

    def offseason_staff_offer(self, staff_id: str) -> dict:
        """雇う希望を出す(空いている役割の候補だけ。資金に収まる候補だけ。入れた順が優先)。"""
        proc = self._staff_stage()
        team = self._my_team()
        m = next((x for x in self.state.league.staff_free if x["id"] == staff_id), None)
        if m is None:
            raise ValueError("職員の市場にいない職員です")
        if m["role"] not in staffmod.vacancies(team):
            raise ValueError(f"{staffmod.default_settings().label(m['role'])}は空いていません(先に解任してください)")
        room = self._staff_room(team)
        if room is not None and int(m["salary"]) > room:
            raise ValueError(f"年俸に使える額が足りません(残り {room:,} 万円)")
        if staff_id not in proc.staff_offers:
            proc.staff_offers.append(staff_id)
        self.dirty = True
        return self.offseason_view()

    def offseason_staff_cancel(self, staff_id: str) -> dict:
        proc = self._staff_stage()
        proc.staff_offers = [x for x in proc.staff_offers if x != staff_id]
        self.dirty = True
        return self.offseason_view()

    def offseason_staff_dismiss(self, role: str) -> dict:
        """自球団の職員を解任する(残りの年俸は払う。市場に戻る。D-475)。"""
        proc = self._staff_stage()
        team = self._my_team()
        if role not in (team.staff or {}):
            raise ValueError("その役割の職員はいません")
        staffmod.dismiss(self.state.league, team, role, proc.year + 1)
        proc._cache.clear()  # スカウトが代わると評価が変わる
        self.dirty = True
        return self.offseason_view()

    def offseason_staff_close(self) -> dict:
        """職員の市場を締める(あなたの希望と AI の希望で、前年の順位が低い球団から 1 人ずつ)。次の段階へは進めない。"""
        self._close_staff(my_ai=False)
        self.dirty = True
        view = self.offseason_view()
        view["last_staff"] = view.get("staff")
        return view

    def _close_staff(self, my_ai: bool) -> None:
        state = self.state
        proc = self._proc()
        if proc.staff_done:
            return
        staffmod.close_stage(state.league, proc, state.team_funds, state.facility_settings, state.my_team_id, my_ai, state.name_parts)

    # ---- チームのページの職員(D-476) ----

    def team_staff(self, team_id: str) -> dict:
        """チームのページの「職員」の欄:今の職員、あなたが決めたか、役割ごとの入れ替えの候補(職員の市場)。"""
        state = self.state
        s = staffmod.default_settings()
        team = self._team(team_id)
        mine = team_id == state.my_team_id
        room = self._staff_room(team) if mine else None
        cands = {r: [] for r in staffmod.ROLES}
        for m in state.league.staff_free:
            if mine and room is not None and int(m["salary"]) > room:
                continue  # 自球団は資金に収まる候補だけ(今の職員の残りの年俸は払うので、空く額はない)
            cands[m["role"]].append(self._staff_public(m))
        order = {r: i for i, r in enumerate(staffmod.ROLES)}
        for r in cands:
            cands[r].sort(key=lambda x: ("SABCDE".index(x["rank"]), x["salary"], x["staff_id"]))
        return {
            "team_id": team_id, "is_mine": mine, "manual": bool(team.staff_manual) and not mine,
            "staff": self._staff_table(team),
            "roles": [{"key": r, "label": s.label(r), "candidates": cands[r]} for r in sorted(staffmod.ROLES, key=order.get)],
            "dead": [{"name": d["name"], "role_label": s.label(d["role"]), "salary_text": f"{int(d['salary']):,} 万円", "until": int(d["until"])} for d in (team.staff_dead or []) if int(d["until"]) >= self._staff_season()],
            "source_text": "あなたが決めた職員(「AI に戻す」まで、AI は監督を代えません)" if team.staff_manual and not mine else ("あなたの球団の職員" if mine else "AI が雇った職員"),
        }

    def staff_swap(self, team_id: str, role: str, staff_id: str) -> dict:
        """職員を入れ替える(どの球団でも、いつでも。今の職員は解任の扱いで市場へ。D-476)。他球団は「あなたが決めた」になる。"""
        state = self.state
        team = self._team(team_id)
        if role not in staffmod.ROLES:
            raise ValueError(f"知らない役割です({role})")
        m = next((x for x in state.league.staff_free if x["id"] == staff_id), None)
        if m is None or m["role"] != role:
            raise ValueError("その役割の職員の市場の候補を選んでください")
        proc = state.procedure
        if proc is not None and proc.phase == "staff" and not proc.staff_done and staff_id in proc.staff_offers:
            proc.staff_offers.remove(staff_id)
        season = self._staff_season()
        if team_id == state.my_team_id:
            room = self._staff_room(team)
            if room is not None and int(m["salary"]) > room:
                raise ValueError(f"年俸に使える額が足りません(残り {room:,} 万円。今の職員の残りの年俸は払います)")
        staffmod.swap(state.league, team, role, staff_id, season)
        if team_id != state.my_team_id:
            team.staff_manual = True
        if proc is not None:
            proc._cache.clear()  # スカウトが代わると評価が変わる
        self.dirty = True
        return self.team_staff(team_id)

    def staff_reset(self, team_id: str) -> dict:
        """AI に戻す:「あなたが決めた」の印を消す。次のオフの職員の段階から、AI がふつうに雇う・監督を代える(今の職員はそのまま)。"""
        team = self._team(team_id)
        team.staff_manual = False
        self.dirty = True
        return self.team_staff(team_id)

    # ---- 記録室の監督(D-477) ----

    def record_managers(self) -> dict:
        """監督ごとの通算(球団・シーズン・勝・敗・分・勝率・優勝・日本一)と、コーチ・スカウトの所属の履歴。担当の記録と履歴から。"""
        state = self.state
        s = staffmod.default_settings()
        names = self._team_names()
        archives = {a.year: a for a in state.history}
        rows: dict[str, dict] = {}
        others = []
        for x in state.league.staff_log:
            if x["role"] != "manager":
                others.append({"year": x["year"], "team_id": x["team_id"], "team_name": names.get(x["team_id"], ""), "role_label": s.label(x["role"]), "name": x["name"], "staff_id": x["staff_id"], "former": bool(x.get("player_id")), "is_mine": x["team_id"] == state.my_team_id})
                continue
            a = archives.get(x["year"])
            r = rows.setdefault(x["staff_id"], {"staff_id": x["staff_id"], "name": x["name"], "former": bool(x.get("player_id")), "teams": [], "seasons": 0, "wins": 0, "losses": 0, "ties": 0, "pennants": 0, "japan": 0, "last_year": 0, "is_mine": False})
            r["seasons"] += 1
            if names.get(x["team_id"]) and names[x["team_id"]] not in r["teams"]:
                r["teams"].append(names[x["team_id"]])
            r["last_year"] = max(r["last_year"], int(x["year"]))
            r["is_mine"] = r["is_mine"] or x["team_id"] == state.my_team_id
            if a is None:
                continue
            st = next((st for st in a.standings if st.team_id == x["team_id"]), None)
            if st is not None:
                r["wins"] += st.wins
                r["losses"] += st.losses
                r["ties"] += st.ties
                if st.rank == 1:
                    r["pennants"] += 1
            if a.postseason is not None and a.postseason.get("champion") == x["team_id"]:
                r["japan"] += 1
        out = []
        for r in rows.values():
            games = r["wins"] + r["losses"]
            out.append({**r, "team_text": "・".join(r["teams"]), "pct": f"{r['wins'] / games:.3f}".lstrip("0") if games else "-"})
        out.sort(key=lambda r: (-r["wins"], -r["seasons"], r["staff_id"]))
        others.sort(key=lambda r: (-r["year"], r["team_id"], r["role_label"]))
        return {"managers": out, "others": others, "years": sorted({x["year"] for x in state.league.staff_log})}
