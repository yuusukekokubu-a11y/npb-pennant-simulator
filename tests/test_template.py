"""新規開始のテンプレート(用意したリーグ。D-462〜D-467)。"""

import json
import zipfile
import io

import pytest

from pennant import api, template as T
from pennant.contracts import HUGE
from pennant.facilities import default_settings as facility_settings, league_upkeep
from pennant.fingerprint import FINGERPRINT_VERSION
from pennant.newgame import TeamNameError
from pennant.procedure import minimum_batters, minimum_positions, shortages
from pennant.rules import DEFAULT_RULES
from pennant.savegame import SAVE_FORMAT_VERSION, build_state, load_game


def _template_state():
    _, data = T.template_files()
    return load_game(data)


def _start(**kw):
    args = {"team_names": [None] * 12, "my_team_index": None, "season_seed": 5}
    args.update(kw)
    return T.start_from_template(**args)


def _tier_counts(tiers: dict) -> dict:
    out: dict = {}
    for t in tiers.values():
        key = "large" if t == HUGE else t
        out[key] = out.get(key, 0) + 1
    return out


def test_template_matches_current_versions():
    """保存形式の版か指紋の版を上げたら、テンプレートを作り直す(scripts/make_template.py。D-467)。"""
    meta, _ = T.template_files()
    assert meta["template_format"] == T.TEMPLATE_FORMAT
    assert meta["save_format_version"] == SAVE_FORMAT_VERSION, "保存形式の版が変わりました。python scripts/make_template.py でテンプレートを作り直してください"
    assert meta["fingerprint_version"] == FINGERPRINT_VERSION, "指紋の版が変わりました(選手や試合の作り方が変わった)。python scripts/make_template.py でテンプレートを作り直してください"
    assert meta["league_seed"] == T.LEAGUE_SEED and meta["season_seed"] == T.SEASON_SEED and meta["id"] == T.TEMPLATE_ID


def test_template_is_a_fresh_league_without_history():
    """中身は新規開始の直後と同じ(歴史は入れない。名前は初期名。観戦のみ。D-462)。"""
    state = _template_state()
    assert state.year == 1 and state.season.day == 0 and not state.season.played
    assert state.history == [] and state.offseasons == [] and state.transactions == [] and state.park_history == []
    assert state.my_team_id is None and state.league.seed == T.LEAGUE_SEED
    assert all(t.display_name is None for t in state.league.teams)
    assert state.baselines.source == "trial"  # 試運転で求めた基準値
    assert state.rules.to_dict() == DEFAULT_RULES.to_dict()
    teams = state.league.teams
    assert all(any(p.foreign for p in t.players) for t in teams) and all(t.development for t in teams)  # 外国人と育成を含む
    assert all(p.contract and p.fa_seasons is not None for t in teams for p in t.players)


def test_template_info_lines():
    info = api.template_info()
    assert info["league_seed"] == T.LEAGUE_SEED and info["title"]
    assert len(info["lines"]) == 3 and info["lines"][0].startswith("前の年の") and "戦力が最も高い球団" in info["lines"][2]
    names = {t["default_name"] for lg in api.preview_teams(info["league_seed"])["leagues"] for t in lg["teams"]}
    meta, _ = T.template_files()
    state = _template_state()
    by_id = {t.id: t.default_name for t in state.league.teams}
    assert by_id[meta["intro"]["strongest"]] in info["lines"][2] and by_id[meta["intro"]["weakest"]] in info["lines"][2]
    assert set(by_id.values()) == names  # 名前の一覧と点検はリーグのシードの preview と同じ


def test_unchanged_start_is_the_template():
    """何も変えずにテンプレートのシーズンのシードで始めると、テンプレートの状態そのもの(シーズンの作り直しが正しい)。"""
    state = _start(season_seed=T.SEASON_SEED)
    assert build_state(state) == build_state(_template_state())


