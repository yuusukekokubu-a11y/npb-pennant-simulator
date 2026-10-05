"""実装⑤:指標の集計の受け入れ条件の確認。"""

import copy
import importlib.util
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

from pennant import ConfigError, generate_league
from pennant.baserunning import HOME, RunnerMove
from pennant.decisions import decide
from pennant.fingerprint import records_record
from pennant.game import GamePlateAppearance, GameResult, PitcherLine
from pennant.metrics import compute, default_metrics_data, format_value, innings_text, load_metrics_config, validate_metrics_config
from pennant.plate_appearance import BaseOutState, PlateAppearance
from pennant.records import (
    earned_flags,
    game_records,
    qualified_batters,
    qualifying_outs,
    qualifying_plate_appearances,
    season_records,
)
from pennant.season import Season
from pennant.stats_report import consistency_checks

ROOT = Path(__file__).resolve().parents[1]


# ---- 試合を手で組み立てる道具 ----

class GameBuilder:
    """打席ログを手で組み立てる(勝敗・打点・自責点の条件を作るため)。H がホーム、A がアウェイ。"""

    def __init__(self):
        self.log = []
        self.inning = 1
        self.half = "top"
        self.outs = 0
        self.score = {"H": 0, "A": 0}
        self.lines: dict[str, PitcherLine] = {}
        self.order: list[str] = []

    def pitcher(self, pid, team, role="reliever"):
        line = PitcherLine(pid, team, role, self.inning)
        self.lines[pid] = line
        self.order.append(pid)
        return self

    def pa(self, pitcher, result="ground_out", outs=1, scorers=(), runners=0, batter="b", moves=None, dp=False, sf=False):
        """scorers:生還する走者の (選手 ID, 出塁させた投手, 失策が関係したか) の並び。"""
        batting = "A" if self.half == "top" else "H"
        fielding = "H" if batting == "A" else "A"
        mv = list(moves or [])
        for runner, resp, err in scorers:
            mv.append(RunnerMove(runner, 3, HOME, resp, False, err))
        if not any(m.start == 0 for m in mv):
            mv.append(RunnerMove(f"{batting}-{batter}", 0, None if outs else 1, pitcher))
        bo = BaseOutState(self.outs, runners >= 1, runners >= 2, runners >= 3)
        runs = sum(1 for m in mv if m.scored)
        p = PlateAppearance(result, f"{batting}-{batter}", pitcher, "R", bo, batted_ball="ground", fielder="SS")
        self.log.append(
            GamePlateAppearance(
                self.inning, self.half, batting, fielding, 1, f"{batting}-{batter}", pitcher,
                self.score[batting] - self.score[fielding], bo, p, mv, runs, outs, double_play=dp, sac_fly=sf,
            )
        )
        self.score[batting] += runs
        line = self.lines[pitcher]
        line.batters_faced += 1
        line.outs += outs
        self.outs += outs
        return self

    def end_half(self, pitcher):
        """残りのアウトを取って、攻守交代する。"""
        while self.outs < 3:
            self.pa(pitcher)
        self.outs = 0
        if self.half == "top":
            self.half = "bottom"
        else:
            self.half = "top"
            self.inning += 1
        return self

    def innings(self, n, home_p, away_p):
        """n イニング、両チームとも無得点で進める。"""
        for _ in range(n):
            self.end_half(home_p)
            self.end_half(away_p)
        return self

    def result(self):
        tie = self.score["H"] == self.score["A"]
        lines = [self.lines[p] for p in self.order]
        last = {}
        for line in lines:
            last[line.team_id] = line
        for line in lines:
            line.exit_reason = "game_end" if last[line.team_id] is line else "inning_end"
        return GameResult("H", "A", self.score["H"], self.score["A"], {}, self.inning, False, tie, False, self.log, lines, {})


def _score(g, pitcher, runs, resp):
    return g.pa(pitcher, "single", outs=0, scorers=[(f"run{len(g.log)}-{i}", resp, False) for i in range(runs)])


