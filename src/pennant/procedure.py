"""オフの手続きの土台(F3-1。①b で draft.py から分けた。D-302):段階・設定(data/draft.json)・人数の規則・手続きの状態(OffseasonProcedure)・
候補の生成と指名の順番・スカウト評価・AI の自由契約と選び方・入団と自由契約・順番の進め方。

手続きの全体の流れは draft.py の説明を見る。
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from .abilities import BATTER, FIELDER_POSITIONS, PITCHER, PITCHER_POSITIONS
from .config import ConfigError, GenerationConfig, NameParts, _Checker, _read_json
from .game_config import load_game_config
from .generate import make_rookie
from .models import League, Player, Team
from .names import NameGenerator
from .negotiation import standing_ranks
from .offseason import PlayerNote
from .scouting import GRADES, SCOUT_METHOD, ScoutReport, ceiling_cuts, quick_value, report
from .season import derive_seed


SUPPORTED_FORMAT_VERSION = 1


PHASES = ("renewal", "release", "fa", "draft", "market", "done")


PHASE_LABELS = {"renewal": "契約更改", "release": "自由契約", "fa": "FA", "draft": "ドラフト", "market": "自由契約市場", "done": "完了"}


# 画面の段階(D-271・D-272):契約更改と自由契約は「契約」の 1 つ
STAGES = ("contract", "fa", "draft", "market", "done")


STAGE_LABELS = {"contract": "契約", "fa": "FA", "draft": "ドラフト", "market": "市場", "done": "完了"}


def stage_of(phase: str) -> str:
    return "contract" if phase in ("renewal", "release") else phase


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
    return player.id in releasable(players, mins, min_batters)


def releasable(players: list[Player], mins: dict[str, int], min_batters: int) -> set[str]:
    """外しても不足が増えない選手の ID(new_shortages が空になる選手と同じ。1 人外すと、そのポジションの人数と野手の合計だけが 1 減るので、
    ポジションの人数が最低人数より多く、野手なら野手の合計が最低人数より多いとき)。"""
    counts = position_counts(players)
    batters = sum(1 for p in players if p.role == BATTER)
    return {p.id for p in players if counts[p.position] > mins.get(p.position, 0) and (p.role != BATTER or batters > min_batters)}


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
    fa_pool: list[Player] = field(default_factory=list)  # FA を宣言して、まだ決まっていない選手(F3-2c)
    fa_info: dict = field(default_factory=dict)  # 選手 ID → {former_team, calc_salary, expected, ai_years, status(open / signed / unsigned), team_id, ...}
    fa_round: int = 1  # 今の FA のラウンド(1 から)
    fa_offers: dict = field(default_factory=dict)  # あなたの球団の今のラウンドの提示(選手 ID → {years, salary})
    fa_results: list[dict] = field(default_factory=list)  # 成立した FA の契約
    fa_log: list[dict] = field(default_factory=list)  # 全球団の提示の記録({round, player_id, team_id, years, salary})
    fa_done: bool = False  # FA 市場が終わったか
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
            "fa_pool": [plain(p) for p in self.fa_pool], "fa_info": copy.deepcopy(self.fa_info), "fa_round": self.fa_round, "fa_offers": copy.deepcopy(self.fa_offers),
            "fa_results": [dict(x) for x in self.fa_results], "fa_log": [dict(x) for x in self.fa_log], "fa_done": self.fa_done,
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
        proc.fa_pool = [player_from(x) for x in d.get("fa_pool", [])]
        proc.fa_info = copy.deepcopy(d.get("fa_info", {}))
        proc.fa_round = int(d.get("fa_round", 1))
        proc.fa_offers = copy.deepcopy(d.get("fa_offers", {}))
        proc.fa_results = [dict(x) for x in d.get("fa_results", [])]
        proc.fa_log = [dict(x) for x in d.get("fa_log", [])]
        proc.fa_done = bool(d.get("fa_done", "fa_pool" not in d))  # 版 11 以前の手続き:FA はない
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
    if proc.phase == "fa" and (proc.fa_done or not proc.fa_pool):  # 宣言した選手がいなければ FA は飛ばす
        proc.fa_done = True
        proc.phase = "draft"
    proc.round = 1
    proc.index = 0
    if proc.phase == "market":
        proc.market.extend(proc.candidates)  # 指名されなかった候補は市場へ(D-204)
        proc.candidates = []


def joined_players(proc: OffseasonProcedure) -> list[PlayerNote]:
    return [PlayerNote(x["player_id"], x["name"], x["team_id"], x["role"], x["position"], x["age"]) for x in proc.picks if x["player_id"]]
