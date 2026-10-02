"""セーブデータの保存・読み込みの時間と大きさ、壊れたデータの検証の例を、表で出す開発者向けスクリプト。

使い方:
    python scripts/inspect_save.py              # 1日目・62日目・最後(125日目)で保存・読み込みを測る
    python scripts/inspect_save.py --days 3     # 3日目で1回だけ測る(短く確かめるとき)

出力は Markdown の表を含む文章。ファイルは作らない(メモリの上だけで行う)。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone

from pennant.savegame import SaveDataError, load_game, save_game, start_game

AT = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows])


def _rezip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _broken_examples(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}

    def edit(change):
        state = json.loads(files["state.json"])
        change(state)
        return _rezip({**files, "state.json": json.dumps(state, ensure_ascii=False).encode("utf-8")})

    def manifest_version(v):
        m = json.loads(files["manifest.json"])
        m["format_version"] = v
        return _rezip({**files, "manifest.json": json.dumps(m).encode("utf-8")})

    cases = [
        ("セーブデータではないファイル", b"this is not a save file"),
        ("状態のファイルが途中で切れている", _rezip({**files, "state.json": files["state.json"][:-20]})),
        ("能力値を 9999 に書き換えた", edit(lambda s: s["league"]["teams"][0]["players"][0]["ratings"].update(stamina=9999))),
        ("選手を50人消した", edit(lambda s: s["league"]["teams"][1]["players"].__delitem__(slice(0, 50)))),
        ("選手の ID を重複させた", edit(lambda s: s["league"]["teams"][0]["players"][1].update(id=s["league"]["teams"][0]["players"][0]["id"]))),
        ("日付を進めた(試合と合わない)", edit(lambda s: s["season"].update(day=s["season"]["day"] + 1))),
        ("新しすぎる版", manifest_version(99)),
    ]
    rows = []
    for label, bad in cases:
        try:
            load_game(bad)
            rows.append([label, "(読み込めてしまった)"])
        except SaveDataError as exc:
            rows.append([label, exc.problems[0].replace("|", "/")])
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="セーブデータの保存・読み込みを確かめる")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--days", type=int, nargs="*", default=[1, 62, 125], help="保存する日(複数可)")
    args = parser.parse_args(argv)

    state = start_game(args.seed)
    rows, last = [], None
    for day in sorted(args.days):
        state.season.play_days(day - state.season.day)
        t0 = time.perf_counter()
        data = save_game(state, AT)
        t1 = time.perf_counter()
        loaded = load_game(data)
        t2 = time.perf_counter()
        same = save_game(loaded, AT) == data
        rows.append([f"{day}日目", f"{len(state.season.played)}試合", f"{len(data) / 1024 / 1024:.2f} MB", f"{t1 - t0:.2f} 秒", f"{t2 - t1:.2f} 秒", "○" if same else "×"])
        last = data
    print("# セーブデータの確認(確認用)\n")
    print("## 保存と読み込み\n" + _table(["保存した日", "試合", "ファイルの大きさ", "保存の時間", "読み込みの時間", "読み込んで保存し直すと同じ中身"], rows))
    with zipfile.ZipFile(io.BytesIO(last)) as zf:
        layout = [[i.filename, f"{i.file_size / 1024:.0f} KB", f"{i.compress_size / 1024:.0f} KB"] for i in zf.infolist()]
    print("\n## 中身の構成(最後に保存したファイル)\n" + _table(["ZIP の中のファイル", "元の大きさ", "圧縮後"], layout))
    print("\n## 壊れたデータ・手で編集したデータの例(最初のエラー文)\n" + _table(["壊し方", "エラー文"], _broken_examples(last)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