# ---- 受け入れ条件3:勝利・敗戦・セーブ・ホールド ----

def test_starter_win_hold_save():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.innings(2, "H1", "A1")
    g.end_half("H1")  # 3回表
    _score(g, "A1", 2, "A1").end_half("A1")  # 3回裏に2点(勝ち越し)
    g.innings(3, "H1", "A1")  # 4〜6回
    g.pitcher("H2", "H").end_half("H2")  # 7回表:中継ぎ
    g.end_half("A1")
    g.end_half("H2").end_half("A1")  # 8回
    g.pitcher("H3", "H").end_half("H3")  # 9回表:抑え(2点リード)
    d = decide(g.result())
    assert d.win == "H1" and d.loss == "A1" and d.save == "H3" and d.holds == ["H2"]


def test_short_starter_win_goes_to_first_reliever():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.end_half("H1")
    _score(g, "A1", 1, "A1").end_half("A1")  # 1回裏に先制
    g.innings(3, "H1", "A1")  # 2〜4回(先発は4回まで)
    g.pitcher("H2", "H")
    for _ in range(5):
        g.end_half("H2").end_half("A1")
    d = decide(g.result())
    assert g.lines["H1"].outs == 12
    assert d.win == "H2"


def test_lead_change_win_to_pitcher_of_record():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.end_half("H1")
    _score(g, "A1", 1, "A1").end_half("A1")  # H が 1-0
    g.innings(4, "H1", "A1")  # 2〜5回
    _score(g, "H1", 2, "H1").end_half("H1")  # 6回表に A が 2-1 と逆転
    g.end_half("A1")
    g.pitcher("H2", "H").end_half("H2")  # 7回表:H2
    g.pitcher("A2", "A")
    _score(g, "A2", 2, "A2").end_half("A2")  # 7回裏に H が 3-2 と再逆転(A2 の責任)
    g.pitcher("H3", "H").end_half("H3").end_half("A2")  # 8回
    g.end_half("H3")  # 9回表
    d = decide(g.result())
    assert d.win == "H2" and d.loss == "A2"
    assert d.save == "H3"  # 1点リードで登板し、2イニングを投げて終えた(①)


def test_save_one_inning_within_three_runs():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.end_half("H1")
    _score(g, "A1", 3, "A1").end_half("A1")
    g.innings(7, "H1", "A1")  # 2〜8回
    g.pitcher("H2", "H").end_half("H2")  # 9回表:3点リード
    d = decide(g.result())
    assert d.win == "H1" and d.save == "H2"


def test_no_save_with_big_lead_unless_three_innings():
    def game(closer_innings):
        g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
        g.end_half("H1")
        _score(g, "A1", 5, "A1").end_half("A1")
        g.innings(8 - closer_innings, "H1", "A1")
        g.pitcher("H2", "H")
        for i in range(closer_innings):
            g.end_half("H2")
            if i < closer_innings - 1:
                g.end_half("A1")
        return decide(g.result())

    assert game(1).save is None  # 5点差・1イニングはセーブにならない
    assert game(3).save == "H2"  # 3イニング以上ならセーブ


def test_save_when_tying_run_on_base():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.end_half("H1")
    _score(g, "A1", 4, "A1").end_half("A1")
    g.innings(7, "H1", "A1")
    g.pa("H1", "single", outs=1).pa("H1", "single", outs=1)  # 9回表 2アウト
    g.pitcher("H2", "H").pa("H2", "ground_out", outs=1, runners=3)  # 満塁(4点リード、同点の走者が打席)で登板
    d = decide(g.result())
    assert g.lines["H2"].outs == 1 and d.save == "H2"


