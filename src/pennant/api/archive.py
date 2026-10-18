"""画面から呼ぶ操作の関数のうち、記録室(D-422、D-423):歴代の順位と優勝・年ごとのタイトル・通算記録・補強の出入り。
計算本体は読むだけで、保存形式・指紋は変えない。"""

from __future__ import annotations

from fractions import Fraction

from ..abilities import POSITION_LABELS
from ..records import qualified_batters, qualified_pitchers

# 年ごとのタイトル(D-423):キー・名前・役割・規定が要るか・小さいほど良いか
TITLES = (
    ("AVG", "首位打者", "batter", True, False),
    ("OBP", "最高出塁率", "batter", True, False),
    ("HR", "本塁打王", "batter", False, False),
    ("RBI", "打点王", "batter", False, False),
    ("H", "最多安打", "batter", False, False),
    ("SB", "盗塁王", "batter", False, False),  # D-432
    ("W", "最多勝", "pitcher", False, False),
    ("ERA", "最優秀防御率", "pitcher", True, True),
    ("SO", "最多奪三振", "pitcher", False, False),
    ("SV", "最多セーブ", "pitcher", False, False),
    ("HLD", "最多ホールド", "pitcher", False, False),
)

# 通算記録(D-423):役割 → [(キー, 名前)]。war は WAR(投手は失点版)
LEADER_KEYS = {
    "batter": (("H", "安打"), ("HR", "本塁打"), ("RBI", "打点"), ("SB", "盗塁"), ("G", "試合"), ("war", "WAR")),
    "pitcher": (("W", "勝利"), ("SV", "セーブ"), ("SO", "奪三振"), ("HLD", "ホールド"), ("war", "WAR")),
}
LEADERS_TOP = 20

# 出入りの種類(D-423)
MOVE_LABELS = {"fa": "FA", "trade": "トレード", "market": "市場", "minimum": "市場(最低人数)", "active_draft": "現役ドラフト", "release": "自由契約", "promote": "支配下登録", "retire": "引退"}
MOVE_ORDER = {k: i for i, k in enumerate(("trade", "release", "fa", "market", "minimum", "active_draft", "promote", "retire"))}
ENTRY_KINDS = {"draft": ("draft", "dev_draft"), "foreign": ("foreign",)}


def _rate(c, key: str) -> Fraction | None:
    """率の値(分母が 0 なら None)。"""
    if key == "AVG":
        return Fraction(c.get("H", 0), c["AB"]) if c.get("AB") else None
    if key == "OBP":
        den = c.get("AB", 0) + c.get("BB", 0) + c.get("HBP", 0) + c.get("SF", 0)
        return Fraction(c.get("H", 0) + c.get("BB", 0) + c.get("HBP", 0), den) if den else None
    if key == "ERA":
        return Fraction(c.get("ER", 0) * 27, c["OUTS"]) if c.get("OUTS") else None
    return Fraction(int(c.get(key, 0)))


def _text(key: str, v: Fraction) -> str:
    if key in ("AVG", "OBP"):
        s = f"{float(v):.3f}"
        return s[1:] if s.startswith("0.") else s
    if key == "ERA":
        return f"{float(v):.2f}"
    return str(int(v))


