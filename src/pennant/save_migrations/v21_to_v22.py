"""版 21 → 版 22(D-385):球団ごとの設備の段階(D-383)を足した。
版 21 にはないので、何もしない(読み込みの最後に、球団の規模に応じた初めの段階:大 3・中 2・小 2 を入れる)。"""

from __future__ import annotations


def convert(bundle: dict) -> dict:
    return bundle
