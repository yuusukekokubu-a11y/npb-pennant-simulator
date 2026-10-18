"""表彰(D-442、D-443)。

年度の確定のときに、そのシーズンの集計(履歴に残す元の数・WAR)から決まった式で選ぶ。記録だけで、ほかには影響させない。
  - リーグごと:MVP・新人王・ベストナイン・ベストバッター賞・ベストディフェンス賞・最優秀中継ぎ投手賞・最優秀救援投手賞。
  - 両リーグで 1 人:最優秀先発投手賞。
  - 同じ値なら WAR の大きい方、それでも同じなら選手の番号の順。
ポジションは、そのシーズンに最も多くスタメンで守った位置(指名打者はスタメンの試合からポジションの試合を引いた数)。
外野(左翼・中堅・右翼)はまとめて 3 人。
"""

from __future__ import annotations

from fractions import Fraction

from .records import qualifying_outs, qualifying_plate_appearances

OUTFIELD = ("LF", "CF", "RF")
NINE = ("P", "C", "1B", "2B", "3B", "SS", "OF", "DH")  # ベストナインの枠(外野は 3 人)
SLOT_LABELS = {"P": "投手", "C": "捕手", "1B": "一塁手", "2B": "二塁手", "3B": "三塁手", "SS": "遊撃手", "OF": "外野手", "DH": "指名打者"}
AWARD_LABELS = {
    "mvp": "MVP",
    "rookie": "新人王",
    "best_nine": "ベストナイン",
    "best_batter": "ベストバッター賞",
    "best_defense": "ベストディフェンス賞",
    "best_setup": "最優秀中継ぎ投手賞",
    "best_closer": "最優秀救援投手賞",
    "best_starter": "最優秀先発投手賞",
}
ENTRY_AGE = {"high_school": 18, "college": 22, "corporate": 24}  # 新人王の入団年数の見積もり(D-443)
ROOKIE_YEARS = 5
ROOKIE_PA = 60
ROOKIE_OUTS = 90


def _slot(pos: str) -> str:
    return "OF" if pos in OUTFIELD else pos


def primary_slot(rec, pid: str) -> str | None:
    """最も多くスタメンで守った枠(外野はまとめる。指名打者はスタメンの試合 − ポジションの試合)。"""
    games: dict[str, int] = {}
    for pos, c in (rec.fielders.get(pid) or {}).items():
        games[_slot(pos)] = games.get(_slot(pos), 0) + int(c["G"])
    starts = int(rec.batters.get(pid, {}).get("G", 0))
    dh = starts - sum(games.values())
    if dh > 0:
        games["DH"] = dh
    if not games:
        return None
    return max(games, key=lambda k: (games[k], -NINE.index(k)))


def _pick(cands, n: int = 1) -> list:
    """(値, WAR, 選手 ID) の大きい順(番号は小さい順)に n 人。"""
    return sorted(cands, key=lambda x: (-x[0], -x[1], x[2]))[:n]


