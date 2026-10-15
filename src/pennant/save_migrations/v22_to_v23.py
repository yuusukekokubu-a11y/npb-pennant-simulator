"""版 22 → 版 23(D-393):選手ごとの「FA 権を取ったことがある」印(自動 FA。D-390)を足した。
版 22 にはないので、何もしない(印はなしとして読む。FA 権を持つ選手は、自動 FA なら次のオフに FA になる)。"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
