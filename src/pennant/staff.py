"""球団職員(D-468〜D-480):監督 1・コーチ 3(打撃・投手・守備走塁)・スカウト 1。

- 職員は辞書 {id, family_name, given_name, age, role, skill, trait, salary, years, until, player_id, origin} で持つ。
  球団の職員は Team.staff(役割 → 職員)、職員の市場は League.staff_free、担当の記録は League.staff_log。
  職員がいない球団(試験用・事前運転)は、すべて標準の効きで年俸 0。
- 腕(skill)は 30〜99 の整数。画面には S〜E のランクだけを出す(D-473)。腕 60 が標準。
- 効き:コーチは年度の確定の伸びと衰え(coach_factors。D-471)、スカウトは見立てのずれの倍率(scout_factor。D-472)、
  監督は采配の腕の段階(manager_level。継投に効く。D-470)と方針の好み(manager_pref。AI のチーム方針に効く)。
- 年俸は資金から出す(設備の維持費と同じ扱い。team_cost・league_costs。D-474)。
- オフの手続きの「職員」の段階(D-474、D-475):start_stage(契約切れ・AI の監督の解任・新しい候補)→ 希望 → close_stage(前年の順位が低い球団から 1 人ずつ)。
- どの球団の職員も、プレイヤーが入れ替えられる(swap。他球団は「あなたが決めた」になる。D-476)。
- 値は data/staff.json(仮置き。DESIGN 9 章)。乱数は別の系列(derive_seed(…, "staff…"))で、ほかの乱数の出方を変えない。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, NameParts, _Checker, _read_json
from .models import League, Team
from .names import NameGenerator
from .season import derive_seed

SUPPORTED_FORMAT_VERSION = 1
ROLES = ("manager", "batting", "pitching", "fielding", "scout")
COACHES = ("batting", "pitching", "fielding")
TRAITS = ("none", "youth", "win", "bunt", "steal", "power")
RANKS = ("S", "A", "B", "C", "D", "E")
# 監督の好み → チーム方針の項目と値(D-470)
TRAIT_POLICY = {"youth": ("usage", "youth"), "win": ("usage", "win"), "bunt": ("bunt", "many"), "steal": ("steal", "aggressive"), "power": ("bunt", "few")}


@dataclass(frozen=True)
class StaffSettings:
    data: dict
    source: str

    def label(self, role: str) -> str:
        return str(self.data["labels"][role])

    def rank(self, skill: int) -> str:
        for r in RANKS:
            if int(skill) >= int(self.data["skill"]["ranks"][r]):
                return r
        return RANKS[-1]

    def salary(self, role: str, skill: int) -> int:
        s = self.data["salary"]
        base = int(s["ranks"][self.rank(skill)])
        return base * int(s["manager_factor"]) if role == "manager" else base

    @property
    def neutral(self) -> int:
        return int(self.data["skill"]["neutral"])

    def clip(self, v: float) -> int:
        sk = self.data["skill"]
        return int(max(int(sk["min"]), min(int(sk["max"]), round(v))))

    def trait_label(self, trait: str) -> str:
        return str(self.data["manager"]["trait_labels"].get(trait, ""))


def load_staff_settings(path: str | Path | None = None) -> StaffSettings:
    data, source = _read_json(path, "staff.json")
    return validate_staff_settings(data, source)


def validate_staff_settings(data, source: str = "(辞書)") -> StaffSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    roles = c.get(root, "roles", "")
    if roles is not None and list(roles) != list(ROLES):
        c.add("roles", f"役割は {', '.join(ROLES)} の順にしてください")
    labels = c.section(c.get(root, "labels", ""), "labels") or {}
    for r in ROLES:
        if not isinstance(labels.get(r), str) or not labels.get(r):
            c.add(f"labels.{r}", "名前(文字)が必要です")
    sk = c.section(c.get(root, "skill", ""), "skill")
    lo = c.integer(c.get(sk, "min", "skill"), "skill.min", 1, 100)
    hi = c.integer(c.get(sk, "max", "skill"), "skill.max", 1, 100)
    if lo is not None and hi is not None and lo > hi:
        c.add("skill.min", "max 以下にしてください")
    c.integer(c.get(sk, "neutral", "skill"), "skill.neutral", 1, 100)
    ranks = c.section(c.get(sk, "ranks", "skill"), "skill.ranks") or {}
    vals = [c.integer(c.get(ranks, r, "skill.ranks"), f"skill.ranks.{r}", 0, 100) for r in RANKS]
    if all(v is not None for v in vals) and vals != sorted(vals, reverse=True):
        c.add("skill.ranks", "S から E へ、下がっていく順にしてください")
    co = c.section(c.get(root, "coach", ""), "coach")
    items = c.section(c.get(co, "items", "coach"), "coach.items") or {}
    from .abilities import BATTER_ABILITIES, PITCHER_ABILITIES

    for r in COACHES:
        v = items.get(r)
        if not isinstance(v, list) or not v or any(x not in BATTER_ABILITIES + PITCHER_ABILITIES for x in v):
            c.add(f"coach.items.{r}", "見せる能力の名前を 1 つ以上並べてください")
    c.number(c.get(co, "growth_per_point", "coach"), "coach.growth_per_point", 0, 0.05)
    c.number(c.get(co, "decline_per_point", "coach"), "coach.decline_per_point", 0, 0.05)
    sc = c.section(c.get(root, "scout", ""), "scout")
    c.number(c.get(sc, "per_point", "scout"), "scout.per_point", 0, 0.05)
    c.number(c.get(sc, "min", "scout"), "scout.min", 0.1, 1)
    c.number(c.get(sc, "max", "scout"), "scout.max", 1, 5)
    mg = c.section(c.get(root, "manager", ""), "manager")
    c.number(c.get(mg, "level_step", "manager"), "manager.level_step", 1, 50)
    c.integer(c.get(mg, "max_level", "manager"), "manager.max_level", 0, 5)
    c.integer(c.get(mg, "pull_from_inning", "manager"), "manager.pull_from_inning", 1, 20)
    c.integer(c.get(mg, "pull_close_margin", "manager"), "manager.pull_close_margin", 0, 20)
    c.number(c.get(mg, "pull_share_per_level", "manager"), "manager.pull_share_per_level", 0, 0.5)
    c.number(c.get(mg, "stay_share_per_level", "manager"), "manager.stay_share_per_level", 0, 0.5)
    tr = c.section(c.get(mg, "traits", "manager"), "manager.traits") or {}
    if sorted(tr) != sorted(TRAITS) or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in tr.values()) or not sum(tr.values()):
        c.add("manager.traits", f"好みの重み({', '.join(TRAITS)})を 0 以上の整数で(合計は 1 以上)")
    tl = c.section(c.get(mg, "trait_labels", "manager"), "manager.trait_labels") or {}
    if any(not isinstance(tl.get(k), str) for k in TRAITS):
        c.add("manager.trait_labels", "好みごとの名前(文字)が必要です")
    sa = c.section(c.get(root, "salary", ""), "salary")
    sr = c.section(c.get(sa, "ranks", "salary"), "salary.ranks") or {}
    for r in RANKS:
        c.integer(c.get(sr, r, "salary.ranks"), f"salary.ranks.{r}", 0, 10_000_000)
    c.integer(c.get(sa, "manager_factor", "salary"), "salary.manager_factor", 1, 10)
    ye = c.section(c.get(root, "years", ""), "years")
    y1 = c.integer(c.get(ye, "min", "years"), "years.min", 1, 10)
    y2 = c.integer(c.get(ye, "max", "years"), "years.max", 1, 10)
    if y1 is not None and y2 is not None and y1 > y2:
        c.add("years.min", "max 以下にしてください")
    ag = c.section(c.get(root, "age", ""), "age")
    for k in ("min", "max", "retire"):
        c.integer(c.get(ag, k, "age"), f"age.{k}", 20, 100)
    ge = c.section(c.get(root, "generate", ""), "generate")
    for k in ("initial_mean", "market_mean", "former_mean"):
        c.number(c.get(ge, k, "generate"), f"generate.{k}", 1, 100)
    c.number(c.get(ge, "sd", "generate"), "generate.sd", 0, 50)
    c.integer(c.get(ge, "per_role", "generate"), "generate.per_role", 0, 50)
    c.integer(c.get(ge, "former_max", "generate"), "generate.former_max", 0, 50)
    c.number(c.get(ge, "former_min_war", "generate"), "generate.former_min_war", -100, 1000)
    c.number(c.get(ge, "former_war_bonus", "generate"), "generate.former_war_bonus", 0, 5)
    c.number(c.get(ge, "former_bonus_max", "generate"), "generate.former_bonus_max", 0, 50)
    c.integer(c.get(ge, "pool_max", "generate"), "generate.pool_max", 0, 500)
    ai = c.section(c.get(root, "ai", ""), "ai")
    c.integer(c.get(ai, "wishes", "ai"), "ai.wishes", 0, 20)
    fire = c.section(c.get(ai, "fire", "ai"), "ai.fire") or {}
    for k, v in fire.items():
        if not str(k).isdigit():
            c.add(f"ai.fire.{k}", "順位(数字)ごとに書いてください")
        c.number(v, f"ai.fire.{k}", 0, 1)
    if c.problems:
        raise ConfigError(source, c.problems)
    return StaffSettings(copy.deepcopy(root), source)


_DEFAULT: list[StaffSettings] = []


def default_settings() -> StaffSettings:
    if not _DEFAULT:
        _DEFAULT.append(load_staff_settings())
    return _DEFAULT[0]


# ---- 職員を作る ----

def _pick_trait(rng: random.Random, s: StaffSettings) -> str:
    weights = s.data["manager"]["traits"]
    total = sum(int(weights[k]) for k in TRAITS)
    x = rng.random() * total
    for k in TRAITS:
        x -= int(weights[k])
        if x < 0:
            return k
    return TRAITS[-1]


def make_member(rng: random.Random, names: NameGenerator, role: str, staff_id: str, mean: float, s: StaffSettings, age: int | None = None, origin: str = "staff", player_id: str | None = None, name: tuple[str, str] | None = None, bonus: float = 0.0, foreign: bool = False) -> dict:
    """職員を 1 人作る(契約はまだない:until は None)。乱数の順:腕 → 年数 → 年齢 → 好み(監督)→ 名前。"""
    g = s.data["generate"]
    skill = s.clip(rng.gauss(float(mean), float(g["sd"])) + bonus)
    years = rng.randint(int(s.data["years"]["min"]), int(s.data["years"]["max"]))
    a = s.data["age"]
    born = rng.randint(int(a["min"]), int(a["max"]))
    trait = _pick_trait(rng, s) if role == "manager" else "none"
    family, given = name if name is not None else names.person()
    return {
        "id": staff_id, "family_name": family, "given_name": given, "age": int(age if age is not None else born), "role": role,
        "skill": skill, "trait": trait, "salary": s.salary(role, skill), "years": years, "until": None, "player_id": player_id, "origin": origin, "foreign": bool(foreign),
    }


def _used_names(league: League) -> set[tuple[str, str]]:
    used = {(p.family_name, p.given_name) for p in league.all_players() + league.development_players()}
    for m in all_staff(league):
        used.add((m["family_name"], m["given_name"]))
    return used


def all_staff(league: League) -> list[dict]:
    """球団の職員と市場の職員(球団の順・役割の順・市場の順)。"""
    out = [t.staff[r] for t in league.teams for r in ROLES if r in t.staff]
    return out + list(league.staff_free)


def name_of(m: dict) -> str:
    """画面の名前(元の外国人選手は姓だけ。D-376)。"""
    return m["family_name"] if m.get("foreign") else f"{m['family_name']} {m['given_name']}"


def assign_initial(league: League, parts: NameParts, seed: int, start_season: int = 1, settings: StaffSettings | None = None) -> None:
    """新規開始(と版 32 の読み込み)の職員(D-478、D-479):全球団の 5 人と、最初の市場の候補(架空)。
    乱数は derive_seed(seed, "staff")(リーグのシード)。契約は start_season から各自の年数(1〜3 年)。"""
    s = settings or default_settings()
    g = s.data["generate"]
    rng = random.Random(derive_seed(seed, "staff"))
    names = NameGenerator(parts, rng, _used_names(league))
    n = 0
    for team in league.teams:
        team.staff = {}
        team.staff_manual = False
        team.staff_dead = []
        for role in ROLES:
            n += 1
            m = make_member(rng, names, role, f"S00N{n:03d}", float(g["initial_mean"]), s)
            m["until"] = int(start_season) + int(m["years"]) - 1
            team.staff[role] = m
    league.staff_free = []
    for role in ROLES:
        for _ in range(int(g["per_role"])):
            n += 1
            league.staff_free.append(make_member(rng, names, role, f"S00N{n:03d}", float(g["market_mean"]), s))
    league.staff_log = []


# ---- 効き ----

def _skill(team: Team, role: str, s: StaffSettings) -> int:
    m = (team.staff or {}).get(role)
    return int(m["skill"]) if m else s.neutral


def coach_means(league: League) -> dict[str, float]:
    """役割 → 12 球団のそのコーチの腕の平均(コーチの効きの標準。D-471)。職員がいなければ空。"""
    out = {}
    for role in COACHES:
        vals = [int(t.staff[role]["skill"]) for t in league.teams if role in (t.staff or {})]
        if vals:
            out[role] = sum(vals) / len(vals)
    return out


def coach_factors(team: Team, means: dict | None = None, settings: StaffSettings | None = None) -> dict[str, tuple[float, float]]:
    """コーチの効き(D-471):見せる能力の項目 → (伸びの倍率, 衰えの倍率)。腕が 12 球団の同じ役割の平均(means)と同じなら標準
    (リーグ全体の能力の水準を動かさないため。means がなければ腕 60 が標準)。職員がいなければ空(標準)。"""
    s = settings or default_settings()
    if not team.staff:
        return {}
    co = s.data["coach"]
    out = {}
    for role in COACHES:
        d = _skill(team, role, s) - float((means or {}).get(role, s.neutral))
        g = max(0.0, 1.0 + float(co["growth_per_point"]) * d)
        dc = max(0.0, 1.0 - float(co["decline_per_point"]) * d)
        for item in co["items"][role]:
            out[item] = (g, dc)
    return out


def scout_factor(team: Team, settings: StaffSettings | None = None) -> float:
    """スカウトの効き(D-472):見立てのずれに掛ける倍率。"""
    s = settings or default_settings()
    if not team.staff or "scout" not in team.staff:
        return 1.0
    sc = s.data["scout"]
    v = 1.0 - float(sc["per_point"]) * (_skill(team, "scout", s) - s.neutral)
    return max(float(sc["min"]), min(float(sc["max"]), v))


def level_of(skill: int, settings: StaffSettings | None = None) -> int:
    s = settings or default_settings()
    mg = s.data["manager"]
    raw = (int(skill) - s.neutral) / float(mg["level_step"])
    level = int(raw + 0.5) if raw >= 0 else -int(-raw + 0.5)  # 四捨五入(0.5 は 0 から遠い方へ)
    return max(-int(mg["max_level"]), min(int(mg["max_level"]), level))


def manager_level(team: Team, settings: StaffSettings | None = None) -> int:
    """監督の采配の腕の段階(−2〜+2。D-470)。監督がいなければ 0。"""
    m = (team.staff or {}).get("manager")
    return level_of(int(m["skill"]), settings) if m else 0


def manager_pref(team: Team) -> tuple[str, str] | None:
    """監督の方針の好み(チーム方針の項目, 値)。なければ None(D-470)。"""
    m = (team.staff or {}).get("manager")
    return TRAIT_POLICY.get(m.get("trait", "none")) if m else None


def pull_rules(level: int, settings: StaffSettings | None = None) -> dict:
    """采配の腕の段階 → 先発の代え方(game.py が使う):share(上限の何割で代えるか)、early(終盤の接戦で早めに代えるか)、run_shift(失点の上限の増減)。"""
    s = settings or default_settings()
    mg = s.data["manager"]
    if level > 0:
        return {"share": 1.0 - float(mg["pull_share_per_level"]) * level, "early": True, "from_inning": int(mg["pull_from_inning"]), "margin": int(mg["pull_close_margin"]), "run_shift": -1 if level >= 2 else 0, "prefer_overall": True}
    if level < 0:
        return {"share": 1.0 + float(mg["stay_share_per_level"]) * -level, "early": False, "from_inning": int(mg["pull_from_inning"]), "margin": int(mg["pull_close_margin"]), "run_shift": 1 if level <= -2 else 0, "prefer_overall": False}
    return {"share": 1.0, "early": False, "from_inning": 0, "margin": 0, "run_shift": 0, "prefer_overall": False}


# ---- 年俸(D-474) ----

def team_cost(team: Team, season: int, reserve: bool = False, settings: StaffSettings | None = None) -> int:
    """その球団が season シーズンに払う職員の年俸(契約している職員と、解任した職員の残り)。
    reserve なら、空いている役割に標準の腕(C)の職員の年俸を見込んでおく(職員の段階の前の契約の段階で、雇う分を残すため。D-474)。"""
    total = sum(int(m["salary"]) for m in (team.staff or {}).values() if m.get("until") is None or int(m["until"]) >= int(season))
    total += sum(int(d["salary"]) for d in (team.staff_dead or []) if int(d["until"]) >= int(season))
    if reserve:
        s = settings or default_settings()
        total += sum(s.salary(r, s.neutral) for r in vacancies(team))
    return total


def staff_active(league: League) -> bool:
    """このリーグに職員の仕組みがあるか(どこかの球団に職員がいるか、市場に職員がいる。事前運転・試験用のリーグは偽)。"""
    return bool(league.staff_free) or any(t.staff for t in league.teams)


def league_costs(league: League, facility_settings=None, season: int = 1) -> dict[str, int]:
    """球団 → 設備の維持費 + 職員の年俸(空いている役割の見込みを含む。年俸に使える額から引く額。D-383、D-474)。"""
    from .facilities import team_upkeep

    active = staff_active(league)
    return {t.id: team_upkeep(t, facility_settings) + (team_cost(t, season, reserve=True) if active else 0) for t in league.teams}


def room(team: Team, funds: dict | None, season: int, facility_settings=None) -> int | None:
    """年俸に使える額の残り(資金 − 設備の維持費 − 職員の年俸 − 総年俸)。資金がなければ None(制限なし)。"""
    from .contracts import team_salary
    from .facilities import team_upkeep

    if not funds or team.id not in funds:
        return None
    return int(funds[team.id]) - team_upkeep(team, facility_settings) - team_cost(team, season) - team_salary(team)


# ---- 担当の記録と年齢(年度の確定) ----

def record_season(league: League, year: int) -> None:
    """年度の確定のはじめに、そのシーズンの担当(各球団・各役割にいた職員)を記録する(D-477)。"""
    for t in league.teams:
        for role in ROLES:
            m = (t.staff or {}).get(role)
            if m:
                league.staff_log.append({"year": int(year), "team_id": t.id, "role": role, "staff_id": m["id"], "name": name_of(m), "player_id": m.get("player_id")})


def age_staff(league: League, settings: StaffSettings | None = None) -> None:
    """職員が 1 歳年を取る。市場の職員は、やめる年齢になったら去る(球団の職員は契約が終わるまで続ける。D-469)。"""
    s = settings or default_settings()
    for m in all_staff(league):
        m["age"] = int(m["age"]) + 1
    league.staff_free = [m for m in league.staff_free if int(m["age"]) < int(s.data["age"]["retire"])]


# ---- 解任・雇う・入れ替える(D-475、D-476) ----

def dismiss(league: League, team: Team, role: str, season: int, reason: str = "dismiss") -> dict | None:
    """職員を解任する(残りの年俸は払う:season 以降の契約の分。市場に戻る)。戻り値は解任した職員。"""
    m = (team.staff or {}).pop(role, None)
    if m is None:
        return None
    if m.get("until") is not None and int(m["until"]) >= int(season):
        team.staff_dead.append({"salary": int(m["salary"]), "until": int(m["until"]), "name": name_of(m), "role": role})
    _to_market(league, m, team.id, reason)
    return m


def _to_market(league: League, m: dict, team_id: str | None, reason: str) -> None:
    m["until"] = None
    m["former_team"] = team_id
    m["left"] = reason
    league.staff_free.append(m)


def hire(league: League, team: Team, staff_id: str, season: int) -> dict:
    """市場の職員を雇う(契約は season から候補の年数)。その役割に職員がいれば先に解任しておくこと。"""
    m = next((x for x in league.staff_free if x["id"] == staff_id), None)
    if m is None:
        raise ValueError("職員の市場にいない職員です")
    if m["role"] in (team.staff or {}):
        raise ValueError(f"{default_settings().label(m['role'])}はすでにいます")
    league.staff_free.remove(m)
    m.pop("former_team", None)
    m.pop("left", None)
    m["until"] = int(season) + int(m["years"]) - 1
    team.staff[m["role"]] = m
    return m


def swap(league: League, team: Team, role: str, staff_id: str, season: int) -> dict:
    """チームのページの入れ替え(D-476):今の職員を解任の扱いで市場へ戻し、市場の候補を雇う。"""
    m = next((x for x in league.staff_free if x["id"] == staff_id), None)
    if m is None or m["role"] != role:
        raise ValueError("その役割の職員の市場の候補を選んでください")
    dismiss(league, team, role, season, "swap")
    return hire(league, team, staff_id, season)


# ---- 職員の段階(D-474、D-475) ----

def vacancies(team: Team) -> list[str]:
    return [r for r in ROLES if r not in (team.staff or {})]


def start_stage(league: League, proc, parts: NameParts, retired: list, career_war: dict, my_team_id: str | None, settings: StaffSettings | None = None) -> None:
    """職員の段階の始め(年度の確定のとき):契約切れ → AI の監督の解任 → 新しい候補(架空・元選手)。proc.staff_on を真にする。
    retired は引退した選手({player_id, family_name, given_name, role, age, team_id, foreign})、career_war は選手 ID → 通算の WAR。"""
    s = settings or default_settings()
    season = int(proc.year) + 1
    retire_age = int(s.data["age"]["retire"])
    events: list[dict] = []
    for t in league.teams:
        for role in ROLES:
            m = (t.staff or {}).get(role)
            if m is None or m.get("until") is None or int(m["until"]) >= season:
                continue
            del t.staff[role]
            events.append({"kind": "expire", "team_id": t.id, "role": role, "staff_id": m["id"], "name": name_of(m)})
            if int(m["age"]) < retire_age:
                _to_market(league, m, t.id, "expire")
        t.staff_dead = [d for d in (t.staff_dead or []) if int(d["until"]) >= season]
    fire = {int(k): float(v) for k, v in s.data["ai"]["fire"].items()}
    for t in league.teams:
        if t.id == my_team_id or t.staff_manual or "manager" not in (t.staff or {}):
            continue
        p = fire.get(int(proc.ranks.get(t.id, 0) or 0), 0.0)
        if p > 0 and random.Random(derive_seed(proc.seed, f"staff:fire:{t.id}")).random() < p:
            m = dismiss(league, t, "manager", season, "fired")
            events.append({"kind": "fired", "team_id": t.id, "role": "manager", "staff_id": m["id"], "name": name_of(m)})
    g = s.data["generate"]
    rng = random.Random(derive_seed(proc.seed, "staff:new"))
    names = NameGenerator(parts, rng, _used_names(league))
    y = int(proc.year)
    n = 0
    for role in ROLES:
        for _ in range(int(g["per_role"])):
            n += 1
            league.staff_free.append(make_member(rng, names, role, f"S{y:02d}N{n:03d}", float(g["market_mean"]), s))
    taken = {m.get("player_id") for m in all_staff(league) if m.get("player_id")}
    formers = sorted((x for x in retired if x["player_id"] not in taken and career_war.get(x["player_id"], 0.0) >= float(g["former_min_war"])), key=lambda x: (-career_war.get(x["player_id"], 0.0), x["player_id"]))[: int(g["former_max"])]
    for k, x in enumerate(formers, start=1):
        r = rng.random()
        if x["role"] == "pitcher":
            role = "pitching" if r < 0.6 else "manager" if r < 0.85 else "scout"
        else:
            role = "batting" if r < 0.35 else "fielding" if r < 0.7 else "manager" if r < 0.9 else "scout"
        bonus = min(float(g["former_bonus_max"]), float(g["former_war_bonus"]) * career_war.get(x["player_id"], 0.0))
        m = make_member(rng, names, role, f"S{y:02d}X{k:02d}", float(g["former_mean"]), s, age=max(int(x["age"]), 30), origin="former", player_id=x["player_id"], name=(x["family_name"], x["given_name"]), bonus=bonus, foreign=bool(x.get("foreign")))
        league.staff_free.append(m)
        events.append({"kind": "former", "team_id": x.get("team_id"), "role": role, "staff_id": m["id"], "name": name_of(m)})
    trim_market(league, s)
    proc.staff_on = True
    proc.staff_events = events
    proc.staff_offers = []
    proc.staff_results = []
    proc.staff_done = False


def trim_market(league: League, settings: StaffSettings | None = None) -> None:
    """市場は決まった人数まで(腕の高い順に残す。同じなら ID の順)。"""
    s = settings or default_settings()
    cap = int(s.data["generate"]["pool_max"])
    if len(league.staff_free) > cap:
        keep = {m["id"] for m in sorted(league.staff_free, key=lambda m: (-int(m["skill"]), m["id"]))[:cap]}
        league.staff_free = [m for m in league.staff_free if m["id"] in keep]


def ai_wishes(team: Team, league: League, budget: int | None, settings: StaffSettings | None = None) -> list[str]:
    """AI の希望(D-474):空いている役割ごとに、資金に収まる候補を腕の高い順に 3 人まで(役割の順に並べる)。"""
    s = settings or default_settings()
    out = []
    for role in vacancies(team):
        pool = sorted((m for m in league.staff_free if m["role"] == role and (budget is None or int(m["salary"]) <= budget)), key=lambda m: (-int(m["skill"]), int(m["salary"]), m["id"]))
        out += [m["id"] for m in pool[: int(s.data["ai"]["wishes"])]]
    return out


def close_stage(league: League, proc, funds: dict | None, facility_settings, my_team_id: str | None, my_ai: bool, parts: NameParts, settings: StaffSettings | None = None) -> list[dict]:
    """職員の市場を締める(D-474):前年の順位が低い球団から 1 人ずつ、希望のうち残っている最初の候補を雇う(空いている役割だけ)。
    誰も雇わなくなるまで回し、まだ空いている役割は、市場の残りの最も安い候補で埋める(いなければ E の職員を作る)。
    my_ai なら、あなたの球団の希望も AI と同じ方針で。戻り値は雇った記録。"""
    s = settings or default_settings()
    season = int(proc.year) + 1
    teams = {t.id: t for t in league.teams}
    order = [tid for tid in proc.order if tid in teams] + [t.id for t in league.teams if t.id not in proc.order]
    results: list[dict] = []

    def budget(team: Team) -> int | None:
        return room(team, funds, season, facility_settings)

    def wishes(team: Team) -> list[str]:
        if team.id == my_team_id and not my_ai:
            return [x for x in proc.staff_offers]
        return ai_wishes(team, league, budget(team), s)

    lists = {tid: wishes(teams[tid]) for tid in order}
    rnd = 0
    while True:
        rnd += 1
        signed = 0
        for tid in order:
            team = teams[tid]
            free = {m["id"]: m for m in league.staff_free}
            for sid in list(lists[tid]):
                m = free.get(sid)
                lists[tid].remove(sid)
                if m is None or m["role"] not in vacancies(team):
                    continue
                b = budget(team)
                if b is not None and int(m["salary"]) > b:
                    continue
                hire(league, team, sid, season)
                results.append({"round": rnd, "team_id": tid, "role": m["role"], "staff_id": sid, "name": name_of(m), "skill": int(m["skill"]), "salary": int(m["salary"]), "years": int(m["years"]), "by": "wish"})
                signed += 1
                break
            if team.id != my_team_id or my_ai:
                if not lists[tid] and vacancies(team):
                    lists[tid] = ai_wishes(team, league, budget(team), s)  # 希望を取られたら選び直す
        if not signed:
            break
    rng = random.Random(derive_seed(proc.seed, "staff:fill"))
    names = None
    n = 0
    for tid in order:
        team = teams[tid]
        for role in vacancies(team):
            pool = sorted((m for m in league.staff_free if m["role"] == role), key=lambda m: (int(m["salary"]), -int(m["skill"]), m["id"]))
            if pool:
                m = pool[0]
            else:
                if names is None:
                    names = NameGenerator(parts, rng, _used_names(league))
                n += 1
                m = make_member(rng, names, role, f"S{int(proc.year):02d}F{n:03d}", float(s.data["skill"]["min"]), s)
                m["skill"] = min(int(m["skill"]), int(s.data["skill"]["ranks"]["D"]) - 1)
                m["salary"] = s.salary(role, int(m["skill"]))
                league.staff_free.append(m)
            hire(league, team, m["id"], season)
            results.append({"round": 0, "team_id": tid, "role": role, "staff_id": m["id"], "name": name_of(m), "skill": int(m["skill"]), "salary": int(m["salary"]), "years": int(m["years"]), "by": "fill"})
    trim_market(league, s)
    proc.staff_results = list(proc.staff_results or []) + results
    proc.staff_offers = []
    proc.staff_done = True
    proc._cache.clear()  # スカウトが代わると評価が変わる(保存して読み込んでも同じ結果にする)
    from .policy import refresh_ai

    refresh_ai(league)  # 新しい監督の好みで、AI の球団のチーム方針を決め直す(D-470)
    return results


# ---- 保存の点検 ----

def check_member(d, where: str, problems: list[str], hired: bool) -> None:
    """職員の辞書の点検(読み込み)。"""
    if not isinstance(d, dict):
        problems.append(f"{where}: 職員のまとまり({{ }})が必要です")
        return
    for key in ("id", "family_name", "given_name", "role"):
        if not isinstance(d.get(key), str) or not d.get(key):
            problems.append(f"{where}.{key}: 文字列が必要です")
    if d.get("role") not in ROLES:
        problems.append(f"{where}.role: 役割は {' / '.join(ROLES)} のどれかです(値: {d.get('role')!r})")
    for key, lo, hi in (("age", 15, 120), ("skill", 1, 100), ("salary", 0, 10_000_000), ("years", 1, 10)):
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            problems.append(f"{where}.{key}: {lo}〜{hi} の整数が必要です(値: {v!r})")
    if d.get("trait") not in TRAITS:
        problems.append(f"{where}.trait: 好みは {' / '.join(TRAITS)} のどれかです(値: {d.get('trait')!r})")
    u = d.get("until")
    if hired and (isinstance(u, bool) or not isinstance(u, int) or u < 0):
        problems.append(f"{where}.until: 契約の満了シーズン(0 以上の整数)が必要です(値: {u!r})")
    if not hired and u is not None:
        problems.append(f"{where}.until: 市場の職員は契約を持ちません(値: {u!r})")
    if d.get("player_id") is not None and not isinstance(d.get("player_id"), str):
        problems.append(f"{where}.player_id: 元の選手の ID(文字列)か null が必要です")
