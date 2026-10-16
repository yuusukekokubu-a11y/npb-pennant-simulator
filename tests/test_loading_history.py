"""ロード画面の「歴史」(D-406):事前運転の 1 年ごとの出来事。読むだけで、事前運転の結果は変えない。"""

from pennant.ability_model import overall
from pennant.config import load_generation_config, load_name_parts
from pennant.generate import generate_league
from pennant.offseason import STRONGEST_TOP, load_offseason_settings, prerun

SETTINGS = load_offseason_settings()
CONFIG = load_generation_config()
PARTS = load_name_parts()


def test_chronicle_reads_without_changing_the_result():
    a = generate_league(5, CONFIG, PARTS)
    b = generate_league(5, CONFIG, PARTS)
    events = []
    prerun(a, 5, CONFIG, PARTS, SETTINGS, years=3)
    prerun(b, 5, CONFIG, PARTS, SETTINGS, years=3, chronicle=events.append)
    assert [(p.id, dict(p.ratings), p.team_id) for p in a.all_players()] == [(p.id, dict(p.ratings), p.team_id) for p in b.all_players()]
    assert [e["year"] for e in events] == [1, 2, 3]
    last = events[-1]
    names = {t.id: t.name for t in b.teams}
    assert last["rookie"] and last["rookie"]["team_name"] == names[last["rookie"]["team_id"]]
    assert last["retired"] and last["retired"]["age"] >= 20 and last["retired"]["team_name"]
    best = max(b.teams, key=lambda t: (sum(sorted((overall(p) for p in t.players), reverse=True)[:STRONGEST_TOP]), t.id))
    assert last["strongest"] == {"team_id": best.id, "team_name": best.name}  # 上位 25 人の総合値の合計が最も大きい球団
