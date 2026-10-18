"""特殊能力(D-374):設定の点検・付き方・効き方(いつも・得点圏・相手が左)・毎年の変化・見せ方・保存。"""

import copy
import io
import json
import random
import zipfile

import pytest

from pennant import ability_model, api
from pennant.config import ConfigError
from pennant.abilities import FIELDER_POSITIONS
from pennant.plate_appearance import BaseOutState, OddsRatioModel
from pennant.savegame import SaveDataError, load_game, save_game
from pennant.skills import (
    always_effects,
    default_settings,
    initial_skills,
    public_skills,
    situational,
    validate_skill_settings,
    yearly_change,
)

S = default_settings()


@pytest.fixture(scope="module")
def game():
    return api.Game.new(2, [None] * 12, 0, season_seed=7, baselines="default")


def _zip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _rezip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def players(g):
    return list(g.state.league.all_players())


def test_settings_have_good_and_bad_pairs_and_validation():
    for role in ("batter", "pitcher"):
        pairs = {}
        for key, s in S.skills.items():
            if s["role"] == role:
                pairs.setdefault(s["pair"], []).append(s["good"])
        both = {k: v for k, v in pairs.items() if k not in ("power_hitter", "average_hitter")}  # 打者の型は良いものだけ
        assert len(both) == (6 if role == "batter" else 5)  # 打者は 盗塁・キャッチャーを足して 5 対(D-428)、ケガしにくさで 6 対・投手は 5 対(D-445) and all(sorted(v) == [False, True] for v in both.values())
    data = copy.deepcopy(S.data)
    data["skills"]["chance_plus"]["when"] = "night"
    with pytest.raises(ConfigError, match="chance_plus.when"):
        validate_skill_settings(data)
    data = copy.deepcopy(S.data)
    data["skills"]["chance_plus"]["effects"] = {"velocity_kmh": 5}
    with pytest.raises(ConfigError, match="chance_plus.effects.velocity_kmh"):
        validate_skill_settings(data)
    data = copy.deepcopy(S.data)
    data["yearly"]["gain"] = 2
    with pytest.raises(ConfigError, match="yearly.gain"):
        validate_skill_settings(data)


def test_initial_skills_are_one_per_pair_and_match_the_role(game):
    ps = players(game)
    with_skills = [p for p in ps if p.skills]
    assert 0.3 < len(with_skills) / len(ps) < 0.7
    for p in ps:
        pairs = [S.skills[k]["pair"] for k in p.skills]
        assert len(pairs) == len(set(pairs)) and all(S.skills[k]["role"] == p.role for k in p.skills)
    # 条件(パワーヒッター・アベレージヒッター)は付くときだけ見る。パワー・ミートの低い選手には付かない
    weak = copy.deepcopy(next(p for p in ps if p.role == "batter"))
    for i in range(200):
        weak.id, weak.abilities["power"], weak.abilities["meet"] = f"X{i}", 40.0, 40.0
        assert not {"power_hitter", "average_hitter"} & set(initial_skills(weak))
    # 同じ選手からは同じ特殊能力(別の乱数の系列)
    assert all(initial_skills(p) == p.skills for p in ps[:50] if p.origin is None)


def test_always_effects_are_in_the_engine_ratings(game):
    cfg = game.state.gen_config
    p = copy.deepcopy(next(p for p in players(game) if p.role == "batter"))
    p.skills = []
    base = ability_model.engine_ratings(cfg, p)
    p.skills = ["running_plus", "power_hitter"]
    assert always_effects(p) == {"baserunning": 15.0, "power": 5.0}
    r = ability_model.engine_ratings(cfg, p)
    assert r["baserunning"] == pytest.approx(base["baserunning"] + 15) and r["power"] == pytest.approx(base["power"] + 5)
    assert r["contact"] == pytest.approx(base["contact"])
    p.skills = ["chance_plus"]  # 条件つきは内部の能力に入れない(打席ごとに足す)
    assert ability_model.engine_ratings(cfg, p) == base
    # 見せる能力の数値は変えない
    assert ability_model.public_abilities(cfg, p) == ability_model.public_abilities(cfg, copy.deepcopy(p))


