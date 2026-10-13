"""古い版のセーブデータの変換(保守②で savegame.py から版ごとのファイルに分けた。D-292)。

版 n の内容(manifest と state の組)を、版 n+1 の形に直す関数を、版ごとに 1 つのファイルに置く。
読み込み(savegame.load_game)は、版を 1 つずつ上げる。保存形式を新しくするときは、
vNN_to_vMM.py を足して、下の MIGRATIONS に加える。

作り直し①(版 14)で能力の仕組みが変わったため、版 13 までの変換は消した(13 以前は読まない。D-344)。
"""

from __future__ import annotations

from typing import Callable

MIGRATIONS: dict[int, Callable[[dict], dict]] = {}
