"""画面から呼ぶ操作の関数のうち、ドラフトと市場(指名・獲得・手放す選手・成績つきの候補の表・ドラフトの振り返り・入退団の記録)。"""

from __future__ import annotations

from .. import draft as draftmod
from ..abilities import POSITION_LABELS
from ..draft import PHASE_LABELS, stage_of
from .common import ROLE_LABELS


class DraftMixin:
    """Game の一部:ドラフトと市場(指名・獲得・手放す選手・成績つきの候補の表・ドラフトの振り返り・入退団の記録)。"""

    def offseason_release(self, player_ids: list[str]) -> dict:
        """操作する球団が選手を手放す(自由契約の段階)。最低人数を割る選び方は受け付けない(警告は画面側)。"""
        proc = self._proc()
        if stage_of(proc.phase) != "contract":
            raise ValueError("今は契約の段階ではありません")
        team = self._my_team()
        mins, min_batters = self._mins()
        ids = [str(x) for x in player_ids]
        pending = [pid for pid in ids if pid in proc.negotiations and proc.negotiations[pid]["team_id"] == team.id and proc.negotiations[pid]["status"] == "pending"]
        if pending:
            raise ValueError("更改の交渉中の選手は、提示のパネルの「自由契約にする」で手放してください")
        chosen = [p for p in team.players if p.id in ids]
        if len(chosen) != len(set(ids)):
            raise ValueError("自分の球団にいない選手が含まれています")
        rest = [p for p in team.players if p.id not in ids]
        worse = draftmod.new_shortages(team.players, rest, mins, min_batters)
        if worse and not self._over_hard_cap(team):  # 予算の上限を超えている間は、最低人数を割っても外せる(不足は完了のときに最低年俸で自動補充。F3-2b)
            raise ValueError("最低人数を割ってしまいます:" + "、".join(f"{'野手' if pos == 'batter' else POSITION_LABELS[pos]} があと {n} 人足りなくなります" for pos, n in worse.items()))
        draftmod.release_players(team, chosen, proc)
        proc.my_release_done = True
        self.dirty = True
        return self.offseason_view()

    def offseason_advance(self) -> dict:
        """AI の番を、自分の番か段階の終わりまで進める。"""
        state = self.state
        proc = self._proc()
        if proc.phase not in ("draft", "market"):
            raise ValueError("今は指名の段階ではありません")
        mins, min_batters = self._mins()
        draftmod.run_ai_turns(state.league, proc, state.scout_sd_map(), state.draft_settings, state.my_team_id, mins, min_batters, ctx=self._contract_ctx())
        self.dirty = True
        return self.offseason_view()

    def offseason_pick(self, player_id: str) -> dict:
        """自分の番に選手を指名(獲得)し、次の自分の番まで AI を進める。"""
        state = self.state
        proc = self._proc()
        team = self._my_team()
        mins, min_batters = self._mins()
        sd = state.scout_sd_map()
        if proc.phase not in ("draft", "market") or proc.current_team() != team.id or draftmod.phase_finished(proc):
            raise ValueError("今は自分の番ではありません(「次の自分の番まで進める」を押してください)")
        player = next((p for p in draftmod.pool_of(proc) if p.id == player_id), None)
        if player is None:
            raise ValueError("その選手は一覧にいません")
        if len(team.players) >= draftmod.MAX_ROSTER:
            raise ValueError("空き枠がありません(70 人)")
        ctx = self._contract_ctx()
        offer = draftmod.offer_for(team, player, proc, ctx)
        if offer is not None and not draftmod.can_afford(team, offer[0], ctx):
            raise ValueError(f"この契約(年俸 {offer[0]:,} 万円)を結ぶと、予算の上限を超えます")
        draftmod.take(proc, team, player, sd, state.draft_settings, ctx)
        draftmod.run_ai_turns(state.league, proc, sd, state.draft_settings, team.id, mins, min_batters, ctx=ctx)
        self.dirty = True
        return self.offseason_view()

    def offseason_pass(self) -> dict:
        state = self.state
        proc = self._proc()
        team = self._my_team()
        mins, min_batters = self._mins()
        if proc.phase not in ("draft", "market") or proc.current_team() != team.id or draftmod.phase_finished(proc):
            raise ValueError("今は自分の番ではありません")
        draftmod.pass_turn(proc, team.id, "pass")
        draftmod.run_ai_turns(state.league, proc, state.scout_sd_map(), state.draft_settings, team.id, mins, min_batters, ctx=self._contract_ctx())
        self.dirty = True
        return self.offseason_view()

    def offseason_players(self, phase: str) -> list:
        """手続きの画面の表に出す選手:自由契約は自球団の全選手、市場は市場の選手、ドラフトは候補。"""
        proc = self._proc()
        if phase == "renewal":
            ids = {e["player_id"] for e in proc.negotiations.values() if e["team_id"] == self.state.my_team_id and e["status"] != "released"}
            return [p for p in self._my_team().players if p.id in ids]
        if phase == "fa":
            return list(proc.fa_pool) + [p for t in self.state.league.teams for p in t.players if p.id in proc.fa_info] + [p for p in proc.market if p.id in proc.fa_info]
        if phase == "release":
            return list(self._my_team().players)
        if phase == "market":
            return list(proc.market)
        if phase == "draft":
            return list(proc.candidates)
        raise ValueError(f"自由契約か市場を選んでください(値: {phase!r})")

    def offseason_table(self, phase: str, role: str = "batter", kind: str = "basic", sort: str | None = None, order: str | None = None, season: str | None = None) -> dict:
        """自由契約・市場の画面の、成績つきの選手の一覧(公開用。D-222)。市場の行には前の球団を足す。"""
        proc = self._proc()
        if phase == "renewal":
            return self._renewal_table(role, kind, sort, order, season)
        if phase == "fa":
            return self._fa_table(role, kind, sort, order, season)
        table = self.roster_table(self.offseason_players(phase), role, kind, sort, order, season)
        if phase != "release":
            names = self._team_names()
            former = {x["player_id"]: names.get(x["team_id"], "") for x in proc.released}
            former.update({pid: names.get(x["former_team"], "") for pid, x in proc.fa_info.items()})
            ctx = self._contract_ctx()
            my = self.state.my_team_id
            team = self._team(my) if my else None
            pool = {p.id: p for p in self.offseason_players(phase)}
            for r in table["rows"]:
                r["former_team"] = former.get(r["player_id"], "")
                if team is not None:  # 自球団が獲得したときの契約(ドラフトは巡ごとの表、市場は算定)
                    offer = draftmod.offer_for(team, pool[r["player_id"]], proc, ctx)
                    r["offer"] = None if offer is None else {"salary": offer[0], "years": offer[1], "affordable": draftmod.can_afford(team, offer[0], ctx)}
                    r["values"]["salary"] = f"{offer[0]:,}" if offer else "—"
                    r["values"]["years"] = str(offer[1]) if offer else "—"
        else:
            mins, min_batters = self._mins()
            team = self._my_team()
            ok = draftmod.releasable(team.players, mins, min_batters)
            over = self._over_hard_cap(team)
            for r in table["rows"]:
                r["can_release"] = r["player_id"] in ok or over  # 上限を超えている間は、最低人数の選手も外せる
        table["phase"] = phase
        return table

    def review_years(self) -> list[int]:
        """振り返りで選べる入団の年度(入団したシーズンの番号。履歴から。事前運転の入団は含まない)。"""
        return sorted({int(x["year"]) + 1 for x in self.state.transactions if x.get("player_id") and x["phase"] in ("draft", "market", "fill")})

    def _career_totals(self) -> tuple[dict[str, int], dict[str, float]]:
        """選手 ID → 出場(G)の累計、WAR の累計(野手は WAR、投手は失点版)。履歴と今シーズンの合計。"""
        games: dict[str, int] = {}
        war: dict[str, float] = {}

        def add(rec, lines):
            for group in (rec.batters, rec.pitchers):
                for pid, c in group.items():
                    games[pid] = games.get(pid, 0) + int(c.get("G", 0))
            for pid, line in lines.items():
                war[pid] = war.get(pid, 0.0) + float(line.war if line.role == "batter" else line.war_ra)

        for a in self.state.history:
            add(a.records, a.war)
        add(self.records.total, self.war_lines())
        return games, war

    def review_entries(self, team_id: str, year: int) -> list[dict]:
        """振り返りの元になる履歴の行(その年度にその球団に入った選手。経路つき)。"""
        rows = [x for x in self.state.transactions if x.get("player_id") and x["phase"] in ("draft", "market", "fill") and int(x["year"]) + 1 == int(year) and x["team_id"] == team_id]
        order = {"draft": 0, "market": 1, "fill": 2}
        return sorted(rows, key=lambda x: (order[x["phase"]], int(x.get("round") or 0), x["player_id"]))

    def draft_review(self, team_id: str | None = None, year: int | None = None) -> dict:
        """ドラフトの振り返り(公開用):入団の経路、入団時の総合の推定値とふれ幅・天井、今の所属、出場と WAR の累計(D-216)。
        真の能力は含めない(答え合わせ用は answers.draft_review_answers)。"""
        state = self.state
        names = self._team_names()
        years = self.review_years()
        teams = [{"team_id": t.id, "team_name": t.name, "is_mine": t.id == state.my_team_id} for t in state.league.teams]
        if team_id is None:
            team_id = state.my_team_id or state.league.teams[0].id
        if team_id not in names:
            raise ValueError(f"球団 '{team_id}' はありません")
        if year is None:
            year = years[-1] if years else None
        elif int(year) not in years:
            raise ValueError(f"{year} シーズン目に入団した選手の履歴はありません")
        out = {"team_id": team_id, "team_name": names[team_id], "year": year, "years": years, "teams": teams, "rows": [], "is_mine": team_id == state.my_team_id, "note": ""}
        if year is None:
            out["note"] = "まだドラフトを行っていません。年度を確定してオフの手続きを終えると、ここに入団した選手の一覧が出ます。"
            return out
        games, war = self._career_totals()
        players = {p.id: p for p in state.league.all_players()}
        rows = []
        for x in self.review_entries(team_id, int(year)):
            p = players.get(x["player_id"])
            sc = p.scouting if p is not None and p.scouting else None
            summary = dict(x)
            if sc and "overall" not in summary:  # 版 8 の履歴には入団時の要約がない。選手が残っていれば評価から引く
                summary.update(draftmod.entry_summary(sc))
            if p is None:
                status, status_label = "left", "引退・退団"
            elif p.team_id == team_id:
                status, status_label = "same", "在籍"
            else:
                status, status_label = "moved", names.get(p.team_id, "")
            rows.append(
                {
                    "player_id": x["player_id"],
                    "name": x["name"],
                    "role": x["role"],
                    "role_label": ROLE_LABELS.get(x["role"], ""),
                    "position": x["position"],
                    "position_label": POSITION_LABELS.get(x["position"], ""),
                    "route": x["phase"],
                    "route_label": f"ドラフト {x['round']} 巡" if x["phase"] == "draft" else ("市場" if x["phase"] == "market" else "自動補充"),
                    "entry_age": x.get("age"),
                    "age": None if p is None else p.age,
                    "entry_overall": summary.get("overall"),
                    "entry_margin": summary.get("margin"),
                    "entry_text": f"{summary['overall']} ± {float(summary['margin']):.0f}" if summary.get("overall") is not None else "-",
                    "entry_ceiling": summary.get("ceiling", "-"),
                    "method": summary.get("method"),
                    "status": status,
                    "status_label": status_label,
                    "games": games.get(x["player_id"], 0),
                    "war": f"{war.get(x['player_id'], 0.0):.1f}",
                    "in_league": p is not None,
                }
            )
        out["rows"] = rows
        out["note"] = f"{year}シーズン目に {names[team_id]} に入った選手({len(rows)}人)。入団時の評価は、そのときの {names[team_id]} のスカウトの推定値 ± ふれ幅と天井(S〜D)。出場と WAR は入団から今までの累計(WAR は野手が WAR、投手が失点版)。答え合わせモードをオンにすると、今の真の総合と、入団時の推定値との差、実際の天井が並びます。"
        return out

    def transactions(self, year: int | None = None) -> dict:
        """指名・獲得・自由契約の履歴(公開用)。"""
        names = self._team_names()
        rows = [x for x in self.state.transactions if year is None or x["year"] == year]
        return {"years": sorted({x["year"] for x in self.state.transactions}), "rows": [{**x, "team_name": names.get(x["team_id"], ""), "position_label": POSITION_LABELS.get(x.get("position", ""), ""), "phase_label": PHASE_LABELS.get(x["phase"], x["phase"]), "is_mine": x["team_id"] == self.state.my_team_id} for x in rows]}
