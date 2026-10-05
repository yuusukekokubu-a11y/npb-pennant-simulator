"""画面から呼ぶ操作の関数のうち、ゲームの土台(状態・集計の使い回し・基準値・球場補正・WAR・シーズンの選び方)。"""

from __future__ import annotations

import math
from fractions import Fraction

from ..baselines import Baselines, blend, compute_baselines, load_baseline_settings
from ..metrics import baseline_dependent, compute
from ..parkfactors import ParkEstimate, estimate_parks, load_park_settings, player_park_factor
from ..records import Records
from ..savegame import GameState
from ..war import WarLine, load_war_settings, war_for_results
from .common import ROLE_LABELS, SOURCE_LABELS, _SeasonView, _StatsCache, _add_war, _sum, metrics_config


class GameBase:
    """ゲームの土台(状態・集計の使い回し・基準値・球場補正・WAR・シーズンの選び方)。"""

    def __init__(self, state: GameState, dirty: bool):
        self.state = state
        self.dirty = dirty  # 未保存の変更があるか
        self._cache = _StatsCache()
        self._park_estimates: tuple[int, dict[str, ParkEstimate] | None] | None = None
        self._war: tuple[int, dict[str, WarLine]] | None = None  # (試合数, WAR の表)。試合数が変わるまで覚えておく(D-179)
        self.last_negotiations: dict | None = None  # 直前に終わったオフの手続きの更改の交渉(保存しない。F3-2b)
        self.last_fa: dict | None = None  # 直前に終わったオフの FA(保存しない。F3-2c)
        self.last_market: dict | None = None  # 直前に終わったオフの市場(保存しない。①b)

    @property
    def records(self) -> _StatsCache:
        """集計(試合ごとの元の数と合計)。前に計算した分は使い回す。"""
        return self._cache.update(self.state.season)

    def baselines(self) -> tuple[Baselines, object]:
        """今使う基準値(出発点と今シーズンの値を、累積打席数で混ぜたもの。D-126)と、今シーズンの比重。

        日が進むまで使い回す。
        """
        cache = self.records
        n = len(cache.per_game)
        if cache._baselines is None or cache._baselines[0] != n:
            prior = self.state.baselines
            settings = self.state.baseline_settings
            batting = _sum(cache.total.batters)
            pa = batting["PA"]  # 比重は今シーズンの全打席数で(D-126)
            current = None
            if cache.tally.plate_appearances:
                current = compute_baselines(cache.tally, batting, _sum(cache.total.pitchers), settings, prior)
            blended, w = blend(prior, current, pa, settings.blend_constant)
            cache._baselines = (n, blended, w)
        return cache._baselines[1], cache._baselines[2]

    def baseline_info(self) -> dict:
        """画面に出す、基準値の混ぜ方と球場補正の説明(D-126、D-143)。"""
        _, w = self.baselines()
        source = SOURCE_LABELS.get(self.state.baselines.source, "出発点の値")
        pct = math.floor(w * 100 + Fraction(1, 2))
        n = self.season_number()
        park_text = (
            "球場補正は、1シーズン目のため 1.0(補正なし)です。"
            if n == 1
            else f"球場補正は、前のシーズンまで({n - 1}シーズン分)の結果から推定した値を使っています。"
        )
        return {
            "source": self.state.baselines.source,
            "source_label": source,
            "weight_percent": pct,
            "plate_appearances": _sum(self.records.total.batters)["PA"],
            "season_number": n,
            "text": f"wOBA などの基準値は、{source}に、今シーズンの値を混ぜて使っています(今シーズンの比重 {pct}%。打席が増えるほど上がり、シーズンの最後で約75%)。{park_text}",
        }

    def season_number(self) -> int:
        """今が何シーズン目か(前のシーズンまでの履歴の数 + 1)。"""
        return len(self.state.park_history) + 1

    def park_estimates(self) -> dict[str, ParkEstimate] | None:
        """前のシーズンまでの履歴から推定した球場補正。1シーズン目(履歴なし)は None。シーズン中は変わらない(D-143)。"""
        n = len(self.state.park_history)
        if n == 0:
            return None
        if self._park_estimates is None or self._park_estimates[0] != n:
            league_of = {t.id: t.league_index for t in self.state.league.teams}
            self._park_estimates = (n, estimate_parks(self.state.park_history, league_of, load_park_settings()))
        return self._park_estimates[1]

    def player_park_factor(self, player_id: str) -> Fraction:
        """選手の球場補正(立った球場ごとの打席数で重みづけ。D-142)。1シーズン目は 1。"""
        return player_park_factor(self.park_estimates(), self.records.player_park_pa.get(player_id, {}))

    def war_lines(self) -> dict[str, WarLine]:
        """今シーズンの、その時点までの WAR(③b。D-179)。基準値は wRC+ と同じ混ぜた値、球場補正は前のシーズンまでの推定。試合数が変わるまで覚えておく。"""
        n = len(self.state.season.played)
        if self._war is None or self._war[0] != n:
            results = [p.result for p in self.state.season.played]
            lines, _, _ = war_for_results(results, self.park_estimates(), self.state.baseline_settings or load_baseline_settings(), load_war_settings(), self.baselines()[0], self.records.total)
            self._war = (n, lines)
        return self._war[1]

    def war_note(self) -> str:
        n = self.season_number()
        park = "球場補正は、1シーズン目のため 1.0(補正なし)" if n == 1 else f"球場補正は、前のシーズンまで({n - 1}シーズン分)の結果から推定した値"
        return f"{self.state.season.day}日目までの値です(シーズンが進むと変わります)。基準値(リーグ平均・得点期待値)は wRC+ と同じく、出発点の値に今シーズンの値を混ぜたもの。{park}。控え水準とポジション補正は設定値(仮置き)。"

    def _team_names(self) -> dict[str, str]:
        return {t.id: t.name for t in self.state.league.teams}

    def _team(self, team_id: str):
        for t in self.state.league.teams:
            if t.id == team_id:
                return t
        raise KeyError(f"チーム '{team_id}' はありません")

    def _season_view(self, season: str | int | None) -> "_SeasonView":
        """成績に使う元の数・基準値・球場補正・WAR のまとまり。season は None/"current"(今)、シーズン番号、"career"(通算)。"""
        state = self.state
        key = "current" if season is None else str(season)
        if key == "current" or key == str(state.year):
            return _SeasonView("current", f"今シーズン({state.year}シーズン目)", self.records.total, self.baselines()[0], self.player_park_factor, self.war_lines(), self._current_player_info, self.baseline_info()["text"], self.war_note(), state.season.day)
        if key == "career":
            if not state.history:
                raise ValueError("通算は、2シーズン目から選べます")
            rec = Records()
            war: dict[str, WarLine] = {}
            pf_sum: dict[str, Fraction] = {}
            pa_sum: dict[str, int] = {}
            info: dict[str, dict] = {}
            for a in state.history:
                rec.add(a.records)
                for pid, c in a.records.batters.items():
                    pf_sum[pid] = pf_sum.get(pid, Fraction(0)) + a.park_factors.get(pid, Fraction(1)) * c["PA"]
                    pa_sum[pid] = pa_sum.get(pid, 0) + c["PA"]
                for pid, v in a.war.items():
                    war[pid] = _add_war(war.get(pid), v)
                info.update(a.players)
            cur = self.records.total
            rec.add(cur)
            for pid, c in cur.batters.items():
                pf_sum[pid] = pf_sum.get(pid, Fraction(0)) + self.player_park_factor(pid) * c["PA"]
                pa_sum[pid] = pa_sum.get(pid, 0) + c["PA"]
            for pid, v in self.war_lines().items():
                war[pid] = _add_war(war.get(pid), v)
            for pid in set(cur.batters) | set(cur.pitchers):
                info[pid] = self._current_player_info(pid)

            def pf(pid):
                return pf_sum[pid] / pa_sum[pid] if pa_sum.get(pid) else Fraction(1)

            n = len(state.history) + 1
            return _SeasonView("career", f"通算(1〜{state.year}シーズン目)", rec, self.baselines()[0], pf, war, lambda pid: info[pid], f"{n}シーズン分の元の数を足し合わせています。基準値に依存する指標(wOBA・wRC+・OPS+・FIP)は、各シーズンの値(そのシーズンの基準値と球場補正)を打席数(投手は投球回)で加重平均した値です。", f"{n}シーズン分の WAR の合計です(各シーズンの値を足しています)。", None, self._career_overrides())
        a = next((a for a in state.history if str(a.year) == key), None)
        if a is None:
            raise ValueError(f"シーズン {season!r} の成績はありません(選べるのは、今シーズン・過去のシーズン番号・career)")
        return _SeasonView(key, f"{a.year}シーズン目", a.records, a.baselines, lambda pid: a.park_factors.get(pid, Fraction(1)), a.war, lambda pid: a.players[pid], f"{a.year}シーズン目の最終の基準値(出発点の値に、そのシーズンの値を混ぜたもの)で計算した、確定した値です。球場補正は{'、1シーズン目のため 1.0(補正なし)' if a.year == 1 else f'、前のシーズンまで({a.year - 1}シーズン分)の結果から推定した値'}。", f"{a.year}シーズン目の確定した WAR です。", a.day)

    def _career_overrides(self) -> dict[str, dict]:
        """通算の、基準値に依存する指標の加重平均(D-192):選手 ID → 指標 → 値(値なしのシーズンは除く。全シーズン値なしなら None)。"""
        config = metrics_config()
        dependent = {role: baseline_dependent(config, role) for role in ROLE_LABELS}
        sums: dict[str, dict[str, list]] = {}  # pid → metric → [重みつき合計, 重みの合計]
        seasons = [(a.records, a.baselines, lambda pid, a=a: a.park_factors.get(pid, Fraction(1))) for a in self.state.history]
        seasons.append((self.records.total, self.baselines()[0], self.player_park_factor))
        for rec, base, pf in seasons:
            for role, group in (("batter", rec.batters), ("pitcher", rec.pitchers)):
                for pid, counts in group.items():
                    weight = counts["PA"] if role == "batter" else counts["OUTS"]
                    values = dict(base.values)
                    if role == "batter":
                        values["pf"] = pf(pid)
                    metrics = compute(config, role, counts, values)
                    acc = sums.setdefault(pid, {})
                    for key in dependent[role]:
                        v = metrics.get(key)
                        if v is None or not weight:
                            acc.setdefault(key, [Fraction(0), 0])
                            continue
                        s = acc.setdefault(key, [Fraction(0), 0])
                        s[0] += v * weight
                        s[1] += weight
        return {pid: {key: (s[0] / s[1] if s[1] else None) for key, s in acc.items()} for pid, acc in sums.items()}

    def _current_player_info(self, pid: str) -> dict:
        p = self.state.season.players.get(pid)
        if p is None:  # オフの手続きの途中で読み込んだ状態では、引退した選手がシーズンの一覧にいない。終わったシーズンの写し(履歴)から引く
            for a in reversed(self.state.history):
                if pid in a.players:
                    return dict(a.players[pid])
            raise KeyError(pid)
        return {"name": p.name, "team_id": p.team_id, "role": p.role, "position": p.position, "age": p.age}
