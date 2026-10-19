"""12 球団の資金を選ぶ(D-490〜D-494):新規開始の 12 球団の資金力・自動の数合わせ・偏った設定・始めた後の変更・保存。"""

import io
import json
import zipfile
from collections import Counter

import pytest

from pennant import api, template as T
from pennant.contract_stage import over_cap, state_context
from pennant.contracts import HUGE, TIERS, apply_tier_changes, assign_tiers, base_funds, changed_funds, fixed_tiers, load_contract_settings
from pennant.facilities import default_settings as facility_settings
from pennant.savegame import SAVE_FORMAT_VERSION, load_game, save_game

CS = load_contract_settings()
IDS = [f"T{i:02d}" for i in range(1, 13)]
COUNTS = {"large": 3, "medium": 6, "small": 3}


def _base(t: str) -> str:
    return "large" if t == HUGE else t


def _counts(tiers) -> Counter:
    return Counter(_base(t) for t in tiers)


def _start(**kw):
    args = {"team_names": [None] * 12, "my_team_index": None, "season_seed": 5}
    args.update(kw)
    return T.start_from_template(**args)


def _no_overrun(state) -> None:
    ctx = state_context(state)
    assert all(over_cap(t, ctx) == 0 for t in state.league.teams)  # 資金を超える初期契約はない(D-492)


# ---- 自動の数合わせ(D-491) ----

def test_assign_tiers_with_many_choices():
    fixed = {"T01": "huge", "T02": "small", "T07": "medium"}
    tiers = assign_tiers(IDS, 3, CS, fixed)
    assert all(tiers[k] == v for k, v in fixed.items())
    assert _counts(tiers.values()) == Counter(COUNTS)  # 選んだ残りで 大 3・中 6・小 3
    assert tiers == assign_tiers(IDS, 3, CS, dict(fixed))  # シードから決まる


def test_assign_tiers_all_chosen_and_overshoot():
    assert assign_tiers(IDS, 3, CS, {t: "medium" for t in IDS}) == {t: "medium" for t in IDS}  # 全球団「中」も認める(D-492)
    assert assign_tiers(IDS, 3, CS, {t: HUGE for t in IDS}) == {t: HUGE for t in IDS}
    over = {t: "large" for t in IDS[:5]}  # 大をいつもの数より多く選んだ:自動の 7 球団に大は入れず、中 → 小 の順に埋める
    tiers = assign_tiers(IDS, 3, CS, over)
    rest = [tiers[t] for t in IDS[5:]]
    assert Counter(rest) == Counter({"medium": 6, "small": 1})


def test_fixed_tiers_checks_values():
    assert fixed_tiers(None) == {} and fixed_tiers(["auto"] * 12) == {} and fixed_tiers([""] * 11 + [None]) == {}
    assert fixed_tiers(["small"] + ["auto"] * 11, "huge", 0) == {0: "small"}  # 表の自球団の行が先
    assert fixed_tiers(["auto"] * 12, "huge", 2) == {2: "huge"}  # 自球団の行が自動なら、前からの my_tier
    assert fixed_tiers(None, "large", None) == {}  # 観戦のみでは my_tier は使わない
    with pytest.raises(ValueError, match="12 球団"):
        fixed_tiers(["small"] * 11)
    with pytest.raises(ValueError, match="3番目の球団"):
        fixed_tiers(["auto", "auto", "giant"] + ["auto"] * 9)
    with pytest.raises(ValueError, match="自球団の資金力"):
        fixed_tiers(None, "giant", 0)


# ---- 新しくリーグを作る(D-490、D-492) ----

@pytest.fixture(scope="module")
def even_game():
    return api.Game.new(4, [None] * 12, 1, season_seed=4, baselines="default", team_tiers=["medium"] * 12)


def test_new_game_with_all_medium(even_game):
    state = even_game.state
    assert state.budget_tiers == {t: "medium" for t in IDS} and state.tier_start == state.budget_tiers and state.tier_next == {}
    assert set(state.team_funds.values()) == {base_funds("medium", CS)}
    _no_overrun(state)
    with pytest.raises(ValueError, match="資金力"):
        api.Game.new(4, [None] * 12, 1, season_seed=4, baselines="default", team_tiers=["medium"] * 11 + ["giant"])


# ---- 用意したリーグ(D-490、D-491) ----