def test_no_hold_when_lead_lost():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.end_half("H1")
    _score(g, "A1", 1, "A1").end_half("A1")
    g.innings(5, "H1", "A1")  # 2〜6回
    g.pitcher("H2", "H")
    _score(g, "H2", 1, "H2").end_half("H2")  # 7回表に同点にされる
    _score(g, "A1", 1, "A1").end_half("A1")  # 7回裏に勝ち越し
    g.pitcher("H3", "H").end_half("H3").end_half("A1")
    g.end_half("H3")
    d = decide(g.result())
    assert d.win == "H2" and "H2" not in d.holds


def test_extra_inning_walkoff():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.innings(9, "H1", "A1")
    g.pitcher("H2", "H").end_half("H2")  # 10回表
    g.pitcher("A2", "A")
    _score(g, "A2", 1, "A2")  # 10回裏にサヨナラ
    d = decide(g.result())
    assert d.win == "H2" and d.loss == "A2" and d.save is None


def test_away_win_with_inherited_runner():
    """決勝点の走者を出塁させた投手が、敗戦投手になる。"""
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.innings(5, "H1", "A1")
    g.pitcher("H2", "H")
    _score(g, "H2", 1, "H1").end_half("H2")  # 6回表、H1 が出した走者が H2 のときに生還
    g.end_half("A1")
    g.innings(3, "H2", "A1")
    d = decide(g.result())
    assert d.win == "A1" and d.loss == "H1"


def test_tie_has_no_decisions():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.innings(12, "H1", "A1")
    d = decide(g.result())
    assert (d.win, d.loss, d.save, d.holds) == (None, None, None, [])


# ---- 受け入れ条件4:打点と自責点 ----

def test_rbi_rules():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    g.pa("H1", "walk", outs=0, scorers=[("r1", "H1", False)], runners=3)  # 押し出し → 1打点
    g.pa("H1", "ground_out", outs=2, scorers=[("r2", "H1", False)], dp=True)  # 併殺打 → 0打点
    g.pa("H1", "error", outs=0, scorers=[("r3", "H1", True)])  # 失策で生還 → 0打点
    hr = [RunnerMove("r4", 1, HOME, "H1"), RunnerMove("A-b4", 0, HOME, "H1")]
    g.pa("H1", "home_run", outs=0, batter="b4", moves=hr)  # 2点本塁打 → 2打点
    rec = game_records(g.result())
    b = rec.batters
    assert b["A-b"]["RBI"] == 1 and b["A-b4"]["RBI"] == 2
    assert b["A-b"]["GDP"] == 1
    assert sum(c["R"] for c in b.values()) == 5


def test_earned_runs_follow_errors():
    g = GameBuilder().pitcher("H1", "H", "starter").pitcher("A1", "A", "starter")
    # 打者 e が失策で出塁 → 次の打者の安打で生還:自責点にならない
    g.pa("H1", "error", outs=0, batter="e", moves=[RunnerMove("A-e", 0, 1, "H1", True, True)])
    g.pa("H1", "single", outs=0, batter="s", moves=[RunnerMove("A-e", 1, HOME, "H1", True, False), RunnerMove("A-s", 0, 1, "H1")])
    # 打者 s は、失策で2塁へ進んだあと、安打で生還:自責点にならない
    g.pa("H1", "ground_out", outs=1, batter="t", moves=[RunnerMove("A-s", 1, 2, "H1", False, True), RunnerMove("A-t", 0, None, "H1")])
    g.pa("H1", "single", outs=0, batter="u", moves=[RunnerMove("A-s", 2, HOME, "H1"), RunnerMove("A-u", 0, 1, "H1")])
    # 打者 u は、失策の関わりなく生還:自責点
    g.pa("H1", "double", outs=0, batter="v", moves=[RunnerMove("A-u", 1, HOME, "H1"), RunnerMove("A-v", 0, 2, "H1")])
    assert earned_flags(g.result()) == [[], [False], [], [False], [True]]
    rec = game_records(g.result())
    assert rec.pitchers["H1"]["R"] == 3 and rec.pitchers["H1"]["ER"] == 1


# ---- 受け入れ条件2:指標の式(手計算の例) ----

