"""新人と外国人の見立て(D-418〜D-420):能力と特殊能力を見立てで見せ、入団すると本当の数字になる。"""

import json

from pennant.config import load_generation_config, load_name_parts
from pennant.draft import load_draft_settings
from pennant.generate import generate_league
from pennant.scouting import ScoutReport, estimate_skills, estimated_player, item_sd, item_stats, grade_cuts, public_estimate, quick_value, report
from pennant.skills import default_settings

CONFIG = load_generation_config()
DS = load_draft_settings()
PLAYERS = generate_league(7, CONFIG, load_name_parts()).all_players()
CUTS = grade_cuts(PLAYERS, DS.grade_shares)
STATS = item_stats(PLAYERS)


def _rep(p, team="T01", seed=11, level="medium"):
    return report(p, team, seed, DS.level_sd(level), CUTS, STATS, DS.comment, DS.skill_rates)


def test_estimate_is_the_scouting_values_in_the_public_shape():
    p = next(x for x in PLAYERS if x.role == "batter")
    rep = _rep(p)
    pub = public_estimate(CONFIG, p, rep)
    q = estimated_player(p, rep)
    assert [a["key"] for a in pub["abilities"]] == ["trajectory", "meet", "power", "speed", "arm", "defense", "catch"]
    assert all(q.abilities[k] == rep.items[k] for k in rep.items) and p.abilities != q.abilities  # 元の選手は変えない
    assert pub == public_estimate(CONFIG, p, _rep(p))  # 同じ候補は何度見ても同じ
    assert quick_value(p, "T01", 11, DS.level_sd("medium"), CUTS) == (rep.overall, rep.grade)  # AI の判断の値は今までと同じ
    assert ScoutReport.from_dict(json.loads(json.dumps(rep.to_dict()))).skills == rep.skills  # 保存して戻しても同じ


def test_skill_estimate_misses_and_adds_with_the_size_of_the_error():
    settings = default_settings()
    have = [p for p in PLAYERS if p.skills]
    def rates(level):
        sd = DS.level_sd(level)
        miss = sum(len(set(p.skills) - set(estimate_skills(p, "T02", 5, sd, DS.skill_rates))) for p in have) / sum(len(p.skills) for p in have)
        extra = sum(1 for p in PLAYERS if set(estimate_skills(p, "T02", 5, sd, DS.skill_rates)) - set(p.skills)) / len(PLAYERS)
        return miss, extra
    small, medium, large = rates("small"), rates("medium"), rates("large")
    assert small[0] < medium[0] < large[0] and small[1] < medium[1] < large[1]
    assert abs(medium[0] - 0.05 * item_sd(DS.level_sd("medium"))) < 0.05 and abs(medium[1] - 0.04 * item_sd(DS.level_sd("medium"))) < 0.05
    for p in PLAYERS[:300]:  # ないものを「ある」と見るときも、同じ対は 1 つまで・役割どおり
        est = estimate_skills(p, "T02", 5, DS.level_sd("large"), DS.skill_rates)
        pairs = [settings.skills[k]["pair"] for k in est]
        assert len(pairs) == len(set(pairs)) and all(settings.skills[k]["role"] == p.role for k in est)


def test_old_entry_records_have_no_skill_estimate():
    p = PLAYERS[0]
    d = _rep(p).to_dict()
    d.pop("skills")
    old = ScoutReport.from_dict(d)
    assert old.skills is None and public_estimate(CONFIG, p, old)["skills"] is None  # 画面では「記録なし」
