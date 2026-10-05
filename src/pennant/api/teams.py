"""画面から呼ぶ操作の関数のうち、順位表・チーム・球場。"""

from __future__ import annotations

from ..abilities import POSITION_LABELS
from ..contracts import remaining_years
from ..newgame import public_player
from ..parkfactors import FACTOR_KEYS, FACTOR_LABELS, ParkTally, raw_ratio
from ..war import war_totals
from .common import BATS_LABELS, THROWS_LABELS, _gb_text, _pct_text, _war_text, raw_rate


class TeamsMixin:
    """Game の一部:順位表・チーム・球場。"""

    def standings(self) -> dict:
        """2リーグの順位表(実装④の順位の決め方そのまま)。自球団の行には is_mine が付く。"""
        season = self.state.season
        my = self.state.my_team_id
        leagues = []
        for i in season.league_indexes():
            rows = []
            for r in season.standings(i):
                rows.append(
                    {
                        "rank": r.rank,
                        "team_id": r.team_id,
                        "name": r.name,
                        "games": r.games,
                        "wins": r.wins,
                        "losses": r.losses,
                        "ties": r.ties,
                        "pct": _pct_text(r.pct, r.wins + r.losses),
                        "games_behind": _gb_text(r.games_behind, r.rank == 1),
                        "is_mine": r.team_id == my,
                    }
                )
            leagues.append({"index": i, "name": self.state.league.league_names[i], "rows": rows})
        return {"day": season.day, "leagues": leagues}

    def teams(self) -> list[dict]:
        return [
            {"id": t.id, "name": t.name, "league_index": t.league_index, "league_name": self.state.league.league_names[t.league_index], "stadium": t.stadium, "is_mine": t.id == self.state.my_team_id}
            for t in self.state.league.teams
        ]

    def players(self, team_id: str) -> list[dict]:
        """チームの選手(公開用の情報だけ。能力値・隠し情報は含めない。D-108)。"""
        team = next(t for t in self.state.league.teams if t.id == team_id)
        return [public_player(p, team.name) for p in team.players]

    def stadium(self, team_id: str) -> dict:
        """球場のページ(公開用。D-138):球場名、本拠地のチーム、本拠地での実際の結果(両チームの合計)。倍率の値は含めない。"""
        team = self._team(team_id)
        games = home_runs = runs = plate_appearances = 0
        for p in self.state.season.played:
            r = p.result
            if r.home_team_id != team_id:
                continue
            games += 1
            runs += r.home_runs + r.away_runs
            plate_appearances += len(r.log)
            home_runs += sum(1 for x in r.log if x.pa.result == "home_run")
        tally = self.records.park_tallies.get(team_id, ParkTally())

        def rate(c, key):
            v = raw_rate(c, key)
            return "-" if v is None else (f"{100 * float(v):.2f}%" if key == "home_run" else f"{float(v):.3f}")

        this_season = {}
        for key in FACTOR_KEYS:
            ratio = raw_ratio(tally, key)
            this_season[key] = {"label": FACTOR_LABELS[key], "home": rate(tally.home, key), "away": rate(tally.away, key), "ratio": "-" if ratio is None else f"{float(ratio):.3f}"}
        est = self.park_estimates()
        estimate = None
        if est is not None and team_id in est:
            e = est[team_id]
            estimate = {"seasons": e.seasons, **{k: f"{float(e.estimate[k]):.3f}" for k in FACTOR_KEYS}}
        return {
            "team_id": team_id,
            "name": team.stadium,
            "team_name": team.name,
            "league_name": self.state.league.league_names[team.league_index],
            "is_mine": team_id == self.state.my_team_id,
            "season_number": self.season_number(),
            "this_season": this_season,
            "estimate": estimate,
            "estimate_note": (
                "まだ推定できません。1シーズンだけでは、運のぶれが大きいためです。2シーズン目から、前のシーズンまでの結果で推定します。"
                if estimate is None
                else f"前のシーズンまで({estimate['seasons']}シーズン分)の本拠地とアウェイの比から推定し、1.0 に向けて縮めた値(各リーグの平均が 1.0)。「得点」は本塁打と BABIP の推定から組み立てた「1打席あたりの得点の出やすさ」で、wRC+・OPS+ の球場補正に使われます。BABIP 単独の推定は参考値です(運のぶれに埋もれやすい)。"
            ),
            "games": games,
            "home_runs": home_runs,
            "runs": runs,
            "plate_appearances": plate_appearances,
            "home_runs_per_game": f"{home_runs / games:.2f}" if games else "-",
            "runs_per_game": f"{runs / games:.2f}" if games else "-",
            "note": "本拠地で行った試合の、両チームを合わせた数です。球場の打ちやすさは、結果から推し量ってください(真の倍率は、答え合わせモードで見られます)。",
        }

    def team(self, team_id: str) -> dict:
        """チームのページ:チームの成績(試合・勝敗・得点・失点)と、選手の一覧(公開用の情報と出場数)。"""
        team = self._team(team_id)
        row = next(r for r in self.state.season.standings(team.league_index) if r.team_id == team_id)
        rec = self.records.total
        t = rec.teams.get(team_id, {})
        order = {k: i for i, k in enumerate(POSITION_LABELS)}
        players = []
        for p in sorted(team.players, key=lambda p: (order[p.position], p.id)):
            counts = (rec.batters if p.role == "batter" else rec.pitchers).get(p.id, {})
            players.append(
                {
                    "player_id": p.id,
                    "name": p.name,
                    "age": p.age,
                    "role": p.role,
                    "position": POSITION_LABELS[p.position],
                    "hand": BATS_LABELS.get(p.bats) if p.role == "batter" else THROWS_LABELS.get(p.throws),
                    "games": counts.get("G", 0),
                }
            )
        lines = {pid: v for pid, v in self.war_lines().items() if v.team_id == team_id}
        totals = war_totals(lines)
        salaries = sorted(({"player_id": p.id, "name": p.name, "position": POSITION_LABELS[p.position], "age": p.age, "salary": int(p.contract["salary"]), "salary_text": f"{int(p.contract['salary']):,}", "remaining": remaining_years(p.contract, self.state.year)} for p in team.players if p.contract), key=lambda r: (-r["salary"], r["player_id"]))
        return {
            "team_id": team_id,
            "name": team.name,
            "budget": self.budget_info(team_id),
            "salaries": salaries,
            "stadium": team.stadium,
            "league_name": self.state.league.league_names[team.league_index],
            "is_mine": team_id == self.state.my_team_id,
            "rank": row.rank,
            "war": {
                "batters": _war_text(totals["batters"]),
                "pitchers_ra": _war_text(totals["pitchers_ra"]),
                "pitchers_fip": _war_text(totals["pitchers_fip"]),
                "total_ra": _war_text(totals["batters"] + totals["pitchers_ra"]),
                "note": self.war_note(),
            },
            "record": {
                "games": t.get("G", 0),
                "wins": row.wins,
                "losses": row.losses,
                "ties": row.ties,
                "pct": _pct_text(row.pct, row.wins + row.losses),
                "games_behind": _gb_text(row.games_behind, row.rank == 1),
                "runs": t.get("R", 0),
                "runs_allowed": t.get("RA", 0),
            },
            "players": players,
        }