@pytest.mark.parametrize("tiers", [["small"] * 12, [HUGE] * 12, [HUGE] + ["small"] * 11, ["small"] + [HUGE] * 11, ["large"] * 5 + ["auto"] * 7, ["auto", "small", "auto", HUGE, "medium"] + ["auto"] * 7])
def test_template_with_team_tiers(tiers):
    base = T.template_files()
    state = _start(team_tiers=tiers, my_team_index=3)
    chosen = {IDS[i]: t for i, t in enumerate(tiers) if t != "auto"}
    assert all(state.budget_tiers[k] == v for k, v in chosen.items())
    target = Counter(COUNTS)
    target.subtract(_counts(chosen.values()))
    auto = [state.budget_tiers[t] for t in IDS if t not in chosen]
    got = _counts(auto)
    assert all(got[k] <= max(0, target[k]) for k in TIERS) or len(auto) == 0  # 自動の球団は、いつもの数を超えない
    assert sum(got.values()) == len(auto)
    assert state.tier_start == state.budget_tiers and state.tier_next == {}
    fs = facility_settings()
    template = load_game(base[1])
    for t in state.league.teams:
        if state.budget_tiers[t.id] != template.budget_tiers[t.id]:  # 変わった球団は資金と設備を求め直す
            assert state.team_funds[t.id] == base_funds(state.budget_tiers[t.id], CS)
            assert t.facilities == {k: fs.ai_level(state.team_funds[t.id]) for k in fs.kinds}
    _no_overrun(state)


def test_template_auto_keeps_template_tiers_when_counts_fit():
    """選んだ数がいつもの数のままなら、自動の球団はテンプレートの規模を残す(入れ替えが要るぶんだけ動く)。"""
    template = load_game(T.template_files()[1])
    same = [template.budget_tiers[t] for t in IDS]
    state = _start(team_tiers=same)
    assert state.budget_tiers == template.budget_tiers and state.team_funds == template.team_funds


# ---- 始めた後の変更(D-493) ----

def test_change_is_pending_until_next_offseason():
    g = api.Game(_start(my_team_index=0), dirty=False)
    funds = dict(g.state.team_funds)
    cur = g.state.budget_tiers["T05"]
    new = "small" if cur != "small" else "large"
    info = g.set_team_tier("T05", new)
    assert g.state.budget_tiers["T05"] == cur and g.state.team_funds == funds  # まだ効かない
    assert g.state.tier_next == {"T05": new} and info["manual"] and info["next"] == new and "次のオフの契約から" in info["source_text"]
    assert g.dirty
    assert g.team("T05")["tier_choice"]["next"] == new
    assert g.set_team_tier("T05", cur)["next"] is None and g.state.tier_next == {}  # 今と同じにしたら取り消し
    assert not g.team_tier("T05")["manual"]
    g.set_team_tier("T05", new)
    assert g.reset_team_tier("T05")["manual"] is False and g.state.tier_next == {}  # 新規開始のときに戻す
    with pytest.raises(ValueError, match="資金力"):
        g.set_team_tier("T05", "auto")
    w = api.Game(_start(), dirty=False)  # 観戦のみでも変えられる
    assert w.set_team_tier("T01", HUGE)["next"] == HUGE


def test_apply_tier_changes_resets_funds_to_the_new_base():
    tiers = {"T01": "small", "T02": "large", "T03": "medium"}
    funds = {"T01": 30000, "T02": 52000, "T03": 41000}
    nxt = {"T01": HUGE, "T02": "large"}
    assert apply_tier_changes(tiers, funds, nxt, CS) == ["T01"]
    assert tiers["T01"] == HUGE and funds["T01"] == base_funds(HUGE, CS) and funds["T02"] == 52000 and nxt == {}


@pytest.mark.slow
def test_change_takes_effect_at_the_next_contract_stage():
    """年度の確定でオフの手続きが始まるとき、新しい規模の基準額に置き直してから順位で増減する(D-493)。"""
    g = api.Game(_start(my_team_index=0), dirty=False)
    cur = g.state.budget_tiers["T07"]
    new = "small" if cur != "small" else "large"
    g.set_team_tier("T07", new)
    g.set_team_tier("T01", HUGE)
    g.advance_to_end()
    g.year_end()
    state = g.state
    rank = state.procedure.ranks["T07"]
    assert state.budget_tiers["T07"] == new and state.budget_tiers["T01"] == HUGE and state.tier_next == {}
    assert state.team_funds["T07"] == changed_funds(base_funds(new, CS), new, rank, CS)
    assert g.team_tier("T07")["manual"] and g.team_tier("T07")["next"] is None
    g.set_team_tier("T07", cur)  # 手続きの途中で変えたら、その次のオフから
    assert state.budget_tiers["T07"] == new and state.tier_next == {"T07": cur}
    again = load_game(save_game(state))
    assert again.tier_next == {"T07": cur} and again.tier_start == state.tier_start and again.budget_tiers == state.budget_tiers


# ---- 保存(D-494) ----

def _as_v33(data: bytes) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    state = json.loads(files["state.json"])
    state.pop("tier_start", None)
    state.pop("tier_next", None)
    manifest = json.loads(files["manifest.json"])
    manifest["format_version"] = 33
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode()
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_saved_and_version_33_reads_as_unchanged():
    g = api.Game(_start(my_team_index=0, team_tiers=["small"] + ["auto"] * 11), dirty=False)
    g.set_team_tier("T02", HUGE)
    again = load_game(save_game(g.state))
    assert SAVE_FORMAT_VERSION == 34 and again.tier_next == {"T02": HUGE} and again.tier_start == g.state.tier_start
    old = load_game(_as_v33(save_game(g.state)))
    assert old.tier_next == {} and old.tier_start == old.budget_tiers == g.state.budget_tiers
