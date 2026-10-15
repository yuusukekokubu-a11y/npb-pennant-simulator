"""バランス調整(D-386〜D-389):自由契約市場の年俸と資金の例外・売れ残りの 2 周目・外国人の偏りを防ぐ仕組み・設定の点検。"""

import copy
from types import SimpleNamespace

import pytest

from pennant import api, foreign as foreignmod, market as marketmod, trade as trademod
from pennant.config import ConfigError
from pennant.contracts import team_salary
from pennant.procedure import load_draft_settings, validate_draft_settings
from pennant.fa import fa_settings
from pennant.negotiation import load_negotiation_settings, validate_negotiation_settings

NEG = load_negotiation_settings()
TS = trademod.load_trade_settings()


@pytest.fixture(scope="module")
def base():
    return api.Game.new(5, [None] * 12, 0, season_seed=9, baselines="default")


def fresh(g):
    return api.Game(copy.deepcopy(g.state), dirty=False)


def test_settings_and_validation():
    fa = fa_settings(NEG)
    assert fa["market_salary_rate"] == 0.3 and fa["market_max_wishes"] == 10 and fa["market_bargain"] is True and fa["auto_max_offers"] == 8
    for key, bad in (("market_salary_rate", 5), ("market_max_wishes", -1), ("auto_max_offers", 100), ("market_bargain", "yes")):
        data = copy.deepcopy(NEG.data)
        data["fa"][key] = bad
        with pytest.raises(ConfigError, match=key):
            validate_negotiation_settings(data)
    ds = load_draft_settings()
    assert ds.foreign["ai_min"] == 3 and ds.foreign["exempt_salary"] == ds.foreign["salary"]["D"]
    data = copy.deepcopy(ds.data)
    data["foreign"]["ai_min"] = -1
    with pytest.raises(ConfigError, match="ai_min"):
        validate_draft_settings(data)
    assert TS.data["ai"]["min_foreign"] == 3
    data = copy.deepcopy(TS.data)
    data["ai"]["min_foreign"] = 99
    with pytest.raises(ConfigError, match="min_foreign"):
        trademod.validate_trade_settings(data)


def test_market_price_and_minimum_exemption(base):
    g = fresh(base)
    ctx = g._contract_ctx()
    minimum, rounding = int(ctx.settings.minimum), int(ctx.settings.rounding)
    rate = float(fa_settings(ctx.negotiation)["market_salary_rate"])
    proc = SimpleNamespace(released=[], fa_info={})
    team = g._team("T01")
    p = team.players[0]
    calc = 10000
    ctx.salary = lambda player, team_id: (calc, 1, 0.5)  # 新規開始の直後は成績がなく、算定は最低年俸なので、算定を決めて確かめる
    salary, years, _ = marketmod.market_terms(proc, p, "T01", ctx, {p.id: "T02"})
    assert salary == max(minimum, int(round(calc * rate / rounding)) * rounding) < calc and years >= 1  # 手放された選手は安く(D-386)
    assert marketmod.market_terms(proc, p, "T01", ctx, {})[0] == minimum  # 指名されなかった候補は最低年俸
    assert marketmod.market_terms(proc, p, "T01", ctx, {p.id: "T02"}, bargain=True)[0] == minimum  # 売れ残りの 2 周目(D-389)
    # 資金を超えていても、最低年俸の選手は獲得できる(自動補充と同じ)
    g.state.team_funds["T01"] = team_salary(team) - 1 + g.budget_info("T01")["upkeep"]
    assert ctx.cap("T01") < team_salary(team)
    assert not marketmod.over_cap(team, minimum, 0, ctx) and marketmod.over_cap(team, minimum + rounding, 0, ctx)


def test_foreign_minimum_exemption(base):
    g = fresh(base)
    ctx = g._contract_ctx()
    ds = g.state.draft_settings
    team = g._team("T01")
    foreigners = [p for p in team.players if p.foreign]
    for p in foreigners[2:]:  # 外国人を 2 人にする
        p.foreign = False
    assert foreignmod.foreign_count(team) == 2
    g.state.team_funds["T01"] = 0  # 資金を超えている
    team.players = team.players[:69]
    proc = SimpleNamespace(foreign_salary={"cheap": int(ds.foreign["exempt_salary"]), "dear": int(ds.foreign["exempt_salary"]) + 100})
    assert foreignmod.can_take(team, proc, "cheap", ctx, ds) and not foreignmod.can_take(team, proc, "dear", ctx, ds)
    assert not foreignmod.can_take(team, proc, "cheap", ctx)  # 設定を渡さなければ今までどおり
    foreigners[2].foreign = True  # 3 人いれば例外はない
    assert not foreignmod.can_take(team, proc, "cheap", ctx, ds)


def test_trade_keeps_three_foreigners(base):
    g = fresh(base)
    ctx = g._trade_ctx()
    team = g._team("T02")
    foreigners = [p for p in team.players if p.foreign]
    for p in foreigners[3:]:
        p.foreign = False
    assert foreignmod.foreign_count(team) == 3
    other = next(p for p in g._team("T03").players if not p.foreign)
    ok, why = trademod.ai_answer(team, [foreigners[0]], [other], ctx, ctx.rate, g.state.gen_config, TS)
    assert not ok and "外国人" in why
    other_f = next(p for p in g._team("T03").players if p.foreign)
    _, why = trademod.ai_answer(team, [foreigners[0]], [other_f], ctx, ctx.rate, g.state.gen_config, TS)
    assert "外国人" not in why  # 外国人どうしの交換は人数が減らない