class ArchiveMixin:
    """Game の一部:記録室(歴代の順位と優勝・年ごとのタイトル・通算記録・補強の出入り)。"""

    def record_years(self) -> list[int]:
        """終わったシーズン(年度を確定したシーズン)の番号。"""
        return [a.year for a in self.state.history]

    def _archive(self, year: int | None):
        years = self.record_years()
        if not years:
            return None
        if year is None:
            year = years[-1]
        a = next((x for x in self.state.history if x.year == int(year)), None)
        if a is None:
            raise ValueError(f"{year} シーズン目の記録はありません(選べるのは終わったシーズン)")
        return a

    def _league_of(self) -> dict[str, int]:
        return {t.id: t.league_index for t in self.state.league.teams}

    def record_standings(self, year: int | None = None) -> dict:
        """歴代の順位と優勝(公開用。D-423):選んだシーズンのリーグごとの順位表と、球団ごとの優勝回数。"""
        state = self.state
        names = self._team_names()
        lnames = state.league.league_names
        out = {"years": self.record_years(), "year": None, "leagues": [], "champions": [], "interleague": None, "allstar": []}
        a = self._archive(year)
        if a is None:
            return out
        out["year"] = a.year
        for i in sorted({s.league_index for s in a.standings}):
            rows = []
            for s in sorted((s for s in a.standings if s.league_index == i), key=lambda s: s.rank):
                games = s.wins + s.losses
                gb = "-" if s.games_behind == 0 else (f"{float(s.games_behind):.1f}".rstrip("0").rstrip("."))
                rows.append({"rank": s.rank, "team_id": s.team_id, "team_name": names.get(s.team_id, s.team_id), "wins": s.wins, "losses": s.losses, "ties": s.ties,
                             "pct": f"{s.wins / games:.3f}".lstrip("0") if games else "-", "games_behind": gb, "champion": s.team_id in a.champions.get(i, []), "is_mine": s.team_id == state.my_team_id})
            out["leagues"].append({"league_index": i, "league_name": lnames[i] if i < len(lnames) else f"リーグ {i + 1}", "rows": rows})
        if a.interleague is not None:  # 交流戦の優勝(D-437)
            il = a.interleague
            out["interleague"] = {
                "champion": names.get(il["champion"], il["champion"]),
                "is_mine": il["champion"] == state.my_team_id,
                "rows": [{"rank": k + 1, "team_name": names.get(x["team_id"], x["team_id"]), "wins": x["wins"], "losses": x["losses"], "ties": x["ties"], "is_mine": x["team_id"] == state.my_team_id} for k, x in enumerate(il["rows"])],
            }
        out["allstar"] = self.allstar_results(a.allstar)  # オールスター(D-436)
        count: dict[str, list[int]] = {}
        il_count: dict[str, list[int]] = {}
        for x in state.history:
            for tids in x.champions.values():
                for tid in tids:
                    count.setdefault(tid, []).append(x.year)
            if x.interleague is not None:
                il_count.setdefault(x.interleague["champion"], []).append(x.year)
        league_of = self._league_of()
        out["champions"] = sorted(
            ({"team_id": t.id, "team_name": t.name, "league_name": lnames[league_of[t.id]] if league_of[t.id] < len(lnames) else "", "count": len(count.get(t.id, [])), "years": count.get(t.id, []), "interleague_years": il_count.get(t.id, []), "is_mine": t.id == state.my_team_id} for t in state.league.teams),
            key=lambda r: (-r["count"], league_of[r["team_id"]], r["team_id"]),
        )
        return out

    def record_titles(self, year: int | None = None) -> dict:
        """年ごとのタイトル(公開用。D-423):リーグごとに打者 6 つ(盗塁王を含む。D-432)・投手 5 つ。率は規定に届いた選手だけ。同じ値は全員。"""
        state = self.state
        names = self._team_names()
        lnames = state.league.league_names
        out = {"years": self.record_years(), "year": None, "leagues": []}
        a = self._archive(year)
        if a is None:
            return out
        out["year"] = a.year
        rec = a.records
        league_of = self._league_of()
        qualified = {"batter": set(qualified_batters(rec)), "pitcher": set(qualified_pitchers(rec))}
        for i in sorted(set(league_of.values())):
            titles = []
            for key, label, role, needs_qualify, low in TITLES:
                group, owner = (rec.batters, rec.batter_team) if role == "batter" else (rec.pitchers, rec.pitcher_team)
                cands = []
                for pid, c in group.items():
                    if league_of.get(owner.get(pid)) != i or (needs_qualify and pid not in qualified[role]):
                        continue
                    v = _rate(c, key)
                    if v is not None:
                        cands.append((v, pid))
                best = (min if low else max)((v for v, _ in cands), default=None)
                if best is None or (not needs_qualify and best == 0):
                    titles.append({"key": key, "label": label, "role": role, "value": None, "value_text": "該当なし", "players": []})
                    continue
                winners = sorted(pid for v, pid in cands if v == best)
                titles.append({"key": key, "label": label, "role": role, "value": float(best), "value_text": _text(key, best),
                               "players": [{"player_id": pid, "name": a.players.get(pid, {}).get("name", pid), "team_id": owner[pid], "team_name": names.get(owner[pid], ""), "is_mine": owner[pid] == state.my_team_id} for pid in winners]})
            out["leagues"].append({"league_index": i, "league_name": lnames[i] if i < len(lnames) else f"リーグ {i + 1}", "titles": titles})
        return out

    def record_leaders(self, role: str = "batter", key: str | None = None) -> dict:
        """通算記録のランキング(公開用。D-423):今シーズンまで(進行中を含む)の上位 20 人。引退した選手も含む。"""
        if role not in LEADER_KEYS:
            raise ValueError(f"打者か投手を選んでください(値: {role!r})")
        keys = dict(LEADER_KEYS[role])
        if key is None:
            key = LEADER_KEYS[role][0][0]
        if key not in keys:
            raise ValueError(f"この記録は選べません(値: {key!r})")
        state = self.state
        totals: dict[str, float] = {}
        seasons: dict[str, int] = {}
        last_name: dict[str, str] = {}

        def add(rec, war, players):
            group = rec.batters if role == "batter" else rec.pitchers
            for pid, c in group.items():
                if key != "war":
                    totals[pid] = totals.get(pid, 0) + int(c.get(key, 0))
                seasons[pid] = seasons.get(pid, 0) + 1
                if pid in players:
                    last_name[pid] = players[pid]
            if key == "war":
                for pid, line in war.items():
                    if line.role == role:
                        totals[pid] = totals.get(pid, 0.0) + float(line.war if role == "batter" else line.war_ra)

        for a in state.history:
            add(a.records, a.war, {pid: x["name"] for pid, x in a.players.items()})
        add(self.records.total, self.war_lines(), {})
        current = {p.id: p for t in state.league.teams for p in t.players + t.development}
        names = self._team_names()
        ranked = sorted(((v, pid) for pid, v in totals.items() if v > 0), key=lambda x: (-x[0], x[1]))
        rows = []
        for n, (v, pid) in enumerate(ranked):
            if n >= LEADERS_TOP and v != ranked[LEADERS_TOP - 1][0]:
                break
            rank = 1 + sum(1 for w, _ in ranked if w > v)
            p = current.get(pid)
            rows.append({"rank": rank, "player_id": pid, "name": p.name if p else last_name.get(pid, pid), "value": v, "value_text": f"{v:.1f}" if key == "war" else str(int(v)),
                         "seasons": seasons.get(pid, 0), "team_name": names.get(p.team_id, "") if p and p.team_id else "引退", "active": p is not None, "is_mine": p is not None and p.team_id == state.my_team_id})
        return {"role": role, "key": key, "label": keys[key], "keys": [{"key": k, "label": v} for k, v in LEADER_KEYS[role]], "rows": rows}

    def record_entries(self, kind: str = "draft", team_id: str | None = None, year: int | None = None) -> dict:
        """補強の記録の当たり・外れ(公開用。D-423):ドラフト(育成ドラフトを含む)か外国人で入った選手。中身はドラフトの振り返りと同じ形。"""
        if kind not in ENTRY_KINDS:
            raise ValueError(f"ドラフトか外国人を選んでください(値: {kind!r})")
        out = self.draft_review(team_id, year, ENTRY_KINDS[kind])
        out["kind"] = kind
        return out

    def record_moves(self, year: int | None = None, team_id: str | None = None) -> dict:
        """補強の記録の出入り(公開用。D-423):オフ(とシーズン中のトレード)の FA・トレード・市場・現役ドラフト・自由契約・支配下登録・引退。"""
        state = self.state
        names = self._team_names()
        years = sorted({int(x["year"]) for x in state.transactions} | {o.year for o in state.offseasons})
        if team_id is not None and team_id not in names:
            raise ValueError(f"球団 '{team_id}' はありません")
        out = {"years": years, "year": None, "team_id": team_id, "teams": [{"team_id": t.id, "team_name": t.name, "is_mine": t.id == state.my_team_id} for t in state.league.teams], "rows": []}
        if not years:
            return out
        if year is None:  # 初期値は、最後に終えたオフの年(なければ最新の年)
            year = state.offseasons[-1].year if state.offseasons else years[-1]
        elif int(year) not in years:
            raise ValueError(f"{year} シーズン目の出入りの記録はありません")
        year = int(year)
        out["year"] = year
        released = {x["player_id"]: x["team_id"] for x in state.transactions if int(x["year"]) == year and x["phase"] == "release"}
        rows = []
        for x in state.transactions:
            if int(x["year"]) != year or x["phase"] not in MOVE_LABELS or not x.get("player_id"):
                continue
            phase = x["phase"]
            if phase == "fa":
                frm, to = x.get("former_team"), x.get("team_id")
                label = "FA 残留" if frm == to else "FA 移籍"
            elif phase in ("trade", "active_draft"):
                frm, to, label = x.get("from_team"), x.get("team_id"), MOVE_LABELS[phase] + (f"({x['day']}日目)" if phase == "trade" and x.get("day") else "")
            elif phase in ("market", "minimum"):
                frm, to, label = released.get(x["player_id"]), x.get("team_id"), MOVE_LABELS[phase]
            elif phase == "release":
                frm, to, label = x.get("team_id"), None, MOVE_LABELS[phase]
            else:  # 支配下登録
                frm, to, label = x.get("team_id"), x.get("team_id"), MOVE_LABELS[phase]
            rows.append((phase, frm, to, label, x))
        off = next((o for o in state.offseasons if o.year == year), None)
        for n in (off.retired if off else []):
            rows.append(("retire", n.team_id, None, MOVE_LABELS["retire"], {"player_id": n.player_id, "name": n.name, "position": n.position, "age": n.age}))
        if team_id is not None:
            rows = [r for r in rows if team_id in (r[1], r[2])]
        current = {p.id for t in state.league.teams for p in t.players + t.development}
        retired_any = {pid for a in state.history for pid in a.players}
        out["rows"] = [
            {"kind": phase, "label": label, "player_id": x["player_id"], "name": x["name"], "position_label": POSITION_LABELS.get(x.get("position"), ""), "age": x.get("age"),
             "from_team_name": names.get(frm, "") if frm else "—", "to_team_name": names.get(to, "") if to else "—", "linkable": x["player_id"] in current or x["player_id"] in retired_any,
             "is_mine": state.my_team_id in (frm, to)}
            for phase, frm, to, label, x in sorted(rows, key=lambda r: (MOVE_ORDER[r[0]], r[4].get("day") or 0, r[1] or "", r[4]["player_id"]))
        ]
        return out