def test_season_seed_changes_the_season_only():
    a, b, c = _start(season_seed=5), _start(season_seed=6), _start(season_seed=5)
    assert build_state(a) == build_state(c)
    assert a.season.seed == 5 and b.season.seed == 6
    assert [g.home_id for g in a.season.schedule.days[0]] != [g.home_id for g in b.season.schedule.days[0]] or a.season.conditions.form("P0001", 0) != b.season.conditions.form("P0001", 0)
    pa = {p.id: (p.age, p.contract["salary"]) for t in a.league.teams for p in t.players}
    pb = {p.id: (p.age, p.contract["salary"]) for t in b.league.teams for p in t.players}
    assert pa == pb  # 選手と契約はテンプレートのまま


def test_names_places_and_my_team():
    names = [None] * 12
    names[2] = "テスト球団"
    state = _start(team_names=names, my_team_index=2, league_names=["テスト連盟", None], stadium_names=["テスト球場"] + [None] * 11)
    assert state.league.teams[2].name == "テスト球団" and state.my_team_id == state.league.teams[2].id
    assert state.league.league_names[0] == "テスト連盟" and state.league.teams[0].stadium == "テスト球場"
    with pytest.raises(TeamNameError):
        _start(team_names=["同じ", "同じ"] + [None] * 10)
    with pytest.raises(TeamNameError):
        _start(my_team_index=12)
    with pytest.raises(ValueError):
        _start(my_tier="giant")
    with pytest.raises(ValueError):
        _start(rules={"unknown": True})


def test_refit_without_changes_keeps_salaries():
    """初期の年俸の求め直しは、新規開始と同じ方法(変えなければテンプレートの年俸と同じ。D-466)。"""
    state = _start()
    before = {p.id: p.contract["salary"] for t in state.league.teams for p in t.players}
    rate = state.contract_rates["1"]
    T.refit_contracts(state)
    assert {p.id: p.contract["salary"] for t in state.league.teams for p in t.players} == before
    assert state.contract_rates["1"] == rate


@pytest.mark.parametrize("tier", ["small", "medium", "large", "huge"])
@pytest.mark.parametrize("index", [0, 6, 9])
def test_my_tier_swaps_and_keeps_funds_consistent(tier, index):
    """資金力を選ぶと、その規模の球団と入れ替えて 大 3・中 6・小 3 を保ち、資金・設備・年俸の整合を保つ(D-466)。"""
    base = _template_state()
    state = _start(my_team_index=index, my_tier=tier)
    my = state.my_team_id
    assert state.budget_tiers[my] == tier
    assert _tier_counts(state.budget_tiers) == {"large": 3, "medium": 6, "small": 3}
    changed = {tid for tid in state.budget_tiers if state.budget_tiers[tid] != base.budget_tiers[tid]}
    cur = base.budget_tiers[my]
    if tier == cur:
        assert not changed
    elif {tier, cur} == {"huge", "large"}:
        assert changed == {my}
    else:
        partner = (changed - {my}).pop()
        assert base.budget_tiers[partner] == ("large" if tier == HUGE else tier)
        payroll = lambda t: T._payroll(t)
        same = [t for t in base.league.teams if t.id != my and base.budget_tiers[t.id] == base.budget_tiers[partner]]
        assert partner == min(same, key=lambda t: (payroll(t), t.id)).id  # 総年俸が最も少ない球団
    fs = facility_settings()
    up = league_upkeep(state.league)
    from pennant.contracts import base_funds

    for t in state.league.teams:
        assert state.team_funds[t.id] == base_funds(state.budget_tiers[t.id], state.contract_settings)
        assert t.facilities == {k: fs.ai_level(state.team_funds[t.id]) for k in fs.kinds}
        assert T._payroll(t) <= state.team_funds[t.id] - up[t.id]  # 資金を超える球団はない


def test_my_tier_is_ignored_when_watching():
    assert build_state(_start(my_tier="huge")) == build_state(_start())