def compute(archive, team_league: dict[str, int], players: dict, prior_archives: list, prior_registered: dict[str, int]) -> list[dict]:
    """表彰の一覧 [{key, label, league(None は両リーグ), slot, player_id, name, team_id, value}]。
    players は選手 ID → Player(外国人・出身・年齢を見る)。prior_registered は今シーズンの前までの一軍登録のシーズン数。"""
    rec = archive.records
    war = archive.war
    info = archive.players
    league_of = {pid: team_league.get(info[pid]["team_id"]) for pid in info if info[pid].get("team_id") in team_league}
    w = lambda pid: (war[pid].war if war[pid].role == "batter" else war[pid].war_ra) if pid in war else Fraction(0)  # 投手は失点版の WAR(通算記録と同じ)
    team_games = {tid: int(c["G"]) for tid, c in rec.teams.items()}
    games_of = lambda pid: team_games.get(info[pid]["team_id"], 0)
    out: list[dict] = []

    def add(key: str, league, pid: str, value: str, slot: str | None = None) -> None:
        out.append({"key": key, "label": AWARD_LABELS[key], "league": league, "slot": slot, "player_id": pid, "name": info[pid]["name"], "team_id": info[pid]["team_id"], "value": value})

    prior_pa: dict[str, int] = {}
    prior_outs: dict[str, int] = {}
    for a in prior_archives:
        for pid, c in a.records.batters.items():
            prior_pa[pid] = prior_pa.get(pid, 0) + int(c["PA"])
        for pid, c in a.records.pitchers.items():
            prior_outs[pid] = prior_outs.get(pid, 0) + int(c["OUTS"])

    def rookie(pid: str) -> bool:
        p = players.get(pid)
        if p is None or p.foreign or p.origin == "foreign":
            return False
        entry = ENTRY_AGE.get(p.origin or "", 22)
        if info[pid]["age"] - entry >= ROOKIE_YEARS:
            return False
        return prior_pa.get(pid, 0) <= ROOKIE_PA and prior_outs.get(pid, 0) <= ROOKIE_OUTS and prior_registered.get(pid, 0) <= 1

    for li in sorted(set(team_league.values())):
        members = [pid for pid in info if league_of.get(pid) == li]
        batters = [pid for pid in members if info[pid]["role"] == "batter" and pid in rec.batters]
        pitchers = [pid for pid in members if pid in rec.pitchers and rec.pitchers[pid]["OUTS"] > 0]
        # MVP・新人王
        for key, pool in (("mvp", members), ("rookie", [pid for pid in members if rookie(pid)])):
            best = _pick([(w(pid), w(pid), pid) for pid in pool if pid in war])
            if best:
                add(key, li, best[0][2], f"WAR {float(best[0][0]):.1f}")
        # ベストナイン・ベストバッター賞
        qualified = [pid for pid in batters if rec.batters[pid]["PA"] * 2 >= qualifying_plate_appearances(games_of(pid))]
        by_slot: dict[str, list[str]] = {}
        for pid in qualified:
            s = primary_slot(rec, pid)
            if s is not None:
                by_slot.setdefault(s, []).append(pid)
        for slot in NINE:
            n = 3 if slot == "OF" else 1
            if slot == "P":
                pool = [pid for pid in pitchers if rec.pitchers[pid]["OUTS"] * 2 >= qualifying_outs(games_of(pid))]
                for _, _, pid in _pick([(w(pid), w(pid), pid) for pid in pool], n):
                    add("best_nine", li, pid, f"WAR {float(w(pid)):.1f}", slot)
                continue
            pool = by_slot.get(slot, [])
            for _, _, pid in _pick([(w(pid), w(pid), pid) for pid in pool], n):
                add("best_nine", li, pid, f"WAR {float(w(pid)):.1f}", slot)
            bat = lambda pid: war[pid].batting + war[pid].baserunning if pid in war else Fraction(0)
            for v, _, pid in _pick([(bat(pid), w(pid), pid) for pid in pool], n):
                add("best_batter", li, pid, f"打撃 + 走塁 {float(v):+.1f} 点", slot)
        # ベストディフェンス賞:そのポジションでチームの守備アウトの 3 分の 1 以上
        for slot in ("C", "1B", "2B", "3B", "SS", "OF"):
            cands = []
            for pid in batters:
                f = rec.fielders.get(pid) or {}
                outs = sum(int(c["OUTS"]) for pos, c in f.items() if _slot(pos) == slot)
                if outs * 3 < games_of(pid) * 27 or pid not in war:
                    continue
                runs = sum((Fraction(v) for pos, v in war[pid].fielding_by_position.items() if _slot(pos) == slot), Fraction(0))
                cands.append((runs, w(pid), pid))
            for v, _, pid in _pick(cands, 3 if slot == "OF" else 1):
                add("best_defense", li, pid, f"守備 {float(v):+.1f} 点", slot)
        # 中継ぎ・救援(先発の登板が登板の半分未満)
        relievers = [pid for pid in pitchers if rec.pitchers[pid]["GS"] * 2 < rec.pitchers[pid]["G"]]
        hp = lambda pid: rec.pitchers[pid]["HLD"] + rec.pitchers[pid]["W"]
        best = _pick([(hp(pid), w(pid), pid) for pid in relievers if hp(pid) > 0])
        if best:
            pid = best[0][2]
            add("best_setup", li, pid, f"{rec.pitchers[pid]['HLD']}ホールド {rec.pitchers[pid]['W']}勝")
        best = _pick([(rec.pitchers[pid]["SV"], w(pid), pid) for pid in relievers if rec.pitchers[pid]["SV"] > 0])
        if best:
            pid = best[0][2]
            add("best_closer", li, pid, f"{rec.pitchers[pid]['SV']}セーブ")
    # 最優秀先発投手賞(両リーグで 1 人)
    starters = [pid for pid in info if pid in rec.pitchers and rec.pitchers[pid]["GS"] * 2 >= rec.pitchers[pid]["G"] and rec.pitchers[pid]["OUTS"] >= qualifying_outs(games_of(pid))]
    best = _pick([(w(pid), w(pid), pid) for pid in starters])
    if best:
        pid = best[0][2]
        c = rec.pitchers[pid]
        era = c["ER"] * 27 / c["OUTS"] if c["OUTS"] else 0.0
        add("best_starter", None, pid, f"WAR {float(w(pid)):.1f}・{c['W']}勝・防御率 {era:.2f}")
    return out
