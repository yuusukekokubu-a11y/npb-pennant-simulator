"""ブラウザでの実行の技術検証(D-082)で使う、計算本体の呼び出し役。

計算本体(src/pennant)は変更せず、そのまま呼ぶ。画面(index.html・worker.js)からは、
ここにある関数だけを呼ぶ。PC の Python でも動く(tests/test_web.py で確かめる)。

- 日程は、実装④の本物の日程(ラウンド制。1チーム125試合、全750試合)を使う(season.Season)。
- 実名のデータは扱わない。テスト用の球団名は、メモリ上の写しに一時的に付けるだけで、
  ログ・ファイル・測定結果には入れない(D-084)。
"""

from __future__ import annotations

import copy
import dataclasses
import gzip
import hashlib
import json
import random
import sys
import time

from pennant import generate_league, load_generation_config, load_name_parts
from pennant.fingerprint import fingerprints, format_fingerprints
from pennant.game import simulate_game
from pennant.game_stats import narrate
from pennant.season import Season

LOG_FORMAT_VERSION = 1


class Bench:
    """架空のリーグを1つ作り、本物の日程で1日ずつ試合を進める(疲労と回復も進める)。"""

    def __init__(self, seed: int = 1):
        t0 = time.perf_counter()
        self.seed = seed
        self.league = generate_league(seed, load_generation_config(), load_name_parts())
        self.season = Season(self.league, seed)
        self.config = self.season.game_config
        self.model = self.season.model
        self.manager = self.season.manager
        self.setup_seconds = time.perf_counter() - t0

    @property
    def results(self):
        return [p.result for p in self.season.played]

    @property
    def days(self) -> int:
        return self.season.day

    def total_games(self) -> int:
        return len(self.season.schedule)

    def is_over(self) -> bool:
        return self.season.is_over

    # ---- 試合を進める ----

    def play_day(self) -> int:
        """1日分(各リーグ3試合、計6試合)を行い、1日を進める。行った試合数を返す。"""
        return len(self.season.play_day().games)

    def play_games(self, n: int) -> float:
        """n 試合を日ごとに行い、かかった秒数を返す(日の途中では止めない)。"""
        t0 = time.perf_counter()
        target = len(self.results) + n
        while len(self.season.played) < target and not self.season.is_over:
            self.play_day()
        return time.perf_counter() - t0

    def games_played(self) -> int:
        return len(self.season.played)

    # ---- 大きさ ----

    def plate_appearances(self) -> int:
        return sum(len(r.log) for r in self.results)

    def log_memory_bytes(self) -> int:
        """全試合の打席ログが、Python のメモリ上で占める大きさの見積もり(共有している物は1回だけ数える)。"""
        return deep_size([r.log for r in self.results])

    def export_log(self, results=None) -> bytes:
        """打席ログを「1行に1件の JSON」にして gzip で圧縮したバイト列(ファイルの中身)。"""
        return encode_log(self.results if results is None else results)

    def export_first_game(self) -> bytes:
        """1試合目の打席ログだけのファイルの中身(ファイルの往復の確認用)。"""
        return encode_log(self.results[:1])

    # ---- 1試合の見出し(テスト用の球団名の確認) ----

    def sample_header(self, test_team_name: str) -> str:
        """テスト用の球団名を、メモリ上の写しの1球団に付けて、1試合の見出しだけを返す。

        名前はこの関数の中だけで使い、リーグ・ログ・ファイルには残さない。
        """
        name = (test_team_name or "").strip() or self.league.teams[0].name
        names = {t.id: t.name for t in self.league.teams}
        names[self.league.teams[0].id] = name
        league = copy.deepcopy(self.league)  # 試合を進めている本体の状態は変えない
        rng = random.Random(self.seed)
        home, away = league.teams[0], league.teams[1]
        hs, _ = self.manager.prepare(home, rng, 0)
        aw, _ = self.manager.prepare(away, rng, 0)
        result = simulate_game(hs, aw, rng, model=self.model, config=self.config, manager=self.manager)
        players = {p.id: p for p in league.all_players()}
        text = narrate(result, players, names)
        lines = text.splitlines()
        score = f"{names[result.away_team_id]} {result.away_runs} - {result.home_runs} {names[result.home_team_id]}"
        return "\n".join(lines[:1] + [f"(結果:{score})"])


# ---- 打席ログのファイル(書き出しと読み込み) ----

def _to_jsonable(x):
    if dataclasses.is_dataclass(x):
        return {f.name: _to_jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)}
    if isinstance(x, (list, tuple)):
        return [_to_jsonable(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _to_jsonable(v) for k, v in x.items()}
    return x


def encode_log(results) -> bytes:
    """打席ログを gzip 圧縮の JSON Lines にする。1行目は形式の情報、2行目以降が1打席ずつ。"""
    lines = [json.dumps({"format": "pennant-pa-log", "format_version": LOG_FORMAT_VERSION, "games": len(results)})]
    for game_index, r in enumerate(results):
        for pa in r.log:
            row = _to_jsonable(pa)
            row["game"] = game_index
            lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    return gzip.compress(raw, mtime=0)


def decode_log(data: bytes) -> dict:
    """encode_log で書いたファイルを読み、中身を確かめる。壊れていれば ValueError(日本語の理由つき)。"""
    try:
        raw = gzip.decompress(bytes(data))
    except (OSError, EOFError) as exc:
        raise ValueError(f"gzip として読めません({exc})") from exc
    try:
        lines = raw.decode("utf-8").splitlines()
        head = json.loads(lines[0])
        rows = [json.loads(line) for line in lines[1:] if line]
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise ValueError(f"打席ログとして読めません({exc})") from exc
    if head.get("format") != "pennant-pa-log":
        raise ValueError("打席ログのファイルではありません(1行目の format が違います)")
    if head.get("format_version") != LOG_FORMAT_VERSION:
        raise ValueError(f"対応していない形式のバージョンです(値: {head.get('format_version')!r})")
    return {
        "games": head.get("games"),
        "plate_appearances": len(rows),
        "runs": sum(r.get("runs", 0) for r in rows),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def log_digest(data: bytes) -> str:
    """圧縮前の中身の sha256(往復の一致確認用)。"""
    return hashlib.sha256(gzip.decompress(bytes(data))).hexdigest()


# ---- メモリの見積もり ----

def deep_size(obj) -> int:
    """オブジェクトとその中身の大きさ(バイト)の合計。同じ物は1回だけ数える。"""
    seen: set[int] = set()
    total = 0
    stack = [obj]
    while stack:
        x = stack.pop()
        if id(x) in seen:
            continue
        seen.add(id(x))
        total += sys.getsizeof(x)
        if isinstance(x, (str, bytes, int, float, bool)) or x is None:
            continue
        if isinstance(x, dict):
            stack.extend(x.keys())
            stack.extend(x.values())
        elif isinstance(x, (list, tuple, set, frozenset)):
            stack.extend(x)
        elif dataclasses.is_dataclass(x):
            stack.extend(getattr(x, f.name) for f in dataclasses.fields(x))
            if hasattr(x, "__dict__"):
                total += sys.getsizeof(x.__dict__)
    return total


def fingerprint_report() -> dict:
    """結果の指紋(D-089)。PC の `python scripts/fingerprint.py` と同じ関数・同じ文章。"""
    t0 = time.perf_counter()
    fp = fingerprints()
    return {"text": format_fingerprints(fp), "seconds": time.perf_counter() - t0}


def python_version() -> str:
    return sys.version.split()[0]
