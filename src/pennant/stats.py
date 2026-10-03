"""生成したリーグの分布を集計する(開発者向けの確認用。画面ではない)。

「一軍相当」は、各球団で総合値(現在の能力のうち「型」の項目を除いた平均)が
高い順に、設定値 first_team の人数を選んだもの。一軍登録人数は未確認のため仮置き。
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Iterable

from .abilities import BATTER, FIELDER_POSITIONS, ITEM_LABELS, PITCHER, POSITION_LABELS, items_for, strength_items_for
from .config import GenerationConfig, archetype_shares
from .models import League, Player, Team

AGE_BINS = ((18, 22), (23, 27), (28, 32), (33, 37), (38, 45))


def age_bin_label(age: int) -> str:
    for lo, hi in AGE_BINS:
        if lo <= age <= hi:
            return f"{lo}〜{hi}歳" if hi < 45 else f"{lo}歳〜"
    return "その他"


def bin_labels() -> list[str]:
    return [age_bin_label(lo) for lo, _ in AGE_BINS]


def overall(player: Player) -> float:
    """総合値:現在の能力のうち、強弱を表す項目の平均。"""
    items = strength_items_for(player.role)
    return statistics.fmean(player.ratings[i] for i in items)


def potential_overall(player: Player) -> float:
    items = strength_items_for(player.role)
    return statistics.fmean(player.hidden.potential[i] for i in items)


def first_team(team: Team, config: GenerationConfig) -> list[Player]:
    """一軍相当の選手(仮置きの人数)。"""
    ft = config["first_team"]
    result = []
    for role, n in ((PITCHER, ft["pitchers"]), (BATTER, ft["fielders"])):
        members = sorted((p for p in team.players if p.role == role), key=overall, reverse=True)
        result += members[:n]
    return result


def first_team_players(leagues: Iterable[League], config: GenerationConfig) -> list[Player]:
    return [p for lg in leagues for t in lg.teams for p in first_team(t, config)]


def mean_sd(values: list[float]) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    if len(values) == 1:
        return values[0], 0.0
    return statistics.fmean(values), statistics.stdev(values)


def strength_mean(players: Iterable[Player]) -> float:
    """選手たちの「強弱の項目」の現在の能力を、項目・選手すべてで平均したもの。"""
    values = [p.ratings[i] for p in players for i in strength_items_for(p.role)]
    return statistics.fmean(values) if values else float("nan")


def calibration_suggestion(leagues: list[League], config: GenerationConfig, target: float = 50.0) -> dict[str, dict[str, float]]:
    """一軍相当の平均を target にするための、基準値(base_mean)の調整案。

    全員の潜在能力に同じ点数を足しても選手の順位は変わらないため、
    「基準値 + (目標 - 一軍相当の平均)」で、一軍相当の平均がちょうど目標になる。
    """
    ft = first_team_players(leagues, config)
    result = {}
    for role, key in ((BATTER, "batter_base_mean"), (PITCHER, "pitcher_base_mean")):
        current_base = config["potential"][key]
        m = strength_mean(p for p in ft if p.role == role)
        result[role] = {"base": current_base, "first_team_mean": m, "suggested_base": current_base + (target - m)}
    return result


# ---- 表の組み立て ----

def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _f(x: float, digits: int = 1) -> str:
    return "-" if x != x else f"{x:.{digits}f}"  # x != x は NaN の判定


def _pct(n: int, total: int) -> str:
    return "-" if total == 0 else f"{100 * n / total:.1f}%"


def _share_table(counter: Counter, total: int, expected: dict[str, float], labels: dict[str, str]) -> str:
    rows = []
    for key, share in expected.items():
        rows.append([labels.get(key, key), str(counter.get(key, 0)), _pct(counter.get(key, 0), total), f"{100 * share:.1f}%"])
    return _table(["区分", "人数", "実際の割合", "設定値"], rows)


def _ability_table(players: list[Player], role: str, use_potential: bool = False) -> str:
    rows = []
    for item in items_for(role):
        vals = [(p.hidden.potential if use_potential else p.ratings)[item] for p in players if p.role == role]
        m, sd = mean_sd(vals)
        rows.append([ITEM_LABELS[item], _f(m), _f(sd)])
    return _table(["能力項目", "平均", "標準偏差"], rows)


def _ability_by_position_table(batters: list[Player]) -> str:
    rows = []
    for item in items_for(BATTER):
        row = [ITEM_LABELS[item]]
        for pos in FIELDER_POSITIONS:
            vals = [p.hidden.potential[item] for p in batters if p.position == pos]
            row.append(_f(mean_sd(vals)[0]))
        rows.append(row)
    return _table(["潜在能力"] + [POSITION_LABELS[pos] for pos in FIELDER_POSITIONS], rows)


def _ability_by_age_table(players: list[Player], role: str) -> str:
    labels = bin_labels()
    rows = []
    for item in items_for(role):
        row = [ITEM_LABELS[item]]
        for label in labels:
            vals = [p.ratings[item] for p in players if p.role == role and age_bin_label(p.age) == label]
            row.append(_f(mean_sd(vals)[0]))
        rows.append(row)
    return _table(["能力項目(現在)"] + labels, rows)


def build_report(
    leagues: list[League],
    config: GenerationConfig,
    draft_class: list[Player] | None = None,
) -> str:
    """生成結果の分布を、Markdown の表を含む文章で返す。"""
    players = [p for lg in leagues for p in lg.all_players()]
    batters = [p for p in players if p.role == BATTER]
    pitchers = [p for p in players if p.role == PITCHER]
    ft = first_team_players(leagues, config)
    out: list[str] = []

    out.append("# 生成したリーグの分布(確認用)")
    out.append(
        f"- リーグ数(生成回数): {len(leagues)}(シード: {', '.join(str(lg.seed) for lg in leagues[:5])}"
        f"{' …' if len(leagues) > 5 else ''})\n"
        f"- 球団数: {sum(len(lg.teams) for lg in leagues)} / 選手数: {len(players)}(打者 {len(batters)}、投手 {len(pitchers)})\n"
        f"- 設定ファイル: {config.source}"
    )

    # 球団の一覧(最初のリーグ)
    lg0 = leagues[0]
    rows = [[lg0.league_names[t.league_index], t.id, t.name, t.stadium, str(len(t.players))] for t in lg0.teams]
    out.append("## 球団(1つ目のリーグ)\n" + _table(["リーグ", "ID", "球団名", "本拠地", "人数"], rows))

    # 年齢
    ages = [p.age for p in players]
    m, sd = mean_sd([float(a) for a in ages])
    cfg_age = config["initial_ages"]
    out.append(
        "## 年齢の分布\n"
        f"- 全体: 平均 {m:.2f} 歳 / 標準偏差 {sd:.2f} 歳 / 最年少 {min(ages)} 歳 / 最年長 {max(ages)} 歳"
        f"(設定値: 平均 {cfg_age['mean']}、標準偏差 {cfg_age['sd']}、{cfg_age['min']}〜{cfg_age['max']} 歳)"
    )
    counter = Counter(age_bin_label(a) for a in ages)
    out.append(_table(["年齢層", "人数", "割合"], [[lb, str(counter[lb]), _pct(counter[lb], len(ages))] for lb in bin_labels()]))
    rows = []
    for t in lg0.teams:
        tm, tsd = mean_sd([float(p.age) for p in t.players])
        rows.append([t.name, _f(tm, 2), _f(tsd, 2)])
    out.append("球団別の平均年齢(1つ目のリーグ)\n\n" + _table(["球団", "平均年齢", "標準偏差"], rows))

    # 型・成長タイプの割合
    b_counter = Counter(p.hidden.archetype for p in batters)
    out.append(
        "## 型ごとの人数の割合\n### 打者の型\n"
        + _share_table(
            b_counter,
            len(batters),
            {k: v["share"] for k, v in config["batter_archetypes"].items()},
            {k: v["label"] for k, v in config["batter_archetypes"].items()},
        )
    )
    labels_b = {k: v["label"] for k, v in config["batter_archetypes"].items()}
    rows = []
    for pos in FIELDER_POSITIONS:
        group = [p for p in batters if p.position == pos]
        pc = Counter(p.hidden.archetype for p in group)
        expected = archetype_shares(config, pos)
        rows.append([POSITION_LABELS[pos], str(len(group))] + [f"{_pct(pc.get(k, 0), len(group))} ({100 * expected[k]:.0f}%)" for k in labels_b])
    out.append(
        "### ポジション別の打者の型の割合(実際の割合(設定値)。D-157)\n"
        + _table(["ポジション", "人数"] + [labels_b[k] for k in labels_b], rows)
    )
    q_counter = Counter(p.hidden.archetype.split("/")[1] for p in pitchers)
    out.append(
        "### 投手の球質\n"
        + _share_table(
            q_counter,
            len(pitchers),
            {k: v["share"] for k, v in config["pitcher_qualities"].items()},
            {k: v["label"] for k, v in config["pitcher_qualities"].items()},
        )
    )
    r_counter = Counter(p.position for p in pitchers)
    total_p = sum(config["roster"]["pitchers"].values())
    out.append(
        "### 投手の役割(球団の構成で人数が決まる)\n"
        + _share_table(
            r_counter,
            len(pitchers),
            {k: n / total_p for k, n in config["roster"]["pitchers"].items()},
            {k: v["label"] for k, v in config["pitcher_roles"].items()},
        )
    )
    g_counter = Counter(p.hidden.growth_type for p in players)
    out.append(
        "### 成長タイプ\n"
        + _share_table(
            g_counter,
            len(players),
            {k: v["share"] for k, v in config["aging"]["growth_types"].items()},
            {k: v["label"] for k, v in config["aging"]["growth_types"].items()},
        )
    )

    # 能力の平均と標準偏差
    for role, label in ((BATTER, "打者"), (PITCHER, "投手")):
        out.append(f"## {label}の能力(現在の能力)\n### 全選手\n" + _ability_table(players, role))
        out.append("### 一軍相当\n" + _ability_table(ft, role))
        out.append("### 年齢層別の平均\n" + _ability_by_age_table(players, role))
        if role == BATTER:
            out.append("### ポジション別の平均(全選手の潜在能力。D-157)\n" + _ability_by_position_table(batters))

    # 一軍相当の平均と調整案
    ftc = config["first_team"]
    sugg = calibration_suggestion(leagues, config)
    rows = []
    for role, label in ((BATTER, "打者"), (PITCHER, "投手")):
        s = sugg[role]
        rows.append([label, _f(s["first_team_mean"], 2), _f(s["base"], 2), _f(s["suggested_base"], 2)])
    out.append(
        "## 一軍相当の平均と、50 に近づけるための調整案\n"
        f"- 一軍相当の人数(1球団あたり): 投手 {ftc['pitchers']} 人、野手 {ftc['fielders']} 人"
        "(**仮置き**。一軍登録人数は未確認)\n"
        "- 平均は、強弱を表す項目(ゴロ/フライ傾向を除く)の現在の能力を、項目・選手すべてで平均したもの\n"
        "- 全員の潜在能力に同じ点数を足しても順位は変わらないため、「調整後の基準値」にすると一軍相当の平均はちょうど 50 になる\n\n"
        + _table(["区分", "一軍相当の平均", "今の基準値", "調整後の基準値(案)"], rows)
    )

    # 潜在能力と現在の能力の差(年齢層別)
    rows = []
    for lb in bin_labels():
        group = [p for p in players if age_bin_label(p.age) == lb]
        pot = [potential_overall(p) for p in group]
        cur = [overall(p) for p in group]
        gb = [p.ratings["gb_fb"] - p.hidden.potential["gb_fb"] for p in group]
        rows.append(
            [
                lb,
                str(len(group)),
                _f(mean_sd(pot)[0]),
                _f(mean_sd(cur)[0]),
                _f(mean_sd([a - b for a, b in zip(pot, cur)])[0]),
                _f(mean_sd(gb)[0], 2),
            ]
        )
    out.append(
        "## 潜在能力と現在の能力の差(年齢層別・初期選手)\n"
        "- 潜在能力・現在の能力は、強弱を表す項目の平均(総合値)\n"
        "- 「固定」グループ(ゴロ/フライ傾向)は年齢で変わらないので、差は 0 になるはず\n\n"
        + _table(["年齢層", "人数", "潜在能力の平均", "現在の能力の平均", "差(潜在−現在)", "ゴロ/フライ傾向の差"], rows)
    )

    # 新人(ドラフト候補)
    if draft_class:
        origins = config["draft"]["origins"]
        rows = []
        for key, o in origins.items():
            group = [p for p in draft_class if p.origin == key]
            ages_g = [float(p.age) for p in group]
            rows.append(
                [
                    o["label"],
                    str(len(group)),
                    _pct(len(group), len(draft_class)),
                    f"{100 * o['share']:.0f}%",
                    _f(mean_sd(ages_g)[0]),
                    _f(mean_sd([potential_overall(p) for p in group])[0]),
                    _f(mean_sd([overall(p) for p in group])[0]),
                ]
            )
        out.append(
            "## 新人(ドラフト候補)\n"
            f"- 人数: {len(draft_class)}。基準分布は全員共通で、生き残りバイアスは入れない\n"
            "- 出身区分(=年齢)によって潜在能力の平均が変わらないことを確認する\n\n"
            + _table(["出身", "人数", "割合", "設定値", "平均年齢", "潜在能力の平均", "現在の能力の平均"], rows)
        )

    # ポジション別人数(1球団)
    t0 = lg0.teams[0]
    pos_counter = Counter(p.position for p in t0.players)
    rows = [[POSITION_LABELS[k], str(v)] for k, v in pos_counter.items()]
    out.append(f"## ポジション別の人数({t0.name})\n" + _table(["ポジション", "人数"], rows))

    return "\n\n".join(out) + "\n"