@pytest.fixture(scope="module")
def mconfig():
    return load_metrics_config()


def test_metric_formulas_by_hand(mconfig):
    # 打席 10:安打 3(単打1・二塁打1・本塁打1)、四球 1、死球 1、犠牲フライ 1、三振 2 → 打数 7、塁打 7
    c = Counter(PA=10, AB=7, H=3, B1=1, B2=1, HR=1, TB=7, BB=1, HBP=1, SF=1, SO=2)
    m = compute(mconfig, "batter", c)
    assert m["avg"] == Fraction(3, 7)
    assert m["obp"] == Fraction(5, 10)
    assert m["slg"] == Fraction(7, 7)
    assert m["ops"] == Fraction(1, 2) + 1
    assert m["iso"] == 1 - Fraction(3, 7)
    assert m["babip"] == Fraction(2, 7 - 2 - 1 + 1)
    assert m["k_pct"] == Fraction(2, 10) and m["bb_pct"] == Fraction(1, 10)
    p = compute(mconfig, "pitcher", Counter(ER=10, OUTS=60, SO=30, BB=10, BF=100))  # 20回で自責点10
    assert p["era"] == Fraction(45, 10) and p["k_pct"] == Fraction(3, 10)
    assert format_value(mconfig, "avg", m["avg"]) == ".429"
    assert format_value(mconfig, "ops", m["ops"]) == "1.500"
    assert format_value(mconfig, "k_pct", m["k_pct"]) == "20.0%"
    assert format_value(mconfig, "era", p["era"]) == "4.50"
    assert innings_text(20) == "6 2/3" and innings_text(21) == "7"


def test_zero_denominator_is_no_value(mconfig):
    m = compute(mconfig, "batter", Counter())
    assert all(v is None for v in m.values())
    assert format_value(mconfig, "avg", None) == "-"
    assert compute(mconfig, "pitcher", Counter())["era"] is None


# ---- 受け入れ条件5:規定 ----

@pytest.mark.parametrize("games,pa", [(125, 388), (143, 443), (10, 31), (5, 16), (15, 47)])
def test_qualifying_plate_appearances(games, pa):
    assert qualifying_plate_appearances(games) == pa  # 3.1 倍を四捨五入(125 → 387.5 → 388、5 → 15.5 → 16)


def test_qualifying_outs():
    assert qualifying_outs(125) == 375 and qualifying_outs(143) == 429


# ---- 受け入れ条件6:指標の定義データ ----

def test_metrics_config_is_valid_and_complete(mconfig):
    for mid, m in mconfig.metrics.items():
        assert m["name"].strip() and "description" not in m, mid  # 解説の文は用語集に(D-311)
    assert set(mconfig.for_role("batter", stage=1)) == {"avg", "obp", "slg", "ops", "iso", "babip", "k_pct", "bb_pct"}
    assert set(mconfig.for_role("pitcher", stage=1)) == {"k_pct", "bb_pct", "era"}


def test_metric_categories(mconfig):
    """区分(基本/セイバー。D-109):成績の画面の切り替えで使う。"""
    assert mconfig.in_category("basic") == ["avg", "obp", "slg", "ops", "era"]  # OPS は基本(D-130)
    assert mconfig.in_category("saber") == ["iso", "babip", "k_pct", "bb_pct", "woba", "wrc_plus", "ops_plus", "fip"]


