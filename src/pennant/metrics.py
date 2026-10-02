"""指標の定義データ(data/metrics.json)の読み込み・検証と、指標の計算(実装⑤。D-097)。

計算式は定義データに書く(例:"(H + BB + HBP) / (AB + BB + HBP + SF)")。式は標準ライブラリの ast で読み、
足し算・引き算・掛け算・割り算・かっこ・整数・数の名前・ほかの指標の名前だけを許す。
計算は分数(Fraction)で行うので、小数の誤差が出ない。分母が 0 のときは「値なし」(None)。
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from .config import SUPPORTED_FORMAT_VERSION, ConfigError, _Checker, _read_json

ROLES = ("batter", "pitcher")
FORMATS = ("rate3", "percent1", "decimal2")
BETTER = ("high", "low")
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


def default_metrics_data() -> dict:
    data, _ = _read_json(None, "metrics.json")
    return copy.deepcopy(data)


def load_metrics_config(path: str | Path | None = None) -> MetricsConfig:
    data, source = _read_json(path, "metrics.json")
    return validate_metrics_config(data, source)


def _names(expr: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)}


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
    metrics = c.section(c.get(root, "metrics", ""), "metrics")
    seen: set[str] = set()
    for mid, m in (metrics or {}).items():
        path = f"metrics.{mid}"
        sec = c.section(m, path)
        if sec is None:
            continue
        for key in ("name", "description"):
            text = c.get(sec, key, path)
            if text is not None and (not isinstance(text, str) or not text.strip()):
                c.add(f"{path}.{key}", "文章を書いてください(空にはできません)")
        c.integer(c.get(sec, "stage", path), f"{path}.stage", 1, 3)
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
                elif name not in seen:
                    c.add(fpath, f"{name} は、{role} の元の数でも、前に定義した指標でもありません")
            if better.get(role) not in BETTER:
                c.add(f"{path}.better.{role}", f"high(高いほどよい)か low(低いほどよい)を書いてください")
        for role in better:
            if role not in formulas:
                c.add(f"{path}.better.{role}", "計算式のない役割です")
        for name in inputs or []:
            if not any(name in counts[r] for r in ROLES):
                c.add(f"{path}.inputs", f"{name} は、元の数にありません")
        seen.add(mid)
    if c.problems:
        raise ConfigError(source, c.problems)
    return MetricsConfig(data=copy.deepcopy(root), source=source)


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


def compute(config: MetricsConfig, role: str, counts: Mapping[str, int]) -> dict[str, Fraction | None]:
    """元の数から、その役割の指標をすべて計算する(値なしは None)。"""
    env: dict[str, Fraction | None] = {name: Fraction(counts.get(name, 0)) for name in config["counts"][role]}
    out: dict[str, Fraction | None] = {}
    for mid, m in config.metrics.items():
        expr = m["formulas"].get(role)
        if expr is None:
            continue
        value = _eval(ast.parse(expr, mode="eval"), env)
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
    return f"{float(value):.2f}"


def innings_text(outs: int) -> str:
    """投球回の表示(例:アウト 20 → 「6 2/3」)。"""
    whole, rest = divmod(outs, 3)
    return f"{whole}" if rest == 0 else f"{whole} {rest}/3"
