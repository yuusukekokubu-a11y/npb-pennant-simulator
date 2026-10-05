"""主力級の FA の調整(主力級ほど自分の価値を高く見る。D-322〜D-325)の確認。"""

import copy
import json

import pytest

from pennant import api
from pennant import fa as famod
from pennant.config import ConfigError
from pennant.negotiation import draw_preference, judge, load_negotiation_settings, noise_for, reason_text, validate_negotiation_settings

NEG = load_negotiation_settings()
FA = famod.fa_settings(NEG)
STAR = FA["star"]


def test_star_multiplier_formula_and_cap():
    """期待の倍率 = min(上限, 1 + 上げ幅 × max(0, 見込みの WAR − 始まり))。上限は「なし」の上限(1.3 倍)より小さい。"""
    assert STAR["start"] == 1.0 and STAR["per_war"] == 0.1 and STAR["max_multiplier"] == 1.25 < NEG.money_none["max_ratio"]
    m = [famod.star_multiplier(NEG, e) for e in (0.3, 1.0, 2.0, 3.0, 3.5, 6.0)]
    assert m == pytest.approx([1.0, 1.0, 1.1, 1.2, 1.25, 1.25])
    # 設定にない旧版(期待なし)
    old = copy.deepcopy(NEG.data)
    old["fa"].pop("star")
    assert famod.star_multiplier(validate_negotiation_settings(old), 5.0) == 1.0


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["fa"]["star"].update(max_multiplier=1.3), "fa.star.max_multiplier"),
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
    ctx = {"rank": 0, "slots": 2.0, "standing": 3, "league_size": 6, "fa_holder": True, "threshold_add": FA["threshold_add"]}
    plain = judge(NEG, pref, 1, 10000, 10000, 29, ctx, "none", 0.0)
    star = judge(NEG, pref, 1, 10000, 10000, 29, {**ctx, "salary_expect": 1.2}, "none", 0.0)
    raised = judge(NEG, pref, 1, 12000, 10000, 29, {**ctx, "salary_expect": 1.2}, "none", 0.0)
    assert star["score"] < plain["score"] and star["values"]["salary"] < plain["values"]["salary"]
    assert raised["score"] == pytest.approx(plain["score"]) and raised["values"]["playing_time"] == plain["values"]["playing_time"]


def _acceptance(expected: float, ratio: float, years: int, rule: str = "none", n: int = 400) -> float:
    """主力級の FA 権保持者(29 歳・一軍の出場が見込める)が、算定 × ratio・years 年の提示を受ける割合(志望と乱数は本物の引き方で)。"""
    expect = famod.star_multiplier(NEG, expected)
    ok = 0
    for i in range(n):
        pid = f"star{i}"
        pref = draw_preference(pid, 7, NEG)
        ctx = {"rank": 0, "slots": 1.5, "standing": 3, "league_size": 6, "fa_holder": True, "threshold_add": FA["threshold_add"], "salary_expect": expect}
        calc = 20000
        ok += judge(NEG, pref, years, int(calc * ratio), calc, 29, ctx, rule, noise_for(11, pid, NEG))["accepted"]
    return ok / n


def test_retention_by_salary_and_years():
    """引き留め(D-323):年俸を上げるか、複数年を提示すると受けやすくなる。「なし」の上限(1.3 倍)の中で引き留められる。"""
    auto = _acceptance(3.0, 1.0, 1)
    up15 = _acceptance(3.0, 1.15, 1)
    top = _acceptance(3.0, NEG.money_none["max_ratio"], 1)
    multi = _acceptance(3.0, 1.0, 3)
    plain = _acceptance(0.8, 1.0, 1)  # 期待のない選手(見込み 0.8)
    assert auto < plain  # 主力級は自動案を断りやすい
    assert auto < up15 < top and top >= plain  # 上限の中で、期待のない選手と同じくらいまで受けやすくなる
    assert multi > auto  # 複数年でも受けやすくなる(効き目は年俸より小さい)
    assert _acceptance(3.0, 1.3, 1, "standard") > _acceptance(3.0, 1.0, 1, "standard")


def test_reason_text_marks_star():
    rec = {"reason": "salary", "star": True}
    assert reason_text(NEG, rec) == f"{NEG.reason('salary')}({STAR['reason']})"
    assert reason_text(NEG, {"reason": "salary"}) == NEG.reason("salary")
    assert reason_text(NEG, {"reason": None}) == "条件が合わない"


@pytest.fixture(scope="module")
def season_end():
    g = api.Game.new(2, [None] * 12, 0, season_seed=5, baselines="default", money_rule="none")
    g.advance(125)
    g.year_end()
    return g


@pytest.mark.slow
def test_renewal_and_fa_market_use_the_expectation(season_end):
    """更改の判定と FA 市場の判定の両方に期待の倍率を使う。FA 市場の AI は、主力級に期待の倍率を考えた年俸で提示する(「なし」の上限まで)。
    期待の倍率と志望の重みは、答え合わせモードがオフの画面に出ない(D-325、受け入れ条件 7)。"""
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
            assert calc <= salary <= calc * NEG.money_none["max_ratio"] + 1
            if pid in star_ids and m > 1.0:
                assert salary >= round(calc * m / ctx.settings.rounding) * ctx.settings.rounding - ctx.settings.rounding
    text = json.dumps([g.offseason_view(), g.decision_table("fa")], ensure_ascii=False)
    for key in ("salary_expect", "preference", "max_multiplier"):
        assert f'"{key}"' not in text
    assert stars is not None
