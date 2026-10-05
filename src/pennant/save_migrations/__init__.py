"""古い版のセーブデータの変換(保守②で savegame.py から版ごとのファイルに分けた。D-292)。

版 n の内容(manifest と state の組)を、版 n+1 の形に直す関数を、版ごとに 1 つのファイルに置く。
読み込み(savegame.load_game)は、版を 1 つずつ上げる。保存形式を新しくするときは、
vNN_to_vMM.py を足して、下の MIGRATIONS に加える。
"""

from __future__ import annotations

from typing import Callable

from .v01_to_v02 import v1_to_v2
from .v02_to_v03 import v2_to_v3
from .v03_to_v04 import v3_to_v4
from .v04_to_v05 import v4_to_v5
from .v05_to_v06 import v5_to_v6
from .v06_to_v07 import v6_to_v7
from .v07_to_v08 import v7_to_v8
from .v08_to_v09 import v8_to_v9
from .v09_to_v10 import v9_to_v10
from .v10_to_v11 import v10_to_v11
from .v11_to_v12 import v11_to_v12
from .v12_to_v13 import v12_to_v13

MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: v1_to_v2, 2: v2_to_v3, 3: v3_to_v4, 4: v4_to_v5, 5: v5_to_v6, 6: v6_to_v7, 7: v7_to_v8, 8: v8_to_v9, 9: v9_to_v10, 10: v10_to_v11, 11: v11_to_v12, 12: v12_to_v13}
