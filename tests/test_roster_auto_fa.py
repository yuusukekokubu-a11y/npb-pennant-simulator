"""自動 FA の訂正(D-390)・自動補充をなくす(D-391)・最低年俸の契約は資金に関わらず(D-394)・保存 版 23(D-393)。"""

import copy
import pytest

from pennant import api, market as marketmod
from pennant.config import ConfigError
from pennant.contract_stage import can_afford
from pennant.contracts import team_salary
from pennant.fa import fa_settings
from pennant.negotiation import load_negotiation_settings, validate_negotiation_settings
from pennant.savegame import SAVE_FORMAT_VERSION, SaveDataError, load_game, save_game


@pytest.fixture(scope="module")
def auto_season_end():
    """自動 FA で 1 シーズン目を終える直前。T01 の選手を 3 通りに整えておく。"""
    g = api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default", rules={"auto_fa": True})
    g.advance_to_end()
    return g


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def test_auto_fa_follows_fa_rights(auto_season_end):
    g = fresh(auto_season_end)
    team = g._team("T01")
    year = g.state.year
    batters = [p for p in team.players if not p.foreign and p.age <= 30][:3]  # 引退しない年齢
    holder, free, plain = batters
    holder.fa_seasons, holder.fa_free = 9, False
    holder.contract["until"] = year + 2  # 契約が残っていても、FA 権を取ったオフは FA
    free.fa_seasons, free.fa_free = 0, True
    free.contract["until"] = year  # 一度 FA 権を取った選手は、満了のたびに FA
    plain.fa_seasons, plain.fa_free = 0, False
    plain.contract["until"] = year  # 権利のない選手は更改
    g.year_end()
    proc = g.state.procedure
    info = proc.fa_info
    assert holder.id in info and info[holder.id]["auto"] and holder.fa_free and holder.contract is None
    assert free.id in info and info[free.id]["auto"]
    assert plain.id not in info and proc.negotiations[plain.id]["status"] == "accepted" and not plain.fa_free
    # 権利のない選手と、契約が残る選手(権利も取っていない)は FA にならない
    assert all(proc.negotiations[pid]["context"].get("auto_fa") for pid in info)


def test_save_keeps_fa_free(auto_season_end):
    g = fresh(auto_season_end)
    p = g._team("T02").players[0]
    p.fa_free = True
    again = load_game(save_game(g.state))
    assert SAVE_FORMAT_VERSION >= 23 and next(q for q in again.league.all_players() if q.id == p.id).fa_free is True
    bad = copy.deepcopy(g.state)
    bad.league.teams[1].players[0].fa_free = "yes"
    with pytest.raises(SaveDataError, match="fa_free"):
        load_game(save_game(bad))


def test_take_minimum_substitutes_within_role(auto_season_end):
    g = fresh(auto_season_end)
    team = g._team("T03")
    pool = [p for t in g.state.league.teams[4:6] for p in t.players]
    catcher = next(p for p in pool if p.position == "C")
    infielder = next(p for p in pool if p.position == "2B")
    pitcher = next(p for p in pool if p.role == "pitcher")
    for p in (catcher, infielder, pitcher):
        for t in g.state.league.teams:
            if p in t.players:
                t.players.remove(p)
        p.team_id = None
    g.year_end()
    ctx = g._contract_ctx()
    proc = g.state.procedure
    proc.market = [infielder, pitcher]
    settings = g.state.draft_settings
    sd = g.state.scout_sd_map()["T03"]
    took = marketmod.take_minimum(team, "batter", "C", proc, ctx, sd, settings)  # 捕手がいないので、ほかの野手で代用
    assert took is infielder and infielder in team.players and infielder.contract["salary"] == ctx.settings.minimum
    assert proc.picks[-1]["phase"] == "minimum" and proc.picks[-1]["slot"] == "C"
    assert marketmod.take_minimum(team, "batter", "C", proc, ctx, sd, settings) is None  # 野手がいなければ取れない(投手では代用しない)
    proc.market.append(catcher)
    assert marketmod.take_minimum(team, "batter", "C", proc, ctx, sd, settings) is catcher


def test_minimum_salary_ignores_funds(auto_season_end):
    g = fresh(auto_season_end)
    team = g._team("T01")
    ctx = g._contract_ctx()
    g.state.team_funds["T01"] = 0
    assert ctx.cap("T01") < team_salary(team)
    assert can_afford(team, ctx.settings.minimum, ctx) and not can_afford(team, ctx.settings.minimum + ctx.settings.rounding, ctx)


def test_roster_target_setting():
    neg = load_negotiation_settings()
    assert fa_settings(neg)["market_roster_target"] == 65 and fa_settings(None)["market_roster_target"] == 65  # 事前運転も同じ
    data = copy.deepcopy(neg.data)
    data["fa"]["market_roster_target"] = 99
    with pytest.raises(ConfigError, match="market_roster_target"):
        validate_negotiation_settings(data)


def test_offseason_keeps_rosters_without_auto_fill(auto_season_end):
    g = fresh(auto_season_end)
    g.year_end()
    g.offseason_auto()
    sizes = [len(t.players) for t in g.state.league.teams]
    assert max(sizes) <= 70 and min(sizes) >= 60  # 70 人は上限だけ。AI は 65 人くらい(D-391)
    assert not any(x["phase"] == "fill" for x in g.state.transactions)  # 自動補充はない
