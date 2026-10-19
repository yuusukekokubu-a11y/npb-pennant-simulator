"""新規開始のテンプレート(用意したリーグ。D-462〜D-467)。

  - テンプレートは、事前運転と試運転を済ませた新規開始の直後(1 日目の前)の状態を、セーブデータと同じ形で
    `data/templates/standard.sav` に置いたもの。情報(版・シード・紹介)は `standard.json`(D-463)。
  - 始めるときは、名前・自球団・資金力・仕組みの選択肢を当てはめ、シーズンをシーズンのシードから作り直す(D-465、D-466)。
  - 作り直しは build_template(`scripts/make_template.py`)。選手の作り方・事前運転・契約・試合の計算・保存形式が
    変わったら作り直す(D-467)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .ability_model import overall
from .contracts import HUGE, initial_funds
from .abilities import BATTER, PITCHER, PITCHER_POSITIONS
from .newgame import TeamNameError, apply_place_names, resolve_team_names
from .savegame import SAVE_FORMAT_VERSION, GameState, load_game, save_game
from .season import Season, derive_seed
from .storage import read_package_bytes, read_package_text

TEMPLATE_ID = "standard"
TEMPLATE_TITLE = "用意したリーグ"
TEMPLATE_FORMAT = 1  # standard.json の形の版
LEAGUE_SEED = 20261019  # テンプレートのリーグのシード(固定)
SEASON_SEED = 1019  # テンプレートの中のシーズンのシード(始めるときは作り直す)
SAVED_AT = datetime(2026, 10, 19, tzinfo=timezone.utc)  # 保存日時を固定して、作り直しても同じ中身にする
STRENGTH_TOP = 25  # 紹介の戦力:上位の総合値の合計の人数(事前運転の「歴史」と同じ。D-406)
FILL_PREFIX = "T"  # 外国人・育成なしで埋める新人の ID の頭文字(ほかの経路と重ならない)


def _strength(team) -> float:
    return sum(sorted((overall(p) for p in team.players), reverse=True)[:STRENGTH_TOP])


def _payroll(team) -> int:
    return sum(int(p.contract["salary"]) for p in list(team.players) + list(team.development) if p.contract)


# ---- 作る ----

def build_template(progress=None, prerun_progress=None) -> tuple[bytes, dict]:
    """テンプレート(セーブデータの中身と情報)を作る。新規開始と同じ手順(Game.new)を、決まったシード・観戦のみ・
    初期名・既定の仕組みで行う。紹介の「前の年」は試運転のシーズンの結果(D-462)。"""
    from .api import Game
    from .fingerprint import FINGERPRINT_VERSION

    kept: list = []
    game = Game.new(LEAGUE_SEED, [None] * 12, None, season_seed=SEASON_SEED, baselines="trial", progress=progress, prerun_progress=prerun_progress, trial_keep=kept)
    state = game.state
    league = state.league
    trial = kept[0]
    champions = []
    for i in range(len(league.league_names)):
        row = trial.standings(i)[0]
        champions.append({"league_index": i, "team_id": row.team_id, "wins": row.wins, "losses": row.losses, "ties": row.ties})
    ranked = sorted(league.teams, key=lambda t: (-_strength(t), t.id))
    meta = {
        "template_format": TEMPLATE_FORMAT,
        "id": TEMPLATE_ID,
        "title": TEMPLATE_TITLE,
        "save_format_version": SAVE_FORMAT_VERSION,
        "fingerprint_version": FINGERPRINT_VERSION,
        "league_seed": LEAGUE_SEED,
        "season_seed": SEASON_SEED,
        "intro": {"champions": champions, "strongest": ranked[0].id, "weakest": ranked[-1].id},
    }
    return save_game(state, SAVED_AT), meta


def meta_bytes(meta: dict) -> bytes:
    return (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# ---- 読む ----

_cache: dict = {}


def template_files() -> tuple[dict, bytes]:
    """置いてあるテンプレートの (情報, セーブデータの中身)。1 回読んだら覚える。"""
    if "files" not in _cache:
        meta = json.loads(read_package_text("data", "templates", f"{TEMPLATE_ID}.json"))
        data = read_package_bytes("data", "templates", f"{TEMPLATE_ID}.sav")
        _cache["files"] = (meta, data)
    return _cache["files"]


def template_info() -> dict:
    """選ぶ画面に出す、テンプレートの名前と紹介(球団名はテンプレートの初期名)。"""
    from .generate import generate_league

    meta, _ = template_files()
    league = generate_league(int(meta["league_seed"]))  # 初期名・リーグ名はリーグのシードだけで決まる(事前運転で変わらない)
    names = {t.id: t.default_name for t in league.teams}
    intro = meta["intro"]
    lines = []
    for c in intro["champions"]:
        lines.append(f"前の年の{league.league_names[c['league_index']]}の優勝:{names[c['team_id']]}({c['wins']}勝{c['losses']}敗{c['ties']}分)")
    lines.append(f"戦力が最も高い球団:{names[intro['strongest']]}/最も低い球団:{names[intro['weakest']]}")
    return {"id": meta["id"], "title": meta["title"], "league_seed": int(meta["league_seed"]), "lines": lines}


# ---- 始める ----

def start_from_template(
    team_names,
    my_team_index: int | None,
    season_seed: int,
    rules: dict | None = None,
    league_names=None,
    stadium_names=None,
    my_tier: str | None = None,
) -> GameState:
    """テンプレートから新しいゲームの状態を作る(D-465、D-466)。名前に問題があれば TeamNameError、
    仕組みの選択肢・資金力が正しくなければ ValueError。シーズンは season_seed で作り直す。"""
    from .contracts import ALL_TIERS
    from .rules import rules_from

    game_rules = rules_from(rules)
    if my_tier not in (None, "", "auto") and my_tier not in ALL_TIERS:
        raise ValueError(f"自球団の資金力は auto / small / medium / large / huge です(値: {my_tier!r})")
    _, data = template_files()
    state = load_game(data)
    league = state.league
    for team, name in zip(league.teams, resolve_team_names(league, team_names)):
        team.display_name = name
    apply_place_names(league, league_names, stadium_names)
    if my_team_index is not None and (isinstance(my_team_index, bool) or not isinstance(my_team_index, int) or not 0 <= my_team_index < len(league.teams)):
        raise TeamNameError([f"自球団の選び方が正しくありません(値: {my_team_index!r})"])
    state.my_team_id = None if my_team_index is None else league.teams[my_team_index].id
    state.rules = game_rules
    changed = False
    if state.my_team_id is not None and my_tier not in (None, "", "auto"):
        changed |= set_tier(state, state.my_team_id, my_tier)
    if not game_rules.foreign_development:
        changed |= remove_foreign(state)
    if changed:
        refit_contracts(state)
    old = state.season
    from .injuries import default_settings as injury_settings

    state.season = Season(league, int(season_seed), season_config=old.season_config, game_config=old.game_config, model=old.model, farm=True, injuries=injury_settings() if game_rules.injuries else None)
    return state


def set_tier(state: GameState, team_id: str, tier: str) -> bool:
    """自球団の資金力を tier にする(D-466)。その規模の球団のうち総年俸が最も少ない球団と規模を入れ替え、大 3・中 6・小 3 を保つ
    (超大量は大として数えるので、今が大なら入れ替えない)。資金は規模の基準額、設備は資金から AI と同じに決める。変えたら True。"""
    from .facilities import default_settings

    tiers = dict(state.budget_tiers)
    cur = tiers[team_id]
    if tier == cur:
        return False
    base = lambda t: "large" if t == HUGE else t
    changed = [team_id]
    if base(tier) != base(cur):
        teams = [t for t in state.league.teams if t.id != team_id and base(tiers[t.id]) == base(tier)]
        partner = min(teams, key=lambda t: (_payroll(t), t.id))
        tiers[partner.id] = base(cur)
        changed.append(partner.id)
    tiers[team_id] = tier
    state.budget_tiers = tiers
    funds = initial_funds(tiers, state.contract_settings)
    fs = state.facility_settings or default_settings()
    for t in state.league.teams:
        if t.id in changed:
            state.team_funds[t.id] = funds[t.id]
            level = fs.ai_level(funds[t.id])
            t.facilities = {k: level for k in fs.kinds}
    return True


def remove_foreign(state: GameState) -> bool:
    """外国人・育成なし(D-466):外国人選手と育成選手を取り除き、外国人がいたポジションに新人を入れる
    (支配下が取り除く前の人数と AI の目安の少ない方に届くまで。最低人数も守る)。球団は戦力の低い順に、能力の高い新人から 1 人ずつ。"""
    from .contracts import set_contract
    from .procedure import make_candidates, minimum_batters, minimum_positions, shortages

    league = state.league
    need: dict[str, list[str]] = {}
    removed_any = False
    for team in league.teams:
        gone = sorted((p for p in team.players if p.foreign), key=lambda p: (-overall(p), p.id))
        removed_any |= bool(gone) or bool(team.development)
        target = min(len(team.players), state.draft_settings.ai_draft_target)
        team.players = [p for p in team.players if not p.foreign]
        team.development = []
        need[team.id] = [p.position for p in gone][: max(0, target - len(team.players))]
    if not removed_any:
        return False
    game_config = state.season.game_config
    mins, min_batters = minimum_positions(game_config), minimum_batters(game_config)
    pool = make_candidates(league, derive_seed(league.seed, "template-fill"), state.gen_config, state.name_parts, state.draft_settings, 0, state.calibration, id_prefix=FILL_PREFIX)
    order = sorted(league.teams, key=lambda t: (_strength(t), t.id))

    def take(team, position: str | None, role: str | None) -> None:
        cands = [p for p in pool if (p.position == position if position else p.role == role)] or [p for p in pool if role and p.role == role] or pool
        p = max(cands, key=lambda c: (overall(c), c.id))
        pool.remove(p)
        p.team_id = team.id
        p.state.fatigue = 0.0
        p.fa_seasons = 0
        team.players.append(p)
        set_contract(p, state.contract_settings.minimum, 1, 1, "template")  # 年俸は refit_contracts で求め直す

    for r in range(max((len(v) for v in need.values()), default=0)):
        for team in order:
            if r < len(need[team.id]):
                pos = need[team.id][r]
                take(team, pos, PITCHER if pos in PITCHER_POSITIONS else BATTER)
    for team in order:  # 最低人数
        for pos, n in shortages(team.players, mins, min_batters).items():
            for _ in range(n):
                take(team, None if pos == "batter" else pos, BATTER if pos == "batter" else (PITCHER if pos in PITCHER_POSITIONS else BATTER))
    from .policy import refresh_ai

    refresh_ai(league)  # 方針は顔ぶれに合わせて AI が決め直す(新規開始と同じ。D-454)
    return True


def refit_contracts(state: GameState) -> None:
    """資金力・顔ぶれを変えた後に、初期の契約の年俸を新規開始と同じ方法で求め直す(年数は変えない。D-466)。"""
    from .contract_stage import ContractContext, fit_initial_salaries
    from .facilities import league_upkeep
    from .negotiation import depth_slots

    league = state.league
    sd = state.draft_settings.level_sd(state.scout_level)
    ctx = ContractContext(state.contract_settings, dict(state.team_funds), dict(state.budget_tiers), lambda pid, role: [], lambda tid: sd, negotiation=state.negotiation_settings, league_seed=league.seed, slots=depth_slots(state.gen_config), upkeep=league_upkeep(league))
    rate, salaries = fit_initial_salaries(league, ctx)
    for team in league.teams:
        for p in team.players:
            p.contract["salary"] = salaries[p.id]
            if p.contract.get("history"):
                p.contract["history"][-1]["salary"] = salaries[p.id]
    state.contract_rates["1"] = float(rate)
