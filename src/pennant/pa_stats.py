"""打席の結果の集計(開発者向けの確認用。画面ではない)。

「一軍相当どうしの対戦」は、各球団の一軍相当(stats.first_team)の打者と投手を組み合わせ、
守備には、その球団でポジションごとに総合値が最も高い野手を置く(簡易の並べ方)。
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Iterable, Mapping

from .abilities import BATTER, FIELDER_POSITIONS, ITEM_LABELS, PITCHER, POSITION_LABELS, items_for
from .config import GenerationConfig
from .models import HiddenInfo, League, Player, PlayerState
from .plate_appearance import RESULT_LABELS, RESULTS, BaseOutState, OddsRatioModel
from .stats import first_team, overall

# ---- 平均的な選手(確認・テスト用) ----


def average_player(role: str, pid: str = "AVG", ratings: Mapping[str, float] | None = None, **attrs) -> Player:
    """全能力 50 の選手。ratings で一部の能力を、attrs で左右などを変えられる。"""
    r = {item: 50.0 for item in items_for(role)}
    r.update(ratings or {})
    defaults = {"bats": "R", "throws": None} if role == BATTER else {"bats": None, "throws": "R"}
    defaults.update(attrs)
    return Player(
        id=pid,
        family_name="平均",
        given_name="選手",
        age=27,
        role=role,
        position="SP" if role == PITCHER else "C",
        ratings=r,
        hidden=HiddenInfo(potential=dict(r), growth_type="normal", archetype="-", ability_drift={i: 0.0 for i in r}),
        state=PlayerState(form=0.0),
        **defaults,
    )


def average_defense(ratings: Mapping[str, float] | None = None) -> dict[str, Player]:
    return {pos: average_player(BATTER, f"D-{pos}", ratings) for pos in FIELDER_POSITIONS}


# ---- 確率・回数から、率を出す ----


def rates_from(probs: Mapping[str, float]) -> dict[str, float]:
    """1打席あたりの確率(または回数)から、主な率を計算する(犠打・犠飛は今回の範囲外)。"""
    total = sum(probs.values())
    p = {k: probs.get(k, 0.0) / total for k in RESULTS}
    hits = p["single"] + p["double"] + p["triple"] + p["home_run"]
    at_bats = 1 - p["walk"] - p["hit_by_pitch"]
    in_play = 1 - p["strikeout"] - p["walk"] - p["hit_by_pitch"] - p["home_run"]
    total_bases = p["single"] + 2 * p["double"] + 3 * p["triple"] + 4 * p["home_run"]
    avg = hits / at_bats
    obp = hits + p["walk"] + p["hit_by_pitch"]
    slg = total_bases / at_bats
    return {
        "K%": p["strikeout"],
        "BB%": p["walk"],
        "HBP%": p["hit_by_pitch"],
        "HR%": p["home_run"],
        "BABIP": (hits - p["home_run"]) / in_play,
        "失策率(インプレーあたり)": p["error"] / in_play,
        "打率": avg,
        "出塁率": obp,
        "長打率": slg,
        "OPS": obp + slg,
    }


RATE_KEYS = ("K%", "BB%", "HBP%", "HR%", "BABIP", "失策率(インプレーあたり)", "打率", "出塁率", "長打率", "OPS")
PERCENT_KEYS = {"K%", "BB%", "HBP%", "HR%", "失策率(インプレーあたり)"}

# 校正の目標(一般的な水準の目安。報告で示された範囲)
TARGETS = {
    "K%": (0.17, 0.21),
    "BB%": (0.07, 0.09),
    "HBP%": (0.005, 0.010),
    "HR%": (0.020, 0.030),
    "BABIP": (0.290, 0.310),
    "打率": (0.240, 0.265),
    "出塁率": (0.310, 0.335),
    "失策率(インプレーあたり)": (0.010, 0.025),
}


def fmt_rate(key: str, value: float) -> str:
    return f"{100 * value:.2f}%" if key in PERCENT_KEYS else f"{value:.3f}".replace("0.", ".", 1)


# ---- 一軍相当どうしの対戦 ----


class FirstTeamPool:
    """各球団の一軍相当の打者・投手と、守備の並び。"""

    def __init__(self, leagues: Iterable[League], gen_config: GenerationConfig):
        self.teams = []
        for lg in leagues:
            for team in lg.teams:
                ft = first_team(team, gen_config)
                batters = [p for p in ft if p.role == BATTER]
                pitchers = [p for p in ft if p.role == PITCHER]
                defense = {}
                for pos in FIELDER_POSITIONS:
                    candidates = [p for p in team.players if p.role == BATTER and p.position == pos]
                    defense[pos] = max(candidates, key=overall)
                self.teams.append((lg.seed, batters, pitchers, defense))

    def matchups(self, rng: random.Random, n: int):
        """(打者, 投手, 守備) を n 組。打者と投手は同じリーグの別の球団から選ぶ。"""
        for _ in range(n):
            bat = rng.randrange(len(self.teams))
            seed = self.teams[bat][0]
            others = [i for i, t in enumerate(self.teams) if t[0] == seed and i != bat]
            fld = rng.choice(others)
            yield rng.choice(self.teams[bat][1]), rng.choice(self.teams[fld][2]), self.teams[fld][3]


def expected_rates(model: OddsRatioModel, pool: FirstTeamPool, n: int, seed: int) -> dict[str, float]:
    """n 組の対戦について、結果の確率を平均した値(乱数のぶれのない期待値)。"""
    rng = random.Random(seed)
    totals = Counter()
    for batter, pitcher, defense in pool.matchups(rng, n):
        for k, v in model.probabilities(batter, pitcher, defense).items():
            totals[k] += v
    return rates_from(totals)


def simulate(model: OddsRatioModel, pool: FirstTeamPool, n: int, seed: int) -> tuple[Counter, Counter]:
    """n 打席を実際に乱数で回し、結果の回数と、担当ポジションの回数を数える。"""
    rng = random.Random(seed)
    counts, by_pos = Counter(), Counter()
    for batter, pitcher, defense in pool.matchups(rng, n):
        pa = model.resolve(batter, pitcher, defense, BaseOutState(), rng)
        counts[pa.result] += 1
        if pa.fielder:
            by_pos[pa.fielder] += 1
    return counts, by_pos


# ---- 感度表など ----

SENSITIVITY_ITEMS = (
    (BATTER, "contact"),
    (BATTER, "eye"),
    (BATTER, "power"),
    (BATTER, "batted_ball_quality"),
    (BATTER, "speed"),
    (BATTER, "gb_fb"),
    (PITCHER, "strikeout"),
    (PITCHER, "control"),
    (PITCHER, "stuff"),
    (PITCHER, "contact_suppression"),
    (PITCHER, "gb_fb"),
    ("fielder", "range"),
    ("fielder", "arm"),
    ("fielder", "fielding"),
)


def sensitivity(model: OddsRatioModel, who: str, item: str, delta: float) -> dict[str, float]:
    """平均的な打者・投手・守備から、1つの能力だけを delta 点動かしたときの率。"""
    batter = average_player(BATTER, "B", {item: 50 + delta} if who == BATTER else None)
    pitcher = average_player(PITCHER, "P", {item: 50 + delta} if who == PITCHER else None)
    defense = average_defense({item: 50 + delta} if who == "fielder" else None)
    return rates_from(model.probabilities(batter, pitcher, defense))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def build_pa_report(model: OddsRatioModel, pool: FirstTeamPool, n: int, seed: int) -> str:
    out: list[str] = ["# 打席の計算の確認(確認用)"]
    out.append(
        f"- 対戦の組み合わせ: 一軍相当どうし(球団数 {len(pool.teams)})\n"
        f"- 打席数: {n:,}(シード {seed})\n- 設定ファイル: {model.config.source}"
    )

    expected_n = min(n, 20_000)  # 期待値は組み合わせの平均なので、2万組あれば十分に安定する
    expected = expected_rates(model, pool, expected_n, seed)
    counts, by_pos = simulate(model, pool, n, seed)
    actual = rates_from(counts)
    rows = []
    for key in RATE_KEYS:
        target = TARGETS.get(key)
        target_text = f"{fmt_rate(key, target[0])}〜{fmt_rate(key, target[1])}" if target else "-"
        ok = "-" if not target else ("○" if target[0] <= expected[key] <= target[1] else "×")
        rows.append([key, fmt_rate(key, expected[key]), fmt_rate(key, actual[key]), target_text, ok])
    out.append(
        "## 結果の割合(一軍相当どうし)\n"
        f"- 期待値:{expected_n:,} 組の対戦について、結果の確率を平均した値(乱数のぶれがない)。実際:{n:,} 打席を乱数で回した結果\n\n"
        + _table(["項目", "期待値", "実際", "校正の目標", "目標内"], rows)
    )
    total = sum(counts.values())
    rows = [[RESULT_LABELS[r], f"{counts[r]:,}", f"{100 * counts[r] / total:.2f}%"] for r in RESULTS]
    out.append("## 結果の内訳(実際に回した回数)\n" + _table(["結果", "回数", "割合"], rows))

    # 感度表
    base = rates_from(model.probabilities(average_player(BATTER), average_player(PITCHER), average_defense()))
    keys = ("K%", "BB%", "HR%", "BABIP", "失策率(インプレーあたり)", "打率", "OPS")
    rows = [["(すべて 50。右打者×右投手)", "基準"] + [fmt_rate(k, base[k]) for k in keys]]
    for who, item in SENSITIVITY_ITEMS:
        who_label = {"batter": "打者", "pitcher": "投手", "fielder": "野手8人"}[who]
        for delta in (10, -10):
            r = sensitivity(model, who, item, delta)
            rows.append([f"{who_label}の{ITEM_LABELS[item]}", f"{delta:+d}"] + [fmt_rate(k, r[k]) for k in keys])
    out.append(
        "## 感度表(平均的な打者・投手・守備から、能力を1つだけ ±10 点動かしたとき)\n"
        "- 基準は右打者×右投手(同じ側)の対戦。左右の補正が入っている分、K% などは全体の平均と少しずれる\n\n"
        + _table(["能力", "変化"] + list(keys), rows)
    )

    # 左右の相性
    rows = []
    for bats, throws in (("R", "R"), ("L", "L"), ("R", "L"), ("L", "R"), ("S", "R"), ("S", "L")):
        r = rates_from(
            model.probabilities(average_player(BATTER, bats=bats), average_player(PITCHER, throws=throws), average_defense())
        )
        side = "両打ち" if bats == "S" else ("同じ側" if bats == throws else "逆側")
        rows.append([f"{'右左両'['RLS'.index(bats)]}打者 × {'右左'['RL'.index(throws)]}投手", side] + [fmt_rate(k, r[k]) for k in keys])
    out.append("## 左右の相性(能力はすべて 50)\n" + _table(["組み合わせ", "区分"] + list(keys), rows))

    # 守備の効果
    rows = []
    for label, ratings in (
        ("守備範囲・捕球 60(高い)", {"range": 60, "fielding": 60}),
        ("すべて 50(平均)", None),
        ("守備範囲・捕球 40(低い)", {"range": 40, "fielding": 40}),
    ):
        r = rates_from(model.probabilities(average_player(BATTER), average_player(PITCHER), average_defense(ratings)))
        rows.append([label, fmt_rate("BABIP", r["BABIP"]), fmt_rate("失策率(インプレーあたり)", r["失策率(インプレーあたり)"]), fmt_rate("打率", r["打率"])])
    out.append(
        "## 守備の効果(野手8人の能力を変えたとき。打者・投手は平均)\n"
        + _table(["野手", "BABIP(インプレーの安打率)", "失策率(インプレーあたり)", "打率"], rows)
    )

    # 担当ポジション別
    total_bip = sum(by_pos.values())
    rows = [[POSITION_LABELS[p], f"{100 * by_pos[p] / total_bip:.1f}%"] for p in ("P",) + FIELDER_POSITIONS]
    out.append("## 担当ポジションの割合(インプレーの打球)\n" + _table(["ポジション", "割合"], rows))
    return "\n\n".join(out) + "\n"
