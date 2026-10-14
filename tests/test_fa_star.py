"""主力級の FA の調整(主力級ほど自分の価値を高く見る。D-322、D-325、D-329、D-331)の確認。
作り直し②で更改での引き留めはなくなり(D-359)、期待の倍率は FA 市場の判定と AI の提示だけに使う。"""

import copy
import json

import pytest

from pennant import api
from pennant import fa as famod
from pennant.config import ConfigError
from pennant.negotiation import judge, load_negotiation_settings, validate_negotiation_settings

NEG = load_negotiation_settings()
FA = famod.fa_settings(NEG)
STAR = FA["star"]


def test_star_multiplier_formula_and_cap():
    """期待の倍率 = min(上限, 1 + 上げ幅 × max(0, 見込みの WAR − 始まり))。"""
    assert STAR["start"] == 1.0 and STAR["per_war"] == 0.1 and STAR["max_multiplier"] == 1.25
    m = [famod.star_multiplier(NEG, e) for e in (0.3, 1.0, 2.0, 3.0, 3.5, 6.0)]
    assert m == pytest.approx([1.0, 1.0, 1.1, 1.2, 1.25, 1.25])
    # 設定にない旧版(期待なし)
    old = copy.deepcopy(NEG.data)
    old["fa"].pop("star")
    assert famod.star_multiplier(validate_negotiation_settings(old), 5.0) == 1.0


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["fa"]["star"].update(max_multiplier=0.9), "fa.star.max_multiplier"),
        (lambda d: d["fa"]["star"].update(per_war=-0.1), "fa.star.per_war"),
        (lambda d: d["fa"]["star"].update(reason=""), "fa.star.reason"),
        (lambda d: d["fa"].update(star=3), "fa.star"),
    ],
)
def test_star_settings_validation(change, message):
    data = copy.deepcopy(NEG.data)
    change(data)
    with pytest.raises(ConfigError, match=message.replace(".", r"\.")):
        validate_negotiation_settings(data)


def test_expectation_raises_the_salary_base():
    """年俸の軸の基準が 算定 × 倍率 になる:同じ提示でも主力級は満足度が下がり、倍率どおりに上げれば元に戻る。"""
    pref = {"salary": 0.4, "playing_time": 0.4, "winning": 0.2}
    ctx = {"rank": 0, "slots": 2.0, "standing": 3, "league_size": 6}
    plain = judge(NEG, pref, 1, 10000, 10000, 29, ctx, 0.0)
    star = judge(NEG, pref, 1, 10000, 10000, 29, {**ctx, "salary_expect": 1.2}, 0.0)
    raised = judge(NEG, pref, 1, 12000, 10000, 29, {**ctx, "salary_expect": 1.2}, 0.0)
    assert star["score"] < plain["score"] and star["values"]["salary"] < plain["values"]["salary"]
    assert raised["score"] == pytest.approx(plain["score"]) and raised["values"]["playing_time"] == plain["values"]["playing_time"]


@pytest.fixture(scope="module")
def season_end():
    g = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default")
    g.advance(125)
    g.year_end()
    return g


@pytest.mark.slow
def test_fa_market_uses_the_expectation(season_end):
    """FA 市場の AI は、主力級に期待の倍率を考えた年俸で提示する(D-325、D-331)。期待の倍率と志望の重みは画面に出ない。"""
    g = api.Game(copy.deepcopy(season_end.state), dirty=False)
    proc = g.state.procedure
    stars = [e for e in proc.negotiations.values() if e["context"].get("fa_holder") and e["expected"] >= 2.0]
    assert all("salary_expect" not in e["context"] for e in proc.negotiations.values())  # 期待の倍率は保存しない(見込みから毎回求める)
    text = json.dumps([g.offseason_view(), g.contract_table("all")], ensure_ascii=False)
    for key in ("salary_expect", "preference", "max_multiplier", "per_war"):
        assert f'"{key}"' not in text
    g.offseason_stage_auto()
    if g.offseason_view()["stage"] != "fa":
        pytest.skip("宣言した選手がいない")
    ctx = g._contract_ctx()
    sizes = {}
    for t in g.state.league.teams:
        sizes[t.league_index] = sizes.get(t.league_index, 0) + 1
    star_ids = [pid for pid, x in proc.fa_info.items() if x["expected"] >= 2.0 and x["status"] == "open"]
    for team in g.state.league.teams:
        offers = famod.ai_offers(team, proc, ctx, sizes)
        for pid, (years, salary) in offers.items():
            info = proc.fa_info[pid]
            m = famod.star_multiplier(ctx.negotiation, info["expected"])
            calc = int(info["calc_salary"])
            assert calc <= salary <= calc * FA["round_multipliers"][-1] * STAR["max_multiplier"] + ctx.settings.rounding
            if pid in star_ids and m > 1.0:
                assert salary >= round(calc * m / ctx.settings.rounding) * ctx.settings.rounding - ctx.settings.rounding
    text = json.dumps([g.offseason_view(), g.decision_table("fa")], ensure_ascii=False)
    for key in ("salary_expect", "preference", "max_multiplier"):
        assert f'"{key}"' not in text
    assert stars is not None
