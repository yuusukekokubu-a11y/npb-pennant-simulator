"""画面から呼ぶ操作の関数のうち、新規開始・開く・保存・進める・状態・年度の確定。"""

from __future__ import annotations

import copy
from datetime import date, datetime
from fractions import Fraction
from typing import Sequence

from .. import draft as draftmod, fa as famod
from ..baselines import Baselines, load_baseline_settings, trial_baselines
from ..config import load_generation_config, load_name_parts
from ..contracts import MONEY_RULES, RULE_LABELS, assign_tiers, load_contract_settings
from ..draft import STAGE_LABELS, load_draft_settings, stage_of
from ..history import ArchivedStanding, SeasonArchive
from ..negotiation import load_negotiation_settings
from ..newgame import TeamNameError, check_team_name, new_league, resolve_team_names
from ..offseason import age_update_retire, load_offseason_settings
from ..parkfactors import season_tallies
from ..savegame import GameState, default_file_name, load_game, save_game
from ..season import Season, derive_seed
from .common import BASELINE_MODES, _gb_text, _pct_text


def preview_teams(seed: int) -> dict:
    """新規開始の画面に出す、球団の並びと架空の初期名(そのシードで作られるリーグ)。"""
    league = new_league(seed)
    return {
        "seed": seed,
        "leagues": [
            {
                "index": i,
                "name": name,
                "teams": [
                    {"id": t.id, "default_name": t.default_name, "stadium": t.stadium}
                    for t in league.teams
                    if t.league_index == i
                ],
            }
            for i, name in enumerate(league.league_names)
        ],
        "order": [t.id for t in league.teams],
    }


def check_team_names(seed: int, names: Sequence[str | None]) -> list[str | None]:
    """球団名の欄ごとの問題(なければ None)。重複は、後ろの欄に理由を付ける。"""
    league = new_league(seed)
    problems: list[str | None] = [check_team_name(n) for n in names]
    if any(problems):
        return problems
    try:
        resolve_team_names(league, names)
    except TeamNameError as exc:
        for text in exc.problems:
            head, _, reason = text.partition(": ")
            if head.endswith("番目の球団"):
                i = int(head.removesuffix("番目の球団")) - 1
                if 0 <= i < len(problems) and problems[i] is None:
                    problems[i] = reason
    return problems


