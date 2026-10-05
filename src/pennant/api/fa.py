"""画面から呼ぶ操作の関数のうち、FA(宣言した選手の表・提示・ラウンドを締める)。"""

from __future__ import annotations

from .. import fa as famod
from ..abilities import POSITION_LABELS
from ..contracts import team_salary
from .common import ROSTER_BASE_COLUMNS


class FaMixin:
    """Game の一部:FA(宣言した選手の表・提示・ラウンドを締める)。"""

    def _fa_row_status(self, pid: str, info: dict, names: dict) -> tuple[int, str]:
        proc = self._proc()
        if info["status"] == "signed":
            return 3, f"契約({names.get(info['team_id'], '')}・{info['years']} 年・{int(info['salary']):,})"
        if info["status"] == "unsigned":
            return 4, "未契約(市場へ)"
        mine = proc.fa_offers.get(pid)
        if mine:
            return 0, f"提示中({mine['years']} 年・{int(mine['salary']):,})"
        return 1, "未契約"

    def _fa_info(self, proc) -> dict:
        """FA の段階の情報(公開用)。"""
        state = self.state
        names = self._team_names()
        fa = famod.fa_settings(state.negotiation_settings)
        my = state.my_team_id
        ctx = self._contract_ctx()
        team = self._team(my) if my else None
        committed = sum(int(o["salary"]) for o in proc.fa_offers.values())
        cap = ctx.cap(my) if my else None
        results = [{**x, "former_team_name": names.get(x["former_team"], ""), "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x["position"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == my or x["former_team"] == my, "stayed": x["team_id"] == x["former_team"]} for x in proc.fa_results]
        counts = {"declared": len(proc.fa_info), "signed": len(proc.fa_results), "open": sum(1 for x in proc.fa_info.values() if x["status"] == "open"), "unsigned": sum(1 for x in proc.fa_info.values() if x["status"] == "unsigned")}
        return {
            "round": min(int(proc.fa_round), int(fa["rounds"])), "rounds": int(fa["rounds"]), "done": bool(proc.fa_done), "counts": counts,
            "offers": [{"player_id": pid, "name": proc.fa_info[pid]["name"], "years": o["years"], "salary": o["salary"], "salary_text": f"{int(o['salary']):,} 万円"} for pid, o in proc.fa_offers.items()],
            "committed": committed, "committed_text": f"{committed:,} 万円",
            "space": None if team is None else famod.MAX_ROSTER - len(team.players) - len(proc.fa_offers),
            "total": None if team is None else team_salary(team), "cap": cap, "cap_text": None if cap is None else f"{cap:,} 万円", "hard": ctx.hard(),
            "salary_editable": True, "none_max_ratio": float(state.negotiation_settings.money_none["max_ratio"]) if state.money_rule == "none" else None,
            "max_years": state.contract_settings.max_years, "min_years": state.contract_settings.min_years, "minimum_salary": state.contract_settings.minimum, "rounding": state.contract_settings.rounding,
            "results": results,
            "note": f"FA を宣言した選手に、年数(1〜5 年)" + ("と年俸" if state.money_rule != "none" else "") + f"を提示できます。全 {int(fa['rounds'])} ラウンドで、各ラウンドの終わりに選手が受けた提示の中から志望(年俸・出場機会・勝利。出場機会と勝利は提示した球団での見込み)で一番よいものを選びます。"
            + "決まらなければ次のラウンドへ、最後のラウンドでも決まらなければ自由契約市場に回ります。元の球団も同じ立場で提示します。補償はありません。"
            + (f"お金のルール「なし」では年俸は算定の 1.0〜{float(state.negotiation_settings.money_none['max_ratio']):g} 倍です。" if state.money_rule == "none" else "")
            + ("標準以上では、総年俸と提示中の年俸の合計が予算の上限を超える提示はできません。" if ctx.hard() else "")
            + "「ラウンドを締める」で、AI 球団の提示と合わせて結果が出ます。「次の手続きへ」「おまかせ」は、残りのラウンドを AI と同じ方針で進めます。",
        }

    def _fa_table(self, role: str, kind: str, sort: str | None, order: str | None, season: str | None) -> dict:
        proc = self._proc()
        names = self._team_names()
        cols = [
            ROSTER_BASE_COLUMNS[role][0], ROSTER_BASE_COLUMNS[role][1], ROSTER_BASE_COLUMNS[role][2],
            {"key": "former", "label": "前の所属", "type": "text", "better": "low"},
            {"key": "calc", "label": "算定年俸", "type": "count", "better": "high"},
            {"key": "status", "label": "状態", "type": "text", "better": "low"},
        ]
        players = self.offseason_players("fa")
        extra = {}
        for p in players:
            info = proc.fa_info[p.id]
            o, status = self._fa_row_status(p.id, info, names)
            extra[p.id] = {"former": (names.get(info["former_team"], ""), names.get(info["former_team"], "")), "calc": (int(info["calc_salary"]), f"{int(info['calc_salary']):,}"), "status": (o, status)}
        if not sort:
            sort, order = "calc", "desc"
        table = self.roster_table(players, role, kind, sort, order, season, base_columns=cols, extra_values=extra)
        for r in table["rows"]:
            info = proc.fa_info[r["player_id"]]
            mine = proc.fa_offers.get(r["player_id"])
            r["fa"] = {"status": info["status"], "former_team": info["former_team"], "former_team_name": names.get(info["former_team"], ""), "calc_salary": int(info["calc_salary"]), "calc_text": f"{int(info['calc_salary']):,} 万円", "expected": round(float(info["expected"]), 2), "offer": mine}
        table["phase"] = "fa"
        return table

    def _fa_open_player(self, player_id: str):
        proc = self._proc()
        if proc.phase != "fa" or proc.fa_done:
            raise ValueError("今は FA の段階ではありません")
        info = proc.fa_info.get(str(player_id))
        if info is None or info["status"] != "open":
            raise ValueError("その選手は、FA で交渉できる選手ではありません")
        return proc, info

    def offseason_fa_offer(self, player_id: str, years: int, salary: int | None = None) -> dict:
        """あなたの球団の FA の提示(今のラウンド。出し直しは上書き)。"""
        state = self.state
        proc, info = self._fa_open_player(player_id)
        team = self._my_team()
        cs = state.contract_settings
        try:
            years = int(years)
        except (TypeError, ValueError):
            raise ValueError("年数は整数で入れてください") from None
        if not cs.min_years <= years <= cs.max_years:
            raise ValueError(f"年数は {cs.min_years}〜{cs.max_years} 年にしてください(値: {years})")
        calc = int(info["calc_salary"])
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
        others = {pid: o for pid, o in proc.fa_offers.items() if pid != str(player_id)}
        if len(team.players) + len(others) + 1 > famod.MAX_ROSTER:
            raise ValueError("空き枠が足りません(70 人。提示中の選手も数えます)")
        ctx = self._contract_ctx()
        cap = ctx.cap(team.id)
        if ctx.hard() and cap is not None and team_salary(team) + sum(int(o["salary"]) for o in others.values()) + salary > cap:
            room = cap - team_salary(team) - sum(int(o["salary"]) for o in others.values())
            raise ValueError(f"総年俸と提示中の年俸の合計が予算の上限を超えます(この選手に出せるのは {max(0, room):,} 万円まで)")
        proc.fa_offers[str(player_id)] = {"years": years, "salary": int(salary)}
        self.dirty = True
        return self.offseason_view()

    def offseason_fa_cancel(self, player_id: str) -> dict:
        proc, _ = self._fa_open_player(player_id)
        proc.fa_offers.pop(str(player_id), None)
        self.dirty = True
        return self.offseason_view()

    def offseason_fa_close(self) -> dict:
        """ラウンドを締める:AI の提示と合わせて、選手が選ぶ。戻り値は画面の情報と、このラウンドの結果。"""
        proc = self._proc()
        if proc.phase != "fa" or proc.fa_done:
            raise ValueError("今は FA の段階ではありません")
        rnd = proc.fa_round
        signed = famod.close_round(self.state.league, proc, self._contract_ctx(), self.state.my_team_id)
        self.dirty = True
        view = self.offseason_view()
        names = self._team_names()
        view["last_round"] = {"round": rnd, "signed": [{**x, "team_name": names.get(x["team_id"], ""), "former_team_name": names.get(x["former_team"], ""), "salary_text": f"{int(x['salary']):,} 万円", "is_mine": x["team_id"] == self.state.my_team_id} for x in signed]}
        return view
