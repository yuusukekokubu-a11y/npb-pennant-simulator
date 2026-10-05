"""ブラウザの画面(web/)を、公開できる形(_site/)にまとめる開発者向けスクリプト。

使い方:
    python scripts/build_web.py                      # _site/ を作る(GitHub Pages の公開でも同じものを使う)
    python scripts/build_web.py --pyodide DIR        # 手元の Pyodide を _site/pyodide/ に入れる(通信なしの試験用)

_site/ の中身:
    index.html・app.js・worker.js・bridge.py      遊ぶための画面(web/ のファイルをそのまま写す)
    js/                                          画面の処理(app.js が読み込む ES モジュール。web/js/ をそのまま写す。保守②)
    dev/                                         開発者向けの測定ページ(web/dev/ をそのまま写す。D-111)
    pennant.zip                                  計算本体(src/pennant の .py と data/*.json)。両方の画面が読み込んで使う
    sample-save.*・expected-fingerprints.json    測定ページの確認用(架空のデータだけ)
    build.json                               作った日時と、入れたファイルの一覧

計算本体は書き換えない。実名のデータ・セーブデータは入れない(架空のデータだけ)。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PACKAGE = ROOT / "src" / "pennant"
WEB_FILES = ("index.html", "app.js", "worker.js", "bridge.py")
WEB_MODULES = "js"  # app.js が読み込む画面の処理(ES モジュール:import と export でつなぐ JavaScript のファイル)
DEV_FILES = ("index.html", "app.js", "worker.js", "bench.py")
FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # zip の中の日時を固定する(同じ中身なら同じファイルになる)


def web_module_files() -> list[str]:
    """web/js/ の .js(公開でも同じ場所 js/ に置く)。"""
    return [f"{WEB_MODULES}/{f.name}" for f in sorted((WEB / WEB_MODULES).glob("*.js"))]


def package_files() -> list[Path]:
    """pennant.zip に入れるファイル(.py と data/*.json だけ。キャッシュなどは入れない)。
    計算本体の中のフォルダ(api/ など。保守②)の .py も入れる。"""
    files = sorted(f for f in PACKAGE.rglob("*.py") if "__pycache__" not in f.parts) + sorted((PACKAGE / "data").glob("*.json"))
    return files


def build_zip(dest: Path) -> list[str]:
    names = []
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in package_files():
            arcname = "pennant/" + f.relative_to(PACKAGE).as_posix()
            info = zipfile.ZipInfo(arcname, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, f.read_bytes())
            names.append(arcname)
    return names


def build(out: Path, pyodide_dir: Path | None = None) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in WEB_FILES:
        shutil.copy2(WEB / name, out / name)
    (out / WEB_MODULES).mkdir()
    for name in web_module_files():
        shutil.copy2(WEB / name, out / name)
    (out / "dev").mkdir()
    for name in DEV_FILES:
        shutil.copy2(WEB / "dev" / name, out / "dev" / name)
    names = build_zip(out / "pennant.zip")
    # 保存・読み込みの確認用(架空のデータだけ。D-103)
    shutil.copy2(ROOT / "tests" / "data" / "sample-save.sav", out / "sample-save.sav")
    shutil.copy2(ROOT / "tests" / "data" / "sample-save.json", out / "sample-save.json")
    shutil.copy2(ROOT / "tests" / "data" / "fingerprints.json", out / "expected-fingerprints.json")
    info = {"web_files": list(WEB_FILES) + web_module_files(), "dev_files": ["dev/" + n for n in DEV_FILES], "package_files": names, "local_pyodide": pyodide_dir is not None}
    if pyodide_dir is not None:
        shutil.copytree(pyodide_dir, out / "pyodide")
    (out / "build.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    # GitHub Pages の自動変換(Jekyll)を止める(先頭が _ のファイルなどを消されないように)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ブラウザの画面を _site/ にまとめる")
    parser.add_argument("--out", default=str(ROOT / "_site"), help="出力先のフォルダ(既定: _site)")
    parser.add_argument("--pyodide", help="手元の Pyodide のフォルダ(試験用。公開では使わない)")
    args = parser.parse_args(argv)
    pyodide_dir = Path(args.pyodide) if args.pyodide else None
    if pyodide_dir is not None and not (pyodide_dir / "pyodide.mjs").exists():
        print(f"Pyodide が見つかりません: {pyodide_dir}", file=sys.stderr)
        return 1
    info = build(Path(args.out), pyodide_dir)
    print(f"{args.out} を作りました(計算本体のファイル {len(info['package_files'])} 個)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
