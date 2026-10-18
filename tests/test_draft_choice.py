"""バランス調整②(D-396〜D-398):ドラフトは資金に関わらず指名・何人取るかは球団が決める(選択終了)・指名されなかった候補は市場に出さない。"""

import copy

import pytest

from pennant import api, draft as draftmod


@pytest.fixture(scope="module")
def at_draft():
    """自球団 T01 の 1 シーズン目を終え、ドラフトの段階の入口まで進めたところ。"""
    g = api.Game.new(4, [None] * 12, 0, season_seed=7, baselines="default")
    g.advance_to_end()
    g.year_end()
    while g.offseason_view()["stage"] != "draft":
        if g.offseason_view()["stage"] == "foreign":
            g.offseason_next()
        else:
            g.offseason_stage_auto()
    return g


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def test_draft_ignores_funds(at_draft):
    g = fresh(at_draft)
    g.state.team_funds["T01"] = 0  # 資金を超えていても指名できる(D-396)
    proc = g.state.procedure
    assert proc.bidding() and draftmod.bid_blocked(g._team("T01"), proc, g._contract_ctx()) is None
    target = proc.candidates[0]
    g.offseason_pick(target.id)
    assert any(x["phase"] == "draft" and x["round"] == 1 and x["team_id"] == "T01" for x in proc.picks)
    assert not any(x["phase"] == "draft" and x.get("note") == "budget" for x in proc.picks)  # AI も資金でパスしない


def test_pass_ends_my_draft(at_draft):
    g = fresh(at_draft)
    proc = g.state.procedure
    g.offseason_pick(proc.candidates[0].id)
    while proc.bidding():  # 抽選に外れたら入札し直す
        g.offseason_pick(proc.candidates[0].id)
    v = g.offseason_view()
    assert v["is_my_turn"] and v["round"] >= 2
    g.offseason_pass()  # 一度パスしたら、その年の指名は終わり(選択終了。D-397)
    assert "T01" in draftmod.draft_ended(proc) and draftmod.phase_finished(proc)
    v = g.offseason_view()
    assert not v["is_my_turn"]
    mine = [x for x in proc.picks if x["phase"] == "draft" and x["team_id"] == "T01"]
    assert sum(1 for x in mine if x["player_id"]) >= 1 and sum(1 for x in mine if not x["player_id"]) == 1  # パスは 1 回だけ記録


def test_ai_picks_to_target_with_minimum(at_draft):
    g = fresh(at_draft)
    proc = g.state.procedure
    before = {t.id: len(t.players) for t in g.state.league.teams}
    g.offseason_pass()  # あなたは 1 巡目で終える。AI は最後まで
    ds = g.state.draft_settings
    assert draftmod.phase_finished(proc) and proc.round <= ds.rounds + 1
    for t in g.state.league.teams:
        if t.id == "T01":
            continue
        picked = sum(1 for x in proc.picks if x["phase"] == "draft" and x["team_id"] == t.id and x["player_id"])
        assert picked <= ds.rounds
        assert picked >= min(ds.ai_draft_min_picks, 70 - before[t.id])  # 最低 4 人(空き枠があれば)
        assert len(t.players) <= 70
        if picked > ds.ai_draft_min_picks:
            assert len(t.players) <= max(ds.ai_draft_target, before[t.id] + ds.ai_draft_min_picks)  # 目安を超えて取りすぎない


def test_undrafted_candidates_leave(at_draft):
    g = fresh(at_draft)
    g.offseason_pass()
    while g.offseason_view()["stage"] != "market":
        g.offseason_stage_auto()
    proc = g.state.procedure
    ids = {p.id for p in proc.market}
    assert proc.candidates == [] and ids  # 指名されなかった候補は市場に出さない(D-398)
    former = draftmod_former(proc)
    assert all(pid in former for pid in ids)  # 市場は手放された選手と FA で決まらなかった選手だけ


def draftmod_former(proc):
    from pennant.market import former_teams

    return former_teams(proc)