class ProgressMixin:
    """Game の一部:新規開始・開く・保存・進める・状態・年度の確定。"""

    @classmethod
    def new(
        cls,
        seed: int,
        team_names: Sequence[str | None],
        my_team_index: int | None,
        season_seed: int | None = None,
        baselines: str = "trial",
        progress=None,
        prerun_progress=None,
        scout_level: str | None = None,
        money_rule: str | None = None,
    ) -> "Game":
        """新しいリーグを作る。seed はリーグ(選手の生成)の、season_seed はシーズン(日程と試合)のシード
        (省略時は seed と同じ)。球団名・自球団に問題があれば TeamNameError(理由つき)。

        baselines は初年度の指標の基準値の求め方:"trial"(見えない試運転のシーズンで求める。既定)か
        "default"(設定ファイルの既定値。速い)。progress には試運転の (終わった日数, 全日数) を知らせる(D-121、D-122)。
        """
        if baselines not in BASELINE_MODES:
            raise ValueError(f"基準値の求め方は trial か default です(値: {baselines!r})")
        gen, parts = load_generation_config(), load_name_parts()
        offseason_settings = load_offseason_settings()
        draft_settings = load_draft_settings()
        level = draft_settings.default_level if scout_level is None else str(scout_level)
        if level not in draft_settings.levels:
            raise ValueError(f"スカウト評価のずれの大きさは small / medium / large です(値: {scout_level!r})")
        contract_settings = load_contract_settings()
        negotiation_settings = load_negotiation_settings()
        rule = contract_settings.default_rule if money_rule is None else str(money_rule)
        if rule not in MONEY_RULES:
            raise ValueError(f"お金のルールは none / loose / standard / strict です(値: {money_rule!r})")
        tiers = assign_tiers([f"T{i:02d}" for i in range(1, 13)], seed, contract_settings) if rule == "strict" else {}
        contract_info: dict = {}
        league = new_league(seed, team_names, gen, parts, prerun=True, offseason_settings=offseason_settings, progress=prerun_progress, draft_settings=draft_settings, scout_sd=draft_settings.level_sd(level), calibration=offseason_settings.calibration(level), money_rule=rule, tiers=tiers, contract_settings=contract_settings, contract_info=contract_info, negotiation_settings=negotiation_settings)
        tiers = {tid: t for tid, t in tiers.items() if any(team.id == tid for team in league.teams)}
        if my_team_index is not None and (isinstance(my_team_index, bool) or not isinstance(my_team_index, int) or not 0 <= my_team_index < len(league.teams)):
            raise TeamNameError([f"自球団の選び方が正しくありません(値: {my_team_index!r})"])
        settings = load_baseline_settings()
        prior = trial_baselines(league, settings, progress) if baselines == "trial" else settings.default_baselines()
        season = Season(league, seed if season_seed is None else season_seed)
        famod.initialize_seasons(league, season.actives, league.seed, negotiation_settings)  # 初期選手の FA 権の年数(D-264)
        my_team_id = None if my_team_index is None else league.teams[my_team_index].id
        state = GameState(season, gen, parts, "", my_team_id, prior, settings, offseason_settings=offseason_settings, calibration=offseason_settings.calibration(level), scout_level=level, draft_settings=draft_settings, money_rule=rule, budget_tiers=tiers, contract_settings=contract_settings, negotiation_settings=negotiation_settings)
        state.contract_rates["1"] = float(contract_info.get("rate", 0.0))  # 新規開始時の単価(初期選手の契約の算定で求めた値)
        return cls(state, dirty=True)

    @classmethod
    def load(cls, data: bytes) -> "Game":
        """セーブデータを読み込む。壊れていれば SaveDataError(今のゲームには触れない)。"""
        return cls(load_game(bytes(data)), dirty=False)

    def save(self, today: date | str | None = None, saved_at: datetime | None = None) -> dict:
        """セーブデータの中身と、既定のファイル名(日付だけ。球団名は入れない)。保存すると「未保存」でなくなる。

        today は端末の今日の日付("2026-10-02" の形でもよい)。省略時は、この計算の場所の今日。"""
        if isinstance(today, str):
            today = date.fromisoformat(today)
        data = save_game(self.state, saved_at)
        self.dirty = False
        return {"data": data, "file_name": default_file_name(today or date.today()), "bytes": len(data)}

    def advance(self, days: int = 1) -> dict:
        """days 日進める(シーズンの終わりで止まる)。戻り値は status と同じ。"""
        season = self.state.season
        n = max(0, min(int(days), season.total_days - season.day))
        if n:
            season.play_days(n)
            self.dirty = True
        return self.status()

    def status(self) -> dict:
        season = self.state.season
        my = self.state.my_team_id
        mine = None
        if my:
            team = next(t for t in self.state.league.teams if t.id == my)
            row = next(r for r in season.standings(team.league_index) if r.team_id == my)
            mine = {
                "id": my,
                "name": team.name,
                "league_name": self.state.league.league_names[team.league_index],
                "rank": row.rank,
                "wins": row.wins,
                "losses": row.losses,
                "ties": row.ties,
                "pct": _pct_text(row.pct, row.wins + row.losses),
                "games_behind": _gb_text(row.games_behind, row.rank == 1),
            }
        return {
            "day": season.day,
            "total_days": season.total_days,
            "games_played": len(season.played),
            "total_games": len(season.schedule),
            "is_over": season.is_over,
            "seed": season.seed,
            "my_team": mine,
            "dirty": self.dirty,
            "year": self.state.year,
            "can_year_end": season.is_over and self.state.procedure is None,
            "seasons": self.season_choices(),
            "offseason": None if self.state.procedure is None else {"active": True, "phase": self.state.procedure.phase, "phase_label": STAGE_LABELS[stage_of(self.state.procedure.phase)], "year": self.state.procedure.year},
            "scout_level": self.state.scout_level,
            "money_rule": self.state.money_rule,
            "money_rule_label": RULE_LABELS[self.state.money_rule],
        }

    def season_choices(self) -> list[dict]:
        """成績のページで選べるシーズン(今シーズン・過去の各シーズン・通算。D-182)。"""
        out = [{"key": "current", "label": f"今シーズン({self.state.year}シーズン目)"}]
        for a in reversed(self.state.history):
            out.append({"key": str(a.year), "label": f"{a.year}シーズン目"})
        if self.state.history:
            out.append({"key": "career", "label": "通算"})
        return out

    def finish_season(self) -> int:
        """シーズンを終えて、球場 × シーズンの集計を履歴に足す(年度の確定の一部。D-146)。戻り値は履歴の数。"""
        if not self.state.season.is_over:
            raise ValueError("シーズンがまだ終わっていません")
        self.state.park_history.append(season_tallies(p.result for p in self.state.season.played))
        self._park_estimates = None
        self.dirty = True
        return len(self.state.park_history)

    def year_end_preview(self) -> dict:
        """年度の確定の確認の画面に出す情報(戻せないこと、保存への導線は画面側)。"""
        season = self.state.season
        names = self._team_names()
        champs = []
        if season.is_over:
            for i in season.league_indexes():
                champs.append({"league_name": self.state.league.league_names[i], "teams": [names[t] for t in season.result().champions[i]]})
        return {
            "year": self.state.year,
            "is_over": season.is_over,
            "champions": champs,
            "players": len(self.state.league.all_players()),
            "log_seasons": self.state.offseason_settings.log_seasons,
            "dirty": self.dirty,
            "note": "年度を確定すると、今シーズンの集計を履歴に残し、選手の年齢が1つ進んで能力が更新され、引退と新人の入団が決まり、次のシーズンが始まります。この操作は戻せません。直前の状態を残したいときは、先に「保存」で別の名前のファイルに保存してください。",
        }

    def _archive_current_season(self) -> SeasonArchive:
        season = self.state.season
        result = season.result()
        standings = [
            ArchivedStanding(i, row.rank, row.team_id, row.wins, row.losses, row.ties, Fraction(row.games_behind))
            for i, rows in result.standings.items()
            for row in rows
        ]
        rec = self.records.total
        players = {}
        for pid in set(rec.batters) | set(rec.pitchers):
            p = season.players[pid]
            players[pid] = {"name": p.name, "team_id": p.team_id, "role": p.role, "position": p.position, "age": p.age}
        keep = len(self.state.history) + 1 < self.state.offseason_settings.log_seasons  # 今シーズンのログを残すか(今シーズンを含めて log_seasons 分)
        return SeasonArchive(
            year=self.state.year,
            seed=season.seed,
            standings=standings,
            champions=dict(result.champions),
            records=copy.deepcopy(rec),
            players=players,
            park_factors={pid: self.player_park_factor(pid) for pid in rec.batters},
            war=dict(self.war_lines()),
            baselines=self.baselines()[0],
            games=list(season.played) if keep else None,
            day=season.day,
            total_days=season.total_days,
        )

    def year_end(self) -> dict:
        """年度の確定(F2。D-185、D-186):今シーズンの集計を履歴へ → 球場補正の履歴と基準値の出発点を更新 →
        加齢・能力の更新・引退・補充(offseason.run_offseason)→ 次のシーズンを作る。戻り値はオフの結果の要約。"""
        state = self.state
        season = state.season
        if not season.is_over:
            raise ValueError("シーズンがまだ終わっていません(最後まで進めてから、年度を確定してください)")
        if state.procedure is not None:
            raise ValueError("オフの手続きが進行中です(手続きを終えると、次のシーズンが始まります)")
        famod.count_active_seasons(state.league, season.actives)  # このシーズンの一軍に入っていた選手の FA 権の年数(D-258)
        archive = self._archive_current_season()
        state.history.append(archive)
        # 残す打席ログの数を守る(直近 log_seasons シーズン。今シーズンは次のシーズンの分として数える)
        limit = max(0, state.offseason_settings.log_seasons - 1)
        with_games = [a for a in state.history if a.games is not None]
        for a in with_games[: max(0, len(with_games) - limit)]:
            a.games = None
        records = {tid: (int(c["W"]), int(c["L"])) for tid, c in self.records.total.teams.items()}
        state.park_history.append(season_tallies(p.result for p in season.played))
        final, _ = self.baselines()
        state.baselines = Baselines(dict(final.values), list(final.re24), dict(final.linear_weights), final.plate_appearances, "season")  # 次の出発点(D-121)
        off_seed = derive_seed(season.seed, "offseason")
        result, _ = age_update_retire(state.league, off_seed, state.gen_config, state.offseason_settings, state.year, state.calibration)
        state.offseasons.append(result)
        state.procedure = draftmod.start_procedure(state.league, off_seed, state.gen_config, state.name_parts, state.draft_settings, state.year, state.calibration, records)
        state.procedure.ranks = {s.team_id: int(s.rank) for s in archive.standings}  # 勝利軸は公式の順位(D-250)
        ctx = self._contract_ctx()
        draftmod.apply_contracts_start(state.league, state.procedure, ctx, state.my_team_id, *self._mins())  # 更改の交渉(AI 球団は最後まで)と AI の超過の解消(D-235、D-244)
        if state.my_team_id is not None and not draftmod.open_entries(state.procedure, state.my_team_id):
            draftmod.next_phase(state.procedure)  # 自球団に満了者がいなければ、契約更改の段階は飛ばす
        state.contract_rates[str(state.year + 1)] = state.procedure.rate
        self.dirty = True
        if state.my_team_id is None:  # 観戦のみ:手続きはすべて自動(D-198)
            filled = draftmod.complete(state.league, state.procedure, state.gen_config, state.name_parts, state.draft_settings, state.scout_sd_map(), state.calibration, None, *self._mins(), ctx=ctx)
            self._finish_offseason(filled)
        return self.offseason_summary(result.year)