def test_spec_glossary_points_to_the_game_glossary(mconfig):
    """指標とゲームのルールの用語は、ゲームの用語集(glossary.json)が正本。SPEC の用語集には、指標の文を重ねて書かない(D-311)。"""
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    glossary = spec[spec.index("## 3. 用語集") : spec.index("## 4.")]
    rows = dict(re.findall(r"^\| (.+?) \| (.+?) \|$", glossary, flags=re.M))
    assert "glossary.json" in glossary
    for mid, m in mconfig.metrics.items():
        assert m["name"] not in rows, f"{m['name']} は用語集(glossary.json)にだけ書きます"


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["metrics"]["avg"].update(name=""), "metrics.avg.name"),
        (lambda d: d["metrics"]["avg"]["formulas"].update(batter="H / XYZ"), "XYZ"),
        (lambda d: d["metrics"]["avg"]["formulas"].update(batter="__import__('os')"), "使えない書き方"),
        (lambda d: d["metrics"]["avg"]["formulas"].update(batter="H / 0.5"), "整数"),
        (lambda d: d["metrics"]["era"]["better"].update(pitcher="good"), "metrics.era.better.pitcher"),
        (lambda d: d["metrics"]["avg"].update(inputs=["H"]), "AB"),
        (lambda d: d["metrics"]["avg"].update(format="pct"), "metrics.avg.format"),
        (lambda d: d["metrics"]["avg"].update(category="other"), "metrics.avg.category"),
        (lambda d: d["metrics"]["avg"].pop("category"), "metrics.avg.category"),
    ],
)
def test_metrics_config_validation(change, message):
    data = default_metrics_data()
    change(data)
    with pytest.raises(ConfigError, match=re.escape(message)):
        validate_metrics_config(data)


# ---- 受け入れ条件1・7・8:1シーズンの整合・再現・隠し情報 ----

@pytest.fixture(scope="module")
def season(config, names):
    league = generate_league(5, config, names)
    s = Season(copy.deepcopy(league), 5)
    result = s.play_to_end()
    return league, result


def test_consistency_checks_on_a_season(season):
    league, result = season
    results = [p.result for p in result.games]
    rec = season_records(results)
    checks = consistency_checks(rec, results, result)
    assert all(ok for _, ok, _ in checks), [c for c in checks if not c[1]]
    assert all(isinstance(v, int) for group in (rec.batters, rec.pitchers, rec.teams) for c in group.values() for v in c.values())


def test_decisions_counts_on_a_season(season):
    _, result = season
    for p in result.games:
        d = decide(p.result)
        if p.result.tie:
            assert d.win is None
            continue
        winners = {line.pitcher_id for line in p.result.pitchers if line.team_id == p.result.winner}
        losers = {line.pitcher_id for line in p.result.pitchers if line.team_id != p.result.winner}
        assert d.win in winners and d.loss in losers
        assert d.save is None or (d.save in winners and d.save != d.win)
        assert not set(d.holds) & {d.win, d.save} and set(d.holds) <= winners


def test_same_seed_same_records(season, config, names):
    league, result = season
    again = Season(copy.deepcopy(league), 5).play_to_end()
    assert records_record(season_records([p.result for p in again.games])) == records_record(season_records([p.result for p in result.games]))


def test_qualified_batters_have_enough_plate_appearances(season):
    _, result = season
    rec = season_records([p.result for p in result.games])
    q = qualified_batters(rec)
    assert 60 <= len(q) <= 130
    assert all(rec.batters[pid]["PA"] >= 388 for pid in q)


def test_fielder_id_is_logged(season):
    _, result = season
    players = {p.id: p for p in season[0].all_players()}
    for p in result.games[:20]:
        for x in p.result.log:
            if x.pa.fielder:
                assert x.fielder_id is not None
                if x.pa.fielder == "P":
                    assert x.fielder_id == x.pitcher_id
                else:
                    assert players[x.fielder_id].role == "batter"
            else:
                assert x.fielder_id is None


def test_script_runs(capsys):
    spec = importlib.util.spec_from_file_location("inspect_stats", ROOT / "scripts" / "inspect_stats.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--seed", "2", "--top", "3"]) == 0
    out = capsys.readouterr().out
    assert "整合チェック" in out and "×" not in out.split("## 規定")[0]
    for heading in ("打率の上位", "本塁打の上位", "打点の上位", "OPSの上位", "防御率の上位", "勝利の上位", "セーブの上位", "奪三振の上位", "チームの得点・失点", "真の能力"):
        assert heading in out
