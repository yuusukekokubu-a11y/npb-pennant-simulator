"""指標の定義データ(data/metrics.json)の読み込み・検証と、指標の計算(実装⑤。D-097)。

計算式は定義データに書く(例:"(H + BB + HBP) / (AB + BB + HBP + SF)")。式は標準ライブラリの ast で読み、
足し算・引き算・掛け算・割り算・かっこ・整数・数の名前・ほかの指標の名前だけを許す。
計算は分数(Fraction)で行うので、小数の誤差が出ない。分母が 0 のときは「値なし」(None)。
"""

from __future__ import annotations

import ast
import copy
import functools
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from .config import SUPPORTED_FORMAT_VERSION, ConfigError, _Checker, _read_json

ROLES = ("batter", "pitcher")
FORMATS = ("rate3", "percent1", "decimal2", "integer")
BETTER = ("high", "low")
TABLE_KINDS = ("basic", "saber", "game")  # 表の種類:基本・セイバー・試合ごと(D-119)
CATEGORIES = ("basic", "saber")  # 区分:基本/セイバー(D-109。成績の画面の切り替えで使う)
_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b}


@dataclass(frozen=True)
class MetricsConfig:
    data: dict
    source: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def metrics(self) -> dict[str, dict]:
        return self.data["metrics"]

    def for_role(self, role: str, stage: int | None = None) -> list[str]:
        """その役割(打者・投手)で表示する指標の ID(定義データの順)。"""
        return [k for k, m in self.metrics.items() if role in m["formulas"] and (stage is None or m["stage"] <= stage)]

    def in_category(self, category: str) -> list[str]:
        """その区分(basic:基本、saber:セイバー)の指標の ID(定義データの順)。"""
        return [k for k, m in self.metrics.items() if m["category"] == category]


def default_metrics_data() -> dict:
    data, _ = _read_json(None, "metrics.json")
    return copy.deepcopy(data)


def load_metrics_config(path: str | Path | None = None) -> MetricsConfig:
    data, source = _read_json(path, "metrics.json")
    return validate_metrics_config(data, source)


def _names(expr: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)}


def baseline_dependent(config: "MetricsConfig", role: str) -> set[str]:
    """基準値(リーグ平均・重み・球場補正 pf)に依存する指標(通算で加重平均するもの。D-192)。
    式に基準値の名前か pf を使う指標と、そのような指標を式に使う指標(推移的)。"""
    base_names = set(config.data.get("baseline_names", {})) | {"pf"} | {n for n in VALUE_NAMES_FOR_METRICS}
    out: set[str] = set()
    changed = True
    while changed:
        changed = False
        for mid, m in config.metrics.items():
            expr = m["formulas"].get(role)
            if expr is None or mid in out:
                continue
            names = _names(expr)
            if names & base_names or names & out:
                out.add(mid)
                changed = True
    return out


VALUE_NAMES_FOR_METRICS = ("w_bb", "w_hbp", "w_1b", "w_2b", "w_3b", "w_hr", "woba_scale", "lg_obp", "lg_slg", "lg_woba", "lg_r_pa", "lg_era", "fip_hr", "fip_bb", "fip_so", "fip_constant", "pf")


