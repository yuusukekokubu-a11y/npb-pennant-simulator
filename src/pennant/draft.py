"""オフの手続き(F3-1。D-201〜D-207):契約更改(F3-2b)→ 自由契約 → ドラフト → 自由契約市場 → 自動補充。

年度の確定(加齢・能力の更新・引退)の後に、OffseasonProcedure を作って段階ごとに進める。
  - 契約更改(renewal):契約が満了した選手に提示し、選手が志望で受ける・断るを決める(F3-2b。negotiation.py。D-243〜D-252)
  - 自由契約(release):球団が選手を手放す。AI は自球団のスカウト評価の低い選手を上限まで(方針は ai_release)
  - ドラフト(draft):ウェーバー方式(前年の勝率が低い順。巡ごとに往復)。空き枠がない球団はパス。AI は ai_choose
  - 市場(market):手放された選手 + 指名されなかった候補。同じ順番で数巡。残った選手はリーグを去る
  - 完了(done):70 人に満たない球団を自動補充(offseason.replenish。F2 の穴埋め)
AI の判断は、真の能力ではなく、球団ごとのスカウト評価(scouting.quick_value)を使う(D-205)。判断の関数は差し替え可能。
評価のずれ(sd)は {"common", "item"} の 2 層(D-212)。手続きは評価方式の版(method)を持つ(D-215)。
操作する球団がないとき(観戦のみ)は、run_ai_offseason で一気に進める。事前運転も同じ関数を使う(D-210)。
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from .abilities import BATTER, FIELDER_POSITIONS, PITCHER, PITCHER_POSITIONS
from .config import ConfigError, GenerationConfig, NameParts, _Checker, _read_json
from .contracts import ContractSettings, cap_of, contract_years, expected_war, is_hard, rate_for, salary_for, set_contract, team_salary
from .negotiation import NegotiationSettings, ai_offer, ai_years, depth_ranks, depth_slots, ensure_preferences, judge, noise_for, standing_ranks
from .game_config import load_game_config
from .generate import make_rookie
from .models import League, Player, Team
from .names import NameGenerator
from .offseason import OffseasonSettings, PlayerNote, replenish
from .scouting import GRADES, SCOUT_METHOD, ScoutReport, ceiling_cuts, quick_value, report
from .season import derive_seed

SUPPORTED_FORMAT_VERSION = 1
PHASES = ("renewal", "release", "draft", "market", "done")
PHASE_LABELS = {"renewal": "契約更改", "release": "自由契約", "draft": "ドラフト", "market": "自由契約市場", "done": "完了"}
MAX_ROSTER = 70


# ---- 設定(data/draft.json) ----

@dataclass(frozen=True)
class DraftSettings:
    data: dict
    source: str
    gen_config: object = None  # AI の判断で人数の目安を見るための生成の設定(手続きの開始時に入れる)

    @property
    def rounds(self) -> int:
        return int(self.data["draft"]["rounds"])

    @property
    def candidate_factor(self) -> float:
        return float(self.data["draft"]["candidate_factor"])

    @property
    def market_rounds(self) -> int:
        return int(self.data["market"]["rounds"])

    def ai(self, key: str):
        return self.data["ai"][key]

    @property
    def default_level(self) -> str:
        return str(self.data["scouting"]["default_level"])

    def level_sd(self, level: str) -> dict[str, float]:
        """ずれの段階 → {"common": 共通の見誤り, "item": 項目ごとの見誤り}(標準偏差。D-212)。"""
        v = self.data["scouting"]["levels"][level]
        return {"common": float(v["common"]), "item": float(v["item"])}

    @property
    def levels(self) -> tuple[str, ...]:
        return tuple(self.data["scouting"]["levels"])

    @property
    def margin_z(self) -> float:
        return float(self.data["scouting"]["margin_z"])

    @property
    def ceiling_shares(self) -> dict[str, float]:
        return {g: float(v) for g, v in self.data["scouting"]["ceiling_shares"].items()}


def load_draft_settings(path: str | Path | None = None) -> DraftSettings:
    data, source = _read_json(path, "draft.json")
    return validate_draft_settings(data, source)


def validate_draft_settings(data, source: str = "(辞書)") -> DraftSettings:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    d = c.section(c.get(root, "draft", ""), "draft")
    c.integer(c.get(d, "rounds", "draft"), "draft.rounds", 1, 30)
    c.number(c.get(d, "candidate_factor", "draft"), "draft.candidate_factor", 1, 5)
    m = c.section(c.get(root, "market", ""), "market")
    c.integer(c.get(m, "rounds", "market"), "market.rounds", 0, 30)
    a = c.section(c.get(root, "ai", ""), "ai")
    c.integer(c.get(a, "release_max", "ai"), "ai.release_max", 0, 20)
    c.integer(c.get(a, "release_min_age", "ai"), "ai.release_min_age", 18, 60)
    c.number(c.get(a, "release_below", "ai"), "ai.release_below", 0, 100)
    c.number(c.get(a, "need_bonus", "ai"), "ai.need_bonus", 0, 100)
    c.number(c.get(a, "market_gain_min", "ai"), "ai.market_gain_min", -50, 50)
    c.number(c.get(a, "surplus_penalty", "ai"), "ai.surplus_penalty", 0, 100)
    bonus = c.section(c.get(a, "ceiling_bonus", "ai"), "ai.ceiling_bonus")
    for g in GRADES:
        c.number(c.get(bonus, g, "ai.ceiling_bonus"), f"ai.ceiling_bonus.{g}", -50, 50)
    s = c.section(c.get(root, "scouting", ""), "scouting")
    levels = c.section(c.get(s, "levels", "scouting"), "scouting.levels")
    for level in ("small", "medium", "large"):
        lv = c.section(c.get(levels, level, "scouting.levels"), f"scouting.levels.{level}")
        for key in ("common", "item"):
            c.number(c.get(lv, key, f"scouting.levels.{level}"), f"scouting.levels.{level}.{key}", 0, 30)
    default = c.get(s, "default_level", "scouting")
    if default not in ("small", "medium", "large"):
        c.add("scouting.default_level", f"small / medium / large のどれかにしてください(値: {default!r})")
    c.number(c.get(s, "margin_z", "scouting"), "scouting.margin_z", 0, 5)
    shares = c.section(c.get(s, "ceiling_shares", "scouting"), "scouting.ceiling_shares")
    total = 0.0
    for g in GRADES:
        v = c.number(c.get(shares, g, "scouting.ceiling_shares"), f"scouting.ceiling_shares.{g}", 0, 1)
        total += v or 0
    if shares is not None and abs(total - 1) > 1e-6:
        c.add("scouting.ceiling_shares", f"合計が 1 になるようにしてください(値: {total:.3f})")
    if c.problems:
        raise ConfigError(source, c.problems)
    return DraftSettings(copy.deepcopy(root), source)


# ---- 最低人数(一軍を組むのに必要な人数。D-203) ----

def minimum_positions(game_config=None) -> dict[str, int]:
    """ポジション → 最低人数。先発・救援・捕手は一軍の人数、その他の野手のポジションは 1。"""
    ar = (game_config or load_game_config())["active_roster"]
    mins = {"SP": int(ar["starters"]), "RP": int(ar["relievers"]), "C": int(ar["catchers"])}
    for pos in FIELDER_POSITIONS:
        mins.setdefault(pos, 1)
    return mins


def minimum_batters(game_config=None) -> int:
    return int((game_config or load_game_config())["active_roster"]["fielders"])


def position_counts(players: list[Player]) -> dict[str, int]:
    counts = {pos: 0 for pos in PITCHER_POSITIONS + FIELDER_POSITIONS}
    for p in players:
        counts[p.position] = counts.get(p.position, 0) + 1
    return counts


def shortages(players: list[Player], mins: dict[str, int], min_batters: int) -> dict[str, int]:
    """足りないポジション → 足りない人数(野手の合計が足りないときは "batter" に入れる)。"""
    counts = position_counts(players)
    out = {pos: n - counts.get(pos, 0) for pos, n in mins.items() if counts.get(pos, 0) < n}
    batters = sum(1 for p in players if p.role == BATTER)
    if batters < min_batters:
        out["batter"] = min_batters - batters
    return out


def can_release(players: list[Player], player: Player, mins: dict[str, int], min_batters: int) -> bool:
    """この選手を外しても、最低人数の不足が今より増えないか(すでに足りないポジションは、それ以上減らさない)。"""
    rest = [p for p in players if p.id != player.id]
    return not new_shortages(players, rest, mins, min_batters)


def new_shortages(before: list[Player], after: list[Player], mins: dict[str, int], min_batters: int) -> dict[str, int]:
    """外した後に増える不足(ポジション → 増えた人数)。"""
    old = shortages(before, mins, min_batters)
    now = shortages(after, mins, min_batters)
    return {pos: n - old.get(pos, 0) for pos, n in now.items() if n > old.get(pos, 0)}


# ---- 手続きの状態 ----

@dataclass
class OffseasonProcedure:
    year: int  # 終わったシーズンの番号
    seed: int
    order: list[str]  # 1 巡目の指名の順番(前年の勝率が低い順)
    rounds: int
    market_rounds: int
    cuts: list[float]  # 天井の段階の境目
    phase: str = "renewal"
    method: int = SCOUT_METHOD  # 評価方式の版(D-215。版 8 の手続きは 1)
    round: int = 1  # 今の巡(1 から)
    index: int = 0  # 今の巡の中の何番目か(0 から。往復は current_team で解決)
    candidates: list[Player] = field(default_factory=list)  # ドラフトの候補(まだ指名されていない)
    market: list[Player] = field(default_factory=list)  # 自由契約市場
    picks: list[dict] = field(default_factory=list)  # 指名・獲得の履歴({phase, round, team_id, player_id, name, position, age})
    released: list[dict] = field(default_factory=list)  # 手放した選手({team_id, player_id, name, position, age})
    filled: list[dict] = field(default_factory=list)  # 自動補充で入った選手(履歴に残すため。D-216。保存しない:完了と同時に履歴へ移る)
    my_release_done: bool = False
    ai_release_done: bool = False
    prerun: bool = False  # 事前運転の手続き(入団時の評価に年を持たせない。保存しない)
    rate: float = 0.0  # このオフの年俸の単価(1 WAR あたりの万円。D-234)
    negotiations: dict = field(default_factory=dict)  # 選手 ID → 更改の交渉(F3-2b。D-244。{team_id, player_id, name, ..., auto_salary, ai_years, expected, context, status, offers})
    ranks: dict = field(default_factory=dict)  # 球団 → 前年のリーグ内の順位(勝利軸。D-250)。事前運転では空
    budget_releases: list[dict] = field(default_factory=list)  # 予算超過の解消で自由契約になった選手({team_id, player_id, name, salary})
    contracts_done: bool = False  # 更改と AI の超過の解消を済ませたか
    _cache: dict = field(default_factory=dict, repr=False, compare=False)  # (球団, 選手) → (総合の推定値, 天井)。保存しない

    def current_team(self) -> str | None:
        """今の指名権を持つ球団(段階が draft / market のとき)。巡の終わりなら None。"""
        if self.index >= len(self.order):
            return None
        order = self.order if self.round % 2 == 1 else list(reversed(self.order))
        return order[self.index]

    def total_rounds(self) -> int:
        return self.rounds if self.phase == "draft" else self.market_rounds

    @property
    def renewals(self) -> list[dict]:
        """更改した選手(受けた交渉)の一覧(画面の「契約更改の結果」。F3-2a の形)。"""
        out = []
        for e in self.negotiations.values():
            if e["status"] != "accepted":
                continue
            last = e["offers"][-1] if e["offers"] else {"salary": e["auto_salary"], "years": e.get("ai_years", 1)}
            out.append({k: e[k] for k in ("team_id", "player_id", "name", "role", "position", "age", "old_salary", "expected")} | {"salary": int(last["salary"]), "years": int(last["years"]), "offers": len(e["offers"])})
        return out

    def to_dict(self, plain) -> dict:
        return {
            "year": self.year, "seed": self.seed, "order": list(self.order), "rounds": self.rounds, "market_rounds": self.market_rounds,
            "cuts": [round(c, 4) for c in self.cuts], "phase": self.phase, "method": self.method, "round": self.round, "index": self.index,
            "candidates": [plain(p) for p in self.candidates], "market": [plain(p) for p in self.market],
            "picks": [dict(x) for x in self.picks], "released": [dict(x) for x in self.released],
            "my_release_done": self.my_release_done, "ai_release_done": self.ai_release_done,
            "rate": round(float(self.rate), 4), "negotiations": {pid: copy.deepcopy(e) for pid, e in self.negotiations.items()}, "ranks": dict(self.ranks),
            "budget_releases": [dict(x) for x in self.budget_releases], "contracts_done": self.contracts_done,
        }

    @classmethod
    def from_dict(cls, d: dict, player_from) -> "OffseasonProcedure":
        proc = cls(int(d["year"]), int(d["seed"]), [str(t) for t in d["order"]], int(d["rounds"]), int(d["market_rounds"]), [float(c) for c in d["cuts"]])
        proc.phase = str(d["phase"])
        proc.method = int(d.get("method", 1))
        proc.round = int(d["round"])
        proc.index = int(d["index"])
        proc.candidates = [player_from(x) for x in d["candidates"]]
        proc.market = [player_from(x) for x in d["market"]]
        proc.picks = [dict(x) for x in d["picks"]]
        proc.released = [dict(x) for x in d["released"]]
        proc.my_release_done = bool(d.get("my_release_done", False))
        proc.ai_release_done = bool(d.get("ai_release_done", False))
        proc.rate = float(d.get("rate", 0.0))
        if "negotiations" in d:
            proc.negotiations = {str(pid): copy.deepcopy(e) for pid, e in d["negotiations"].items()}
        else:  # 版 10:更改は済んでいる(全員が受けた形にする。D-253)
            for x in d.get("renewals", []):
                proc.negotiations[str(x["player_id"])] = {**{k: x.get(k) for k in ("team_id", "player_id", "name", "role", "position", "age", "old_salary", "expected")}, "auto_salary": int(x["salary"]), "ai_years": int(x["years"]), "context": {}, "status": "accepted", "offers": []}
        proc.ranks = {str(k): int(v) for k, v in d.get("ranks", {}).items()}
        proc.budget_releases = [dict(x) for x in d.get("budget_releases", [])]
        proc.contracts_done = bool(d.get("contracts_done", False))
        return proc


# ---- 候補の生成と順番 ----

def draft_order(league: League, records: dict[str, tuple[int, int]] | None) -> list[str]:
    """1 巡目の順番:前年の勝率が低い球団から(同率は ID の順)。成績がなければ ID の順。"""
    teams = [t.id for t in league.teams]
    if not records:
        return sorted(teams)

    def pct(tid):
        w, l = records.get(tid, (0, 0))
        return w / (w + l) if w + l else 0.5

    return sorted(teams, key=lambda tid: (pct(tid), tid))


def candidate_slots(config: GenerationConfig, count: int) -> list[tuple[str, str]]:
    """候補の (役割, ポジション):人数の目安(roster)の比率に合わせる(端数は多い順)。"""
    roster = {pos: int(n) for pos, n in config["roster"]["pitchers"].items()}
    roster.update({pos: int(n) for pos, n in config["roster"]["fielders"].items()})
    total = sum(roster.values())
    raw = {pos: count * n / total for pos, n in roster.items()}
    alloc = {pos: int(math.floor(v)) for pos, v in raw.items()}
    rest = count - sum(alloc.values())
    for pos in sorted(raw, key=lambda k: (raw[k] - alloc[k]), reverse=True)[:rest]:
        alloc[pos] += 1
    slots = []
    for pos in PITCHER_POSITIONS + FIELDER_POSITIONS:
        role = PITCHER if pos in PITCHER_POSITIONS else BATTER
        slots += [(role, pos)] * alloc[pos]
    return slots


def make_candidates(league: League, seed: int, config: GenerationConfig, parts: NameParts, settings: DraftSettings, year: int, calibration: dict[str, float] | None, id_prefix: str = "D") -> list[Player]:
    """ドラフトの候補(指名数 × 1.5 倍ほど)。既存の新人の生成で作り、校正の定数を足す(D-202)。"""
    count = int(math.ceil(settings.rounds * len(league.teams) * settings.candidate_factor))
    rng = random.Random(derive_seed(seed, "candidates"))
    names = NameGenerator(parts, rng, {(p.family_name, p.given_name) for p in league.all_players()})
    out = []
    for i, (role, pos) in enumerate(candidate_slots(config, count), start=1):
        out.append(make_rookie(config, names, rng, role, pos, f"{id_prefix}{year + 1:02d}C{i:03d}", (calibration or {}).get(role, 0.0)))
    return out


def start_procedure(league: League, seed: int, config: GenerationConfig, parts: NameParts, settings: DraftSettings, year: int, calibration: dict[str, float] | None, records: dict[str, tuple[int, int]] | None, id_prefix: str = "D") -> OffseasonProcedure:
    if settings.gen_config is None:
        object.__setattr__(settings, "gen_config", config)
    candidates = make_candidates(league, seed, config, parts, settings, year, calibration, id_prefix)
    cuts = ceiling_cuts(candidates, settings.ceiling_shares)
    return OffseasonProcedure(year, seed, draft_order(league, records), settings.rounds, settings.market_rounds, cuts, candidates=candidates, ranks=standing_ranks(league.teams, records))


# ---- 評価 ----

def scout_report(proc: OffseasonProcedure, player: Player, team_id: str, sd: dict, settings: DraftSettings) -> ScoutReport:
    return report(player, team_id, proc.seed, sd, proc.cuts, settings.margin_z, proc.method)


def cached_value(proc: OffseasonProcedure, player: Player, team_id: str, sd: dict) -> tuple[float, str]:
    """総合の推定値と天井(手続きの間は使い回す。同じ入力なら同じ値)。"""
    key = (team_id, player.id)
    v = proc._cache.get(key)
    if v is None:
        v = quick_value(player, team_id, proc.seed, sd, proc.cuts, proc.method)
        proc._cache[key] = v
    return v


# ---- AI の判断(差し替え可能。D-205) ----

def ai_release(team: Team, proc: OffseasonProcedure, sd: dict, settings: DraftSettings, mins: dict[str, int], min_batters: int) -> list[Player]:
    """手放す選手:年齢が下限以上で、自球団の評価(総合の推定値)が基準より低い順に、上限まで。最低人数は守る。"""
    limit = int(settings.ai("release_max"))
    if limit <= 0:
        return []
    pool = [p for p in team.players if p.age >= int(settings.ai("release_min_age"))]
    scored = sorted(pool, key=lambda p: (cached_value(proc, p, team.id, sd)[0], p.id))
    out: list[Player] = []
    remaining = list(team.players)
    for p in scored:
        if len(out) >= limit:
            break
        if cached_value(proc, p, team.id, sd)[0] >= float(settings.ai("release_below")):
            break
        if can_release(remaining, p, mins, min_batters):
            out.append(p)
            remaining = [x for x in remaining if x.id != p.id]
    return out


def ai_value(player: Player, team: Team, proc: OffseasonProcedure, sd: dict, settings: DraftSettings, need: dict[str, int], surplus: set[str] | None = None) -> float:
    """AI が候補につける点数:総合の推定値 + 天井の加点 + 足りないポジションなら加点 − 人数の目安を超えているポジションなら減点。"""
    est, ceiling = cached_value(proc, player, team.id, sd)
    v = est + float(settings.ai("ceiling_bonus")[ceiling])
    if player.position in need or (player.role == BATTER and "batter" in need):
        v += float(settings.ai("need_bonus"))
    elif surplus and player.position in surplus:
        v -= float(settings.ai("surplus_penalty"))
    return v


def surplus_positions(team: Team, config: GenerationConfig) -> set[str]:
    """人数の目安(generation.json の roster)以上に人がいるポジション。"""
    target = {pos: int(n) for pos, n in config["roster"]["pitchers"].items()}
    target.update({pos: int(n) for pos, n in config["roster"]["fielders"].items()})
    counts = position_counts(team.players)
    return {pos for pos, n in target.items() if counts.get(pos, 0) >= n}


def ai_choose(team: Team, pool: list[Player], proc: OffseasonProcedure, sd: dict, settings: DraftSettings, mins: dict[str, int], min_batters: int, phase: str) -> Player | None:
    """指名(獲得)する選手。空き枠がなければ None。市場では、自球団の最低の推定値より十分よい選手がいなければ見送る。"""
    if len(team.players) >= MAX_ROSTER or not pool:
        return None
    need = shortages(team.players, mins, min_batters)
    surplus = surplus_positions(team, settings.gen_config) if settings.gen_config is not None else set()
    best = max(pool, key=lambda p: (ai_value(p, team, proc, sd, settings, need, surplus), p.id))
    if phase == "market" and not need:
        worst = min((cached_value(proc, p, team.id, sd)[0] for p in team.players), default=0.0)
        if cached_value(proc, best, team.id, sd)[0] < worst + float(settings.ai("market_gain_min")):
            return None
    return best


# ---- 契約(F3-2a。D-232〜D-237) ----

@dataclass
class ContractContext:
    """手続きの中で契約を扱うための材料。war_history(選手 ID, 役割) → [(WAR, 出場), ...](新しい順)。scout(選手, 球団) → (総合の推定値, 天井)。
    乱数はすべて契約の系列(contract:…)で、AI の判断には使わない(D-237)。"""

    settings: ContractSettings
    rule: str  # none / loose / standard / strict
    tiers: dict  # 球団 → large / medium / small(きびしいのとき)
    war_history: object  # (選手 ID, 役割) → [(WAR, 出場), ...]
    sd_of: object  # 球団 ID → ずれ({common, item})
    scout: object = None  # (選手, 球団) → (総合の推定値, 天井)。bind(proc) で手続きのシードと区切りから作る
    rate: float = 0.0
    negotiation: NegotiationSettings | None = None  # 志望の判定の設定(F3-2b)。None なら判定なしで全員が受ける(F3-2a と同じ)
    league_seed: int = 0  # 志望の乱数のもと
    slots: dict | None = None  # ポジション → 一軍の枠の目安(出場機会軸)
    _cache: dict = field(default_factory=dict, repr=False, compare=False)  # (選手, 球団) → 見込み(手続きの間は年齢も能力も変わらないので使い回す)

    def bind(self, proc: "OffseasonProcedure") -> "ContractContext":
        if self.scout is None:
            self.scout = contract_scout(proc.seed, self.sd_of, proc.cuts)
        return self

    def cap(self, team_id: str) -> int | None:
        return cap_of(self.rule, self.tiers.get(team_id), self.settings)

    def hard(self) -> bool:
        return is_hard(self.rule)

    def expected(self, player: Player, team_id: str) -> tuple[float, str]:
        key = (player.id, team_id)
        v = self._cache.get(key)
        if v is None:
            v = expected_war(player.age, player.role, self.war_history(player.id, player.role), self.scout(player, team_id), self.settings)
            self._cache[key] = v
        return v

    def salary(self, player: Player, team_id: str) -> tuple[int, int, float]:
        """算定した (年俸, 契約年数, 見込みの WAR)。"""
        exp, _ = self.expected(player, team_id)
        return salary_for(exp, self.rate, self.settings), contract_years(player.age, exp, self.settings), exp


def contract_scout(seed: int, sd_of, cuts: list[float]):
    """所属球団の評価(契約用。乱数は contract の系列)を返す関数を作る。"""

    def scout(player: Player, team_id: str) -> tuple[float, str]:
        return quick_value(player, team_id, derive_seed(seed, "contract"), sd_of(team_id), cuts)

    return scout


def apply_contracts_start(league: League, proc: OffseasonProcedure, ctx: ContractContext, my_team_id: str | None, mins, min_batters) -> None:
    """手続きの最初の契約の処理(D-235、D-244):単価を求め直す → 契約が満了した全員の交渉を作る → AI 球団は交渉を最後まで進める(D-251)
    → 標準以上なら AI 球団の予算超過の解消。あなたの球団の交渉は契約更改の段階で(「おまかせ」は AI と同じ)、超過は自由契約の段階で手動。"""
    if proc.contracts_done:
        return
    ctx.bind(proc)
    expected = {}
    for team in league.teams:
        for p in team.players:
            expected[p.id] = ctx.expected(p, team.id)[0]
    ctx.rate = round(rate_for(list(expected.values()), len(league.teams), len(expected), ctx.settings), 4)
    proc.rate = ctx.rate
    neg = ctx.negotiation
    if neg is not None:
        ensure_preferences(league.all_players(), ctx.league_seed, neg)
        ensure_preferences(proc.candidates, ctx.league_seed, neg)
    sizes: dict[int, int] = {}
    for team in league.teams:
        sizes[team.league_index] = sizes.get(team.league_index, 0) + 1
    for team in league.teams:
        expiring = [p for p in team.players if p.contract is None or int(p.contract["until"]) <= proc.year]
        if not expiring:
            continue
        ranks = depth_ranks(team.players, lambda p: ctx.scout(p, team.id)[0]) if neg is not None else {}
        for p in expiring:
            exp = expected[p.id]
            context = {"rank": ranks.get(p.id, 0), "slots": round((ctx.slots or {}).get(p.position, 1.0), 3), "standing": proc.ranks.get(team.id), "league_size": sizes[team.league_index]}
            proc.negotiations[p.id] = {
                "team_id": team.id, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age,
                "old_salary": int(p.contract["salary"]) if p.contract else None, "auto_salary": salary_for(exp, ctx.rate, ctx.settings),
                "ai_years": ai_years(p.age, exp, neg, ctx.settings.default_years) if neg is not None else ctx.settings.default_years,
                "expected": round(exp, 2), "context": context, "status": "pending", "offers": [],
            }
    teams = {t.id: t for t in league.teams}
    for team in league.teams:
        if team.id == my_team_id:
            continue
        for e in [e for e in proc.negotiations.values() if e["team_id"] == team.id and e["status"] == "pending"]:
            ai_negotiate_entry(teams[e["team_id"]], e, proc, ctx)
    if ctx.hard():
        for team in league.teams:
            if team.id == my_team_id:
                continue
            resolve_overrun(team, proc, ctx, mins, min_batters)
    proc.contracts_done = True


# ---- 契約更改の交渉(F3-2b。D-244、D-250〜D-252) ----

def entry_player(team: Team, entry: dict) -> Player:
    return next(p for p in team.players if p.id == entry["player_id"])


def projected_total(team: Team, proc: OffseasonProcedure) -> int:
    """見込みの総年俸:契約が残る選手と更改済の選手は契約の年俸、未決定の選手は自動案の年俸(D-252)。"""
    total = 0
    for p in team.players:
        e = proc.negotiations.get(p.id)
        if e is not None and e["status"] == "pending":
            total += int(e["auto_salary"])
        elif p.contract:
            total += int(p.contract["salary"])
    return total


def offers_left(entry: dict, settings: NegotiationSettings | None) -> int:
    return 0 if settings is None else max(0, settings.max_offers - len(entry["offers"]))


def make_offer(team: Team, entry: dict, years: int, salary: int, proc: OffseasonProcedure, ctx: ContractContext) -> dict:
    """提示して答えを記録する。受けたら契約、断られて回数を使い切ったら自由契約(市場へ)。戻り値は提示の記録。"""
    player = entry_player(team, entry)
    neg = ctx.negotiation
    if neg is None:
        rec = {"years": int(years), "salary": int(salary), "accepted": True, "reason": None}
    else:
        r = judge(neg, player.preference or {}, int(years), int(salary), int(entry["auto_salary"]), player.age, entry["context"], ctx.rule, noise_for(proc.seed, player.id, neg))
        rec = {"years": int(years), "salary": int(salary), "accepted": bool(r["accepted"]), "reason": None if r["accepted"] else r["reason"]}
    entry["offers"].append(rec)
    if rec["accepted"]:
        set_contract(player, int(salary), int(years), proc.year + 1, "renew" if player.contract else "initial", offers=len(entry["offers"]))
        entry["status"] = "accepted"
    elif neg is not None and len(entry["offers"]) >= neg.max_offers:
        release_entry(team, entry, proc)
    return rec


def release_entry(team: Team, entry: dict, proc: OffseasonProcedure) -> None:
    """交渉をやめて自由契約(市場へ)。"""
    player = entry_player(team, entry)
    release_players(team, [player], proc)
    proc.released[-1]["note"] = "negotiation"
    entry["status"] = "released"


def ai_negotiate_entry(team: Team, entry: dict, proc: OffseasonProcedure, ctx: ContractContext) -> None:
    """AI の方針で交渉を最後まで進める(D-251):最初は自動案(若手の高い見込みは複数年)、断られたら見込みの高い選手にだけ年俸と年数を上げて再提示。
    標準以上で上限を超えるなら年俸は上げない。再提示しない選手は自由契約。"""
    neg = ctx.negotiation
    while entry["status"] == "pending":
        if neg is None:
            make_offer(team, entry, entry["ai_years"], entry["auto_salary"], proc, ctx)
            break
        o = ai_offer(len(entry["offers"]), int(entry["auto_salary"]), int(entry["ai_years"]), float(entry["expected"]), neg, ctx.rule, ctx.settings.max_years, ctx.settings.rounding)
        if o is None:
            release_entry(team, entry, proc)
            break
        years, salary = o
        auto = int(entry["auto_salary"])
        if salary > auto and ctx.hard():
            cap = ctx.cap(team.id)
            if cap is not None and projected_total(team, proc) - auto + salary > cap:
                salary = auto
        make_offer(team, entry, years, salary, proc, ctx)


def open_entries(proc: OffseasonProcedure, team_id: str) -> list[dict]:
    return [e for e in proc.negotiations.values() if e["team_id"] == team_id and e["status"] == "pending"]


def initialize_contracts(league: League, seed: int, ctx: ContractContext, prerun_years: int) -> float:
    """新規開始のとき、初期選手の契約を作り直す(D-232):年俸は所属球団の評価から算定(校正の後の能力で)、残りの年数は事前運転の契約から引き継ぐ
    (事前運転で契約を扱わなかったときは、AI 球団の更改と同じ方針の年数。見込みの高い若手は 2〜3 年、ほかは 1 年。D-243)。
    1 シーズン目から数えた満了シーズンに付け替える。戻り値は単価。"""
    all_players = league.all_players()
    ctx.scout = contract_scout(derive_seed(seed, "contract:init"), ctx.sd_of, ceiling_cuts(all_players, {"S": 0.05, "A": 0.15, "B": 0.30, "C": 0.30, "D": 0.20}))
    expected = {}
    for team in league.teams:
        for p in team.players:
            expected[p.id] = ctx.expected(p, team.id)[0]
    ctx.rate = round(rate_for(list(expected.values()), len(league.teams), len(expected), ctx.settings), 4)
    for team in league.teams:
        salaries = {p.id: salary_for(expected[p.id], ctx.rate, ctx.settings) for p in team.players}
        cap = ctx.cap(team.id)
        if ctx.hard() and cap is not None and sum(salaries.values()) > cap:
            # 標準以上で上限を超える球団は、最低年俸を超える分を同じ割合で縮めて、開始時に上限の中に収める(開始直後に選手を手放さなくて済むように)
            mn = ctx.settings.minimum
            excess = sum(v - mn for v in salaries.values())
            room = cap - mn * len(salaries)
            scale = max(0.0, room / excess) if excess > 0 else 0.0
            step = ctx.settings.rounding
            salaries = {pid: max(mn, int((mn + (v - mn) * scale) // step) * step) for pid, v in salaries.items()}
        for p in team.players:
            exp = expected[p.id]
            years = ai_years(p.age, exp, ctx.negotiation, ctx.settings.default_years) if ctx.negotiation is not None else ctx.settings.default_years
            remaining = max(1, int(p.contract["until"]) - prerun_years) if p.contract else years
            p.contract = None
            set_contract(p, salaries[p.id], remaining, 1, "initial")
    return ctx.rate


def state_war_history(state):
    """セーブデータの状態から、(選手 ID, 役割) → 直近 3 シーズンの [(WAR, 出場)](新しい順)を返す関数(履歴の集計から)。"""
    archives = list(state.history)[-3:][::-1]

    def war_history(pid: str, role: str) -> list[tuple[float, float]]:
        out = []
        for a in archives:
            line = a.war.get(pid)
            if line is None:
                continue
            if role == "batter":
                usage = float(a.records.batters.get(pid, {}).get("PA", 0))
                out.append((float(line.war), usage))
            else:
                usage = float(a.records.pitchers.get(pid, {}).get("OUTS", 0)) / 3.0
                out.append((float(line.war_ra), usage))
        return out

    return war_history


def state_context(state, proc: "OffseasonProcedure | None" = None) -> ContractContext:
    """セーブデータの状態から契約の文脈を作る(手続きがあればそのシードと区切りで評価を引く)。"""
    ctx = ContractContext(state.contract_settings, state.money_rule, dict(state.budget_tiers), state_war_history(state), state.scout_sd_of, negotiation=state.negotiation_settings, league_seed=state.league.seed, slots=depth_slots(state.gen_config))
    if proc is not None:
        ctx.bind(proc)
        ctx.rate = float(proc.rate)
    return ctx


def fill_missing_contracts(state) -> None:
    """版9以前のセーブデータ:契約のない選手に、算定した年俸と 1〜3 年の残り(別の乱数系列)を付ける(D-235)。"""
    league = state.league
    seed = derive_seed(league.seed, "contract:migrate")
    rng = random.Random(seed)
    ctx = ContractContext(state.contract_settings, state.money_rule, dict(state.budget_tiers), state_war_history(state), state.scout_sd_of)
    ctx.scout = contract_scout(seed, state.scout_sd_of, ceiling_cuts(league.all_players(), {"S": 0.05, "A": 0.15, "B": 0.30, "C": 0.30, "D": 0.20}))
    expected = {p.id: ctx.expected(p, t.id)[0] for t in league.teams for p in t.players}
    rate = rate_for(list(expected.values()), len(league.teams), len(expected), ctx.settings)
    year = int(state.year)
    for team in league.teams:
        for p in sorted(team.players, key=lambda p: p.id):
            if p.contract is not None:
                continue
            years = rng.randint(1, 3)
            set_contract(p, salary_for(expected[p.id], rate, ctx.settings), years, year, "migrate")
    state.contract_rates.setdefault(str(year), rate)


def over_cap(team: Team, ctx: ContractContext) -> int:
    """上限(標準以上)か目安(ゆるい)を超えている額(超えていなければ 0)。なしは常に 0。"""
    cap = ctx.cap(team.id)
    if cap is None:
        return 0
    return max(0, team_salary(team) - cap)


def resolve_overrun(team: Team, proc: OffseasonProcedure, ctx: ContractContext, mins, min_batters) -> list[Player]:
    """予算超過の解消(AI と同じ方針):見込みの WAR あたりの年俸が高い選手から、超過が解消するまで自由契約(最低人数は守る)。"""
    out: list[Player] = []
    if not ctx.hard():
        return out
    while over_cap(team, ctx) > 0:
        candidates = [p for p in team.players if can_release(team.players, p, mins, min_batters)]
        if not candidates:
            break

        def cost(p: Player) -> float:
            exp = max(0.05, ctx.expected(p, team.id)[0])
            return int(p.contract["salary"]) / exp if p.contract else 0.0

        worst = max(candidates, key=lambda p: (cost(p), int(p.contract["salary"]) if p.contract else 0, p.id))
        salary = int(worst.contract["salary"]) if worst.contract else 0
        release_players(team, [worst], proc)
        proc.released[-1]["note"] = "budget"
        proc.budget_releases.append({"team_id": team.id, "player_id": worst.id, "name": worst.name, "role": worst.role, "position": worst.position, "age": worst.age, "salary": salary})
        out.append(worst)
    return out


def can_afford(team: Team, salary: int, ctx: ContractContext | None, total: int | None = None) -> bool:
    """標準以上で、この年俸の契約を結んでも上限を超えないか(なし・ゆるいは常に可)。total は今の総年俸(省略時は数える)。"""
    if ctx is None or not ctx.hard():
        return True
    cap = ctx.cap(team.id)
    if cap is None:
        return True
    return (team_salary(team) if total is None else total) + int(salary) <= cap


def offer_for(team: Team, player: Player, proc: OffseasonProcedure, ctx: ContractContext | None) -> tuple[int, int, float] | None:
    """指名(獲得)したときの契約 (年俸, 年数, 見込み)。ドラフトは巡ごとの表、市場は算定。契約の文脈がなければ None。"""
    if ctx is None:
        return None
    if proc.phase == "draft":
        return ctx.settings.rookie_salary(proc.round), ctx.settings.rookie_years, 0.0
    return ctx.salary(player, team.id)


# ---- 手続きを進める ----

def _entry_report(proc: OffseasonProcedure, player: Player, team_id: str, sd: dict, settings: DraftSettings) -> dict:
    d = scout_report(proc, player, team_id, sd, settings).to_dict()
    d["year"] = None if proc.prerun else proc.year + 1  # 事前運転の入団は年を持たない(ゲーム開始前。振り返りには出ない。D-216)
    return d


def entry_summary(scouting: dict | None) -> dict:
    """入団時の評価の要約(履歴に残す公開用の値:総合の推定値・ふれ幅・天井・方式の版。D-216)。"""
    if not scouting:
        return {}
    r = ScoutReport.from_dict(scouting)
    pub = r.to_public()
    return {"overall": pub["overall"], "margin": pub["margin"], "ceiling": pub["ceiling"], "method": r.method}


def join(team: Team, player: Player, proc: OffseasonProcedure, sd: dict, settings: DraftSettings) -> None:
    player.team_id = team.id
    player.scouting = _entry_report(proc, player, team.id, sd, settings)
    player.state.fatigue = 0.0
    team.players.append(player)


def release_players(team: Team, players: list[Player], proc: OffseasonProcedure) -> None:
    ids = {p.id for p in players}
    team.players = [p for p in team.players if p.id not in ids]
    for p in players:
        p.team_id = None
        salary = int(p.contract["salary"]) if p.contract else None
        p.contract = None  # 残りの契約は消える(違約金なし。D-236)
        proc.market.append(p)
        proc.released.append({"team_id": team.id, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "salary": salary})


def apply_ai_releases(league: League, proc: OffseasonProcedure, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, mins, min_batters, policy=ai_release) -> None:
    if proc.ai_release_done:
        return
    for team in league.teams:
        if team.id == my_team_id:
            continue
        release_players(team, policy(team, proc, scout_sd[team.id], settings, mins, min_batters), proc)
    proc.ai_release_done = True


def pool_of(proc: OffseasonProcedure) -> list[Player]:
    return proc.candidates if proc.phase == "draft" else proc.market


def take(proc: OffseasonProcedure, team: Team, player: Player, scout_sd: dict[str, dict], settings: DraftSettings, ctx: ContractContext | None = None) -> None:
    """今の球団が選手を指名(獲得)して契約し、次の順番へ。ドラフトは巡ごとの年俸、市場は算定した年俸(D-235)。"""
    pool = pool_of(proc)
    offer = offer_for(team, player, proc, ctx)
    pool.remove(player)
    join(team, player, proc, scout_sd[team.id], settings)
    salary = None
    if offer is not None:
        salary, years, _ = offer
        set_contract(player, salary, years, proc.year + 1, proc.phase)
    proc.picks.append({"phase": proc.phase, "round": proc.round, "team_id": team.id, "player_id": player.id, "name": player.name, "role": player.role, "position": player.position, "age": player.age, "salary": salary, **entry_summary(player.scouting)})
    advance_turn(proc)


def pass_turn(proc: OffseasonProcedure, team_id: str, reason: str = "pass") -> None:
    proc.picks.append({"phase": proc.phase, "round": proc.round, "team_id": team_id, "player_id": None, "name": "", "role": "", "position": "", "age": None, "note": reason})
    advance_turn(proc)


def advance_turn(proc: OffseasonProcedure) -> None:
    proc.index += 1
    if proc.index >= len(proc.order):
        proc.index = 0
        proc.round += 1


def phase_finished(proc: OffseasonProcedure) -> bool:
    return proc.round > proc.total_rounds() or not pool_of(proc)


def next_phase(proc: OffseasonProcedure) -> None:
    i = PHASES.index(proc.phase)
    proc.phase = PHASES[min(i + 1, len(PHASES) - 1)]
    proc.round = 1
    proc.index = 0
    if proc.phase == "market":
        proc.market.extend(proc.candidates)  # 指名されなかった候補は市場へ(D-204)
        proc.candidates = []


def affordable_pool(team: Team, proc: OffseasonProcedure, ctx: ContractContext | None) -> list[Player]:
    """標準以上で、上限の中で結べる選手だけ(ドラフトは巡ごとの年俸、市場は算定)。なし・ゆるいは全員。"""
    pool = pool_of(proc)
    if ctx is None or not ctx.hard():
        return pool
    total = team_salary(team)
    if proc.phase == "draft":
        return pool if can_afford(team, ctx.settings.rookie_salary(proc.round), ctx, total) else []
    return [p for p in pool if can_afford(team, ctx.salary(p, team.id)[0], ctx, total)]


def run_ai_turns(league: League, proc: OffseasonProcedure, scout_sd: dict[str, dict], settings: DraftSettings, my_team_id: str | None, mins, min_batters, policy=ai_choose, ctx: ContractContext | None = None) -> bool:
    """AI の番を進める。自分の番が来たら True で止まる。段階の終わり(全巡終了か候補切れ)なら False。
    標準以上では、予算が足りない球団はパスする(note="budget")。"""
    teams = {t.id: t for t in league.teams}
    while not phase_finished(proc):
        tid = proc.current_team()
        if tid is None:
            advance_turn(proc)
            continue
        if tid == my_team_id:
            if len(teams[tid].players) >= MAX_ROSTER:
                pass_turn(proc, tid, "full")  # 空き枠がなければ自動でパス(D-203)
                continue
            if proc.phase == "draft" and not affordable_pool(teams[tid], proc, ctx):
                pass_turn(proc, tid, "budget")  # 予算が足りなければ自動でパス(D-235)
                continue
            return True
        team = teams[tid]
        if len(team.players) >= MAX_ROSTER:
            pass_turn(proc, tid, "full")
            continue
        pool = affordable_pool(team, proc, ctx)
        if not pool:
            pass_turn(proc, tid, "budget" if pool_of(proc) else "skip")
            continue
        choice = policy(team, pool, proc, scout_sd[tid], settings, mins, min_batters, proc.phase)
        if choice is None:
            pass_turn(proc, tid, "skip")
        else:
            take(proc, team, choice, scout_sd, settings, ctx)
    return False


def make_room(team: Team, count: int, proc: OffseasonProcedure, sd: dict, mins: dict[str, int], min_batters: int) -> list[Player]:
    """最低人数を満たす補充のために、空き枠が足りない分だけ、自球団の評価が低い順に選手を外す(不足を増やさない選手だけ)。
    外した選手はそのオフの終わりにリーグを去る(履歴には自由契約として残る)。"""
    out: list[Player] = []
    for p in sorted(team.players, key=lambda p: (cached_value(proc, p, team.id, sd)[0], p.id)):
        if len(out) >= count:
            break
        if can_release(team.players, p, mins, min_batters):
            release_players(team, [p], proc)
            proc.released[-1]["note"] = "room"
            out.append(p)
    return out


def finalize(league: League, proc: OffseasonProcedure, config: GenerationConfig, parts: NameParts, settings: DraftSettings, scout_sd: dict[str, dict], calibration: dict[str, float] | None, mins, min_batters, id_prefix: str = "Y", ctx: ContractContext | None = None) -> list[PlayerNote]:
    """完了:70 人に満たない球団を自動補充(最低人数を満たすポジションから、次に人数の目安との差が大きいポジション)。市場の残りはリーグを去る。"""
    rng = random.Random(derive_seed(proc.seed, "fill"))
    names = NameGenerator(parts, rng, {(p.family_name, p.given_name) for p in league.all_players()})
    counter = [0]
    target = {pos: int(n) for pos, n in config["roster"]["pitchers"].items()}
    target.update({pos: int(n) for pos, n in config["roster"]["fielders"].items()})
    added: list[PlayerNote] = []
    for team in league.teams:
        slots: list[tuple[str, str]] = []
        current = position_counts(team.players)
        for pos, n in shortages(team.players, mins, min_batters).items():
            if pos == "batter":  # 野手の合計が足りないときは、人数の目安との差が大きい野手のポジションで埋める
                for _ in range(n):
                    fill_pos = max(FIELDER_POSITIONS, key=lambda k: (target[k] - current.get(k, 0), -list(target).index(k)))
                    slots.append((BATTER, fill_pos))
                    current[fill_pos] = current.get(fill_pos, 0) + 1
                continue
            slots += [(PITCHER if pos in PITCHER_POSITIONS else BATTER, pos)] * n
            current[pos] += n
        excess = len(team.players) + len(slots) - MAX_ROSTER
        if excess > 0:  # 70 人のまま最低人数が足りない球団は、評価の低い選手を外して枠を空ける(上限 70 人と最低人数の両方を守る。D-203)
            make_room(team, excess, proc, scout_sd[team.id], mins, min_batters)
            current = position_counts(team.players)
            for _, pos in slots:
                current[pos] = current.get(pos, 0) + 1
        while len(team.players) + len(slots) < MAX_ROSTER:
            pos = max(target, key=lambda k: (target[k] - current.get(k, 0), -list(target).index(k)))
            slots.append((PITCHER if pos in PITCHER_POSITIONS else BATTER, pos))
            current[pos] = current.get(pos, 0) + 1
        for p in replenish(team, slots, config, names, rng, proc.year, counter, calibration, id_prefix):
            p.scouting = _entry_report(proc, p, team.id, scout_sd[team.id], settings)
            salary = None
            if ctx is not None:  # 自動補充は最低年俸(予算に関わらず結べる。D-235)
                salary = ctx.settings.minimum
                set_contract(p, salary, ctx.settings.default_years, proc.year + 1, "fill")
                if ctx.negotiation is not None:
                    ensure_preferences([p], ctx.league_seed, ctx.negotiation)
            added.append(PlayerNote(p.id, p.name, team.id, p.role, p.position, p.age, p.origin))
            proc.filled.append({"phase": "fill", "round": 0, "team_id": team.id, "player_id": p.id, "name": p.name, "role": p.role, "position": p.position, "age": p.age, "salary": salary, **entry_summary(p.scouting)})
    proc.market = []
    proc.candidates = []
    proc.phase = "done"
    return added


def run_ai_offseason(league: League, seed: int, config: GenerationConfig, parts: NameParts, offseason_settings: OffseasonSettings, settings: DraftSettings, year: int, calibration: dict[str, float] | None, scout_sd: dict[str, dict], records_or_order, id_prefix: str = "D", game_config=None, ctx: ContractContext | None = None) -> tuple[OffseasonProcedure, list[PlayerNote]]:
    """観戦のみ(全球団 AI)の手続きを一気に進める(事前運転・指紋 (n)・観戦のみの年度の確定で使う)。
    records_or_order は 球団 → (勝, 敗) か、1 巡目の順番の一覧。戻り値は手続きと、入団した全選手。"""
    mins = minimum_positions(game_config)
    min_batters = minimum_batters(game_config)
    if isinstance(records_or_order, list):
        proc = start_procedure(league, seed, config, parts, settings, year, calibration, None, id_prefix)
        proc.order = list(records_or_order)
    else:
        proc = start_procedure(league, seed, config, parts, settings, year, calibration, records_or_order, id_prefix)
    proc.prerun = id_prefix == "B"
    if ctx is not None:
        apply_contracts_start(league, proc, ctx, None, mins, min_batters)
    filled = complete(league, proc, config, parts, settings, scout_sd, calibration, None, mins, min_batters, "Y" if id_prefix == "D" else id_prefix, ctx)
    return proc, joined_players(proc) + filled


def joined_players(proc: OffseasonProcedure) -> list[PlayerNote]:
    return [PlayerNote(x["player_id"], x["name"], x["team_id"], x["role"], x["position"], x["age"]) for x in proc.picks if x["player_id"]]


def complete(league: League, proc: OffseasonProcedure, config: GenerationConfig, parts: NameParts, settings: DraftSettings, scout_sd: dict[str, dict], calibration, my_team_id: str | None, mins, min_batters, fill_prefix: str = "Y", ctx: ContractContext | None = None) -> list[PlayerNote]:
    """残りの手続きを AI の方針で最後まで進める(「おまかせ」。自分の球団も AI と同じ方針。予算超過の解消も AI と同じ)。戻り値は自動補充で入った選手。"""
    while proc.phase != "done":
        if proc.phase == "renewal":
            if my_team_id is not None and ctx is not None:  # 「おまかせ」:残りの交渉を AI と同じ方針で(D-251)
                team = next(t for t in league.teams if t.id == my_team_id)
                for e in open_entries(proc, my_team_id):
                    ai_negotiate_entry(team, e, proc, ctx)
            next_phase(proc)
        elif proc.phase == "release":
            if my_team_id is not None and ctx is not None:
                resolve_overrun(next(t for t in league.teams if t.id == my_team_id), proc, ctx, mins, min_batters)
            apply_ai_releases(league, proc, scout_sd, settings, my_team_id, mins, min_batters)
            if my_team_id is not None and not proc.my_release_done:
                team = next(t for t in league.teams if t.id == my_team_id)
                release_players(team, ai_release(team, proc, scout_sd[team.id], settings, mins, min_batters), proc)
                proc.my_release_done = True
            next_phase(proc)
        elif proc.phase in ("draft", "market"):
            run_ai_turns(league, proc, scout_sd, settings, None, mins, min_batters, ctx=ctx)
            next_phase(proc)
        else:
            break
    return finalize(league, proc, config, parts, settings, scout_sd, calibration, mins, min_batters, fill_prefix, ctx)