def test_no_foreign_development_removes_and_fills():
    """外国人・育成なし:外国人と育成を取り除き、取り除く前の人数と 65 人の少ない方まで新人で埋める(D-466)。"""
    base = _template_state()
    state = _start(rules={"foreign_development": False}, my_team_index=0)
    assert not state.rules.foreign_development
    mins, mb = minimum_positions(state.season.game_config), minimum_batters(state.season.game_config)
    target = state.draft_settings.ai_draft_target
    ids = [p.id for t in state.league.teams for p in t.players]
    assert len(ids) == len(set(ids))
    up = league_upkeep(state.league)
    for t, b in zip(state.league.teams, base.league.teams):
        assert not t.development and not any(p.foreign for p in t.players)
        kept = len([p for p in b.players if not p.foreign])
        new = [p for p in t.players if p.id.startswith(T.FILL_PREFIX)]
        assert len(t.players) == max(kept, min(len(b.players), target)) and not shortages(t.players, mins, mb)
        assert all(p.contract and p.fa_seasons == 0 and p.team_id == t.id for p in new)
        assert T._payroll(t) <= state.team_funds[t.id] - up[t.id]
    assert sum(1 for t in state.league.teams for p in t.players if p.id.startswith(T.FILL_PREFIX)) > 0
    # 外国人の候補・育成ドラフトはない仕組みとして続く(シーズンを作れる)
    assert state.season.actives and all(len(a.players) == 29 for a in state.season.actives.values())


def test_template_game_saves_loads_and_continues_the_same():
    """テンプレートで始めたゲームも、保存して読み込んだ続きが同じ(D-465)。"""
    names = [None] * 12
    names[4] = "テスト球団"
    g = api.Game.from_template(names, 4, 11, rules={"auto_fa": True}, my_tier="small")
    assert g.dirty
    g.advance(3)
    data = g.save()["data"]
    g2 = api.Game.load(data)
    g.advance(4)
    g2.advance(4)
    assert build_state(g.state) == build_state(g2.state)
    assert g2.state.rules.auto_fa and g2.state.league.teams[4].name == "テスト球団"


def test_template_files_are_packaged():
    """テンプレートはパッケージとブラウザの pennant.zip に入る(D-463)。"""
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("build_web", root / "scripts" / "build_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_web_test"] = mod
    spec.loader.exec_module(mod)
    names = {f.relative_to(mod.PACKAGE).as_posix() for f in mod.package_files()}
    assert {"data/templates/standard.sav", "data/templates/standard.json"} <= names
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "data/templates/*.sav" in text and "data/templates/*.json" in text
    _, data = T.template_files()
    assert zipfile.ZipFile(io.BytesIO(data)).namelist()[0] == "manifest.json"


@pytest.mark.slow
def test_template_is_up_to_date():
    """今のコードで作り直したテンプレートが、置いてあるものと同じ(D-467)。違えば python scripts/make_template.py。"""
    data, meta = T.build_template()
    stored_meta, stored = T.template_files()
    read = lambda d: json.loads(zipfile.ZipFile(io.BytesIO(d)).read("state.json"))
    assert meta == stored_meta, "テンプレートの情報が今のコードと違います。python scripts/make_template.py で作り直してください"
    assert read(data) == read(stored), "テンプレートの中身が今のコードと違います。python scripts/make_template.py で作り直してください"


@pytest.mark.slow
def test_template_game_through_year_end():
    """テンプレートで始めたゲームで 1 シーズンを終え、年度の確定とオフの手続き(おまかせ)を経て 2 シーズン目に入れる。"""
    g = api.Game.from_template([None] * 12, 1, 3, my_tier="large")
    g.advance_to_end()
    g.year_end()
    for _ in range(40):
        if g.state.procedure is None:
            break
        g.offseason_auto()
    assert g.state.procedure is None and g.state.year == 2 and len(g.state.history) == 1
    g.advance(1)
    assert g.state.season.day >= 1