def _check_formula(expr: str) -> str | None:
    """式に使えない書き方があれば、その理由を返す。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return f"式として読めません({exc.msg})"
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.USub, ast.Name, ast.Load, ast.Constant, ast.Div) + tuple(_OPS)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            return f"使えない書き方があります({type(node).__name__})。使えるのは + − × ÷ とかっこ、整数、数の名前だけです"
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, int)):
            return "数は整数で書いてください"
    return None


def validate_metrics_config(data: Any, source: str = "(辞書)") -> MetricsConfig:
    c = _Checker()
    root = c.section(data, "(全体)")
    if root is None:
        raise ConfigError(source, c.problems)
    version = c.get(root, "format_version", "")
    if version is not None and version != SUPPORTED_FORMAT_VERSION:
        c.add("format_version", f"対応していない形式のバージョンです(値: {version!r}、対応: {SUPPORTED_FORMAT_VERSION})")
    counts_sec = c.section(c.get(root, "counts", ""), "counts")
    counts: dict[str, set[str]] = {}
    for role in ROLES:
        sec = c.section(c.get(counts_sec, role, "counts"), f"counts.{role}")
        counts[role] = set(sec or {})
    baseline_sec = c.section(c.get(root, "baseline_names", ""), "baseline_names") or {}
    baseline_names = set(baseline_sec)  # 指標の式で使える基準値の名前(第2弾。D-121〜D-125)
    for name, label in baseline_sec.items():
        if not isinstance(label, str) or not label.strip():
            c.add(f"baseline_names.{name}", "表示名を書いてください(空にはできません)")
    metrics = c.section(c.get(root, "metrics", ""), "metrics")
    seen: set[str] = set()
    for mid, m in (metrics or {}).items():
        path = f"metrics.{mid}"
        sec = c.section(m, path)
        if sec is None:
            continue
        text = c.get(sec, "name", path)  # 解説の文は用語集(glossary.json)に持つ(D-311)
        if text is not None and (not isinstance(text, str) or not text.strip()):
            c.add(f"{path}.name", "文章を書いてください(空にはできません)")
        c.integer(c.get(sec, "stage", path), f"{path}.stage", 1, 3)
        category = c.get(sec, "category", path)
        if category is not None and category not in CATEGORIES:
            c.add(f"{path}.category", "basic(基本)か saber(セイバー)を書いてください")
        fmt = c.get(sec, "format", path)
        if fmt is not None and fmt not in FORMATS:
            c.add(f"{path}.format", f"知らない表示の形です(使えるもの: {', '.join(FORMATS)})")
        inputs = c.get(sec, "inputs", path)
        if inputs is not None and (not isinstance(inputs, list) or not inputs):
            c.add(f"{path}.inputs", "元になる数の名前を、1つ以上のリストで書いてください")
            inputs = None
        formulas = c.section(c.get(sec, "formulas", path), f"{path}.formulas") or {}
        better = c.section(c.get(sec, "better", path), f"{path}.better") or {}
        if not formulas:
            c.add(f"{path}.formulas", "打者(batter)か投手(pitcher)の計算式を、1つ以上書いてください")
        for role, expr in formulas.items():
            fpath = f"{path}.formulas.{role}"
            if role not in ROLES:
                c.add(fpath, f"知らない役割です(使えるもの: {', '.join(ROLES)})")
                continue
            if not isinstance(expr, str):
                c.add(fpath, "計算式を文字で書いてください")
                continue
            problem = _check_formula(expr)
            if problem:
                c.add(fpath, problem)
                continue
            for name in sorted(_names(expr)):
                if name in counts[role]:
                    if inputs is not None and name not in inputs:
                        c.add(f"{path}.inputs", f"計算式で使う {name} が、元になる数(inputs)に書かれていません")
                elif name not in seen and name not in baseline_names:
                    c.add(fpath, f"{name} は、{role} の元の数でも、前に定義した指標でも、基準値の名前でもありません")
            if better.get(role) not in BETTER:
                c.add(f"{path}.better.{role}", f"high(高いほどよい)か low(低いほどよい)を書いてください")
        for role in better:
            if role not in formulas:
                c.add(f"{path}.better.{role}", "計算式のない役割です")
        for name in inputs or []:
            if not any(name in counts[r] for r in ROLES):
                c.add(f"{path}.inputs", f"{name} は、元の数にありません")
        seen.add(mid)
    _check_tables(c, root, counts, metrics or {})
    if c.problems:
        raise ConfigError(source, c.problems)
    return MetricsConfig(data=copy.deepcopy(root), source=source)


def _check_tables(c: _Checker, root: dict, counts: dict[str, set[str]], metrics: dict) -> None:
    """表の定義(D-119):列と並び順に使う名前が、その役割の元の数か指標であること。"""
    sec = c.section(c.get(root, "tables", ""), "tables")
    if sec is None:
        return
    for role in ROLES:
        rsec = c.section(c.get(sec, role, "tables"), f"tables.{role}")
        if rsec is None:
            continue
        usable = counts[role] | {mid for mid, m in metrics.items() if isinstance(m, dict) and role in (m.get("formulas") or {})}
        for kind in TABLE_KINDS:
            path = f"tables.{role}.{kind}"
            tsec = c.section(c.get(rsec, kind, f"tables.{role}"), path)
            if tsec is None:
                continue
            cols = c.get(tsec, "columns", path)
            if cols is not None and (not isinstance(cols, list) or not cols):
                c.add(f"{path}.columns", "列の名前を、1つ以上のリストで書いてください")
                cols = []
            for name in cols or []:
                if name not in usable:
                    c.add(f"{path}.columns", f"{name} は、{role} の元の数でも指標でもありません")
            if kind != "game":
                sort = c.get(tsec, "sort", path)
                if sort is not None and sort not in usable:
                    c.add(f"{path}.sort", f"{sort} は、{role} の元の数でも指標でもありません")


# ---- 計算 ----

def _eval(node: ast.AST, env: Mapping[str, Fraction | None]) -> Fraction | None:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return Fraction(node.value)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return None if v is None else -v
    a, b = _eval(node.left, env), _eval(node.right, env)
    if a is None or b is None:
        return None
    if isinstance(node.op, ast.Div):
        return None if b == 0 else a / b
    return _OPS[type(node.op)](a, b)


@functools.lru_cache(maxsize=None)
def _parsed(expr: str) -> ast.Expression:
    return ast.parse(expr, mode="eval")


def compute(
    config: MetricsConfig, role: str, counts: Mapping[str, int], baselines: Mapping[str, Fraction] | None = None
) -> dict[str, Fraction | None]:
    """元の数から、その役割の指標をすべて計算する(値なしは None)。

    baselines は基準値(名前 → 分数。第2弾の指標で使う)。渡さないと、基準値を使う指標は値なし。
    """
    env: dict[str, Fraction | None] = {name: (baselines or {}).get(name) for name in config.data.get("baseline_names", {})}
    env.update({name: Fraction(counts.get(name, 0)) for name in config["counts"][role]})
    out: dict[str, Fraction | None] = {}
    for mid, m in config.metrics.items():
        expr = m["formulas"].get(role)
        if expr is None:
            continue
        value = _eval(_parsed(expr), env)
        out[mid] = value
        env[mid] = value
    return out


def format_value(config: MetricsConfig, metric_id: str, value: Fraction | None) -> str:
    """表示の形にする。値なしは「-」。"""
    if value is None:
        return "-"
    fmt = config.metrics[metric_id]["format"]
    if fmt == "rate3":
        text = f"{float(value):.3f}"
        return text[1:] if text.startswith("0.") else text.replace("-0.", "-.")
    if fmt == "percent1":
        return f"{100 * float(value):.1f}%"
    if fmt == "integer":
        return str(math.floor(value + Fraction(1, 2)))  # 四捨五入(分数のまま)
    return f"{float(value):.2f}"


_PREC = {ast.Add: 1, ast.Sub: 1, ast.Mult: 2, ast.Div: 2}
_SYMBOL = {ast.Add: " + ", ast.Sub: " − ", ast.Mult: " × ", ast.Div: " ÷ "}


def formula_text(config: MetricsConfig, role: str, expr: str) -> str:
    """計算式を、日本語の名前と × ÷ で書いた文字にする(用語集の式)。"""
    counts = config["counts"][role]
    baselines = config.data.get("baseline_names", {})

    def label(name: str) -> str:
        if name in counts:  # 「投球回(アウトの数)」のような名前は、かっこの中(式で使っている数)を出す
            text = counts[name]
            return text.split("(")[1].rstrip(")") if "(" in text else text
        if name in config.metrics:
            return config.metrics[name]["name"]
        return baselines.get(name, name)

    def show(node, parent_prec=0, right=False) -> str:
        if isinstance(node, ast.Expression):
            return show(node.body)
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.Name):
            return label(node.id)
        if isinstance(node, ast.UnaryOp):
            return "−" + show(node.operand, 3)
        prec = _PREC[type(node.op)]
        text = show(node.left, prec) + _SYMBOL[type(node.op)] + show(node.right, prec, True)
        need = prec < parent_prec or (right and prec == parent_prec)
        return f"({text})" if need else text

    return show(_parsed(expr))


def innings_text(outs: int) -> str:
    """投球回の表示(例:アウト 20 → 「6 2/3」)。"""
    whole, rest = divmod(outs, 3)
    return f"{whole}" if rest == 0 else f"{whole} {rest}/3"