def test_situational_effects_change_plate_appearance_odds(game):
    p = copy.deepcopy(next(p for p in players(game) if p.role == "batter"))
    pitcher = copy.deepcopy(next(x for x in players(game) if x.role == "pitcher"))
    p.skills, pitcher.skills = ["chance_plus", "vs_left_minus"], []
    assert situational(p, True, False) == {"contact": 8.0, "power": 8.0}
    assert situational(p, False, True) == {"contact": -8.0, "eye": -8.0, "power": -8.0}
    assert situational(p, False, False) == {}
    # 得点圏では打席の結果の出方が変わる(同じ乱数で、多数の打席の平均)
    model = OddsRatioModel()
    pitcher.throws = "R"

    fielders = [x for x in players(game) if x.role == "batter" and x.id != p.id]
    defense = {pos: fielders[i] for i, pos in enumerate(FIELDER_POSITIONS)}

    def hits(batter, base):
        rng = random.Random(3)
        return sum(model.resolve(batter, pitcher, defense, base, rng).result in ("single", "double", "triple", "home_run") for _ in range(4000))

    plain = copy.deepcopy(p)
    plain.skills = []
    risp = BaseOutState(outs=0, second=True)
    assert hits(p, risp) > hits(plain, risp)
    assert hits(p, BaseOutState()) == hits(plain, BaseOutState())  # 得点圏でなければ同じ
    assert p.ratings == plain.ratings  # 元の選手は変えない


def test_yearly_change_keeps_pairs_and_is_reproducible(game):
    ps = [copy.deepcopy(p) for p in players(game)]
    total_gain = total_lose = 0
    for seed in range(5):
        for p in ps:
            before = list(p.skills)
            q = copy.deepcopy(p)
            ch = yearly_change(p, seed)
            assert yearly_change(q, seed) == ch and q.skills == p.skills  # 同じシードなら同じ
            total_gain += len(ch["gained"])
            total_lose += len(ch["lost"])
            assert set(p.skills) == (set(before) - set(ch["lost"])) | set(ch["gained"])
            pairs = [S.skills[k]["pair"] for k in p.skills]
            assert len(pairs) == len(set(pairs))
    n = len(ps) * 5
    assert 0.02 < total_gain / n < 0.06 and 0.005 < total_lose / n < 0.06


@pytest.mark.slow
def test_offseason_records_skill_changes(game):
    g = api.Game(copy.deepcopy(game.state), dirty=False)
    g.advance_to_end()
    g.year_end()
    g.offseason_auto()
    r = g.state.offseasons[-1]
    assert r.skill_changes
    by_id = {p.id: p for p in g.state.league.all_players()}
    for pid, ch in r.skill_changes.items():
        p = by_id.get(pid)
        if p is not None and p.origin != "draft":
            assert set(ch["gained"]) <= set(p.skills) or ch["lost"]


def test_skills_are_shown_for_pros_only_and_saved(game):
    g = api.Game(copy.deepcopy(game.state), dirty=False)
    p = next(p for p in players(g) if p.skills)
    v = g.player(p.id)["player"]
    assert v["skills"] == public_skills(p) and all(set(s) == {"key", "label", "good"} for s in v["skills"])
    table = g.ability_table("batter", qualified=False, team_id=p.team_id) if p.role == "batter" else None
    if table is not None and table["rows"]:
        assert table["columns"][-1]["key"] == "skills"
    lineup = g.lineup_view()
    assert all("skills" in s for s in lineup["slots"] + lineup["bench"])
    # 保存と読み込みで残る。知らない特殊能力は理由つきで断る
    again = load_game(save_game(g.state))
    assert [x.skills for x in again.league.all_players()] == [x.skills for x in players(g)]
    files = _zip(save_game(g.state))
    state = json.loads(files["state.json"])
    state["league"]["teams"][0]["players"][0]["skills"] = ["flying"]
    files["state.json"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    with pytest.raises(SaveDataError, match="知らない特殊能力"):
        load_game(_rezip(files))


def test_draft_candidates_skills_are_hidden():
    g = api.Game.new(1, [None] * 12, 0, season_seed=4, baselines="default")
    g.advance_to_end()
    g.year_end()
    while g.state.procedure is not None and g.offseason_view()["stage"] != "draft":
        g.offseason_stage_auto()
    assert g.state.procedure is not None
    cands = g.offseason_players("draft")
    assert any(c.skills for c in cands)  # 内部では持っている
    text = json.dumps([g.offseason_view(), g.offseason_table("draft"), g.offseason_table("draft", role="pitcher")], ensure_ascii=False)
    assert '"skills"' not in text and not any(S.label(k) in text for k in S.skills)
