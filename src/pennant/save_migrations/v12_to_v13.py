"""セーブデータの変換:版 12 → 版 13。"""

from __future__ import annotations


def v12_to_v13(bundle: dict) -> dict:
    """版12の市場は指名の順番の方式(ウェーバー)。版 13 は FA と同じ提示の方式(①b。D-300)。
    進行中の手続きには、市場の提示の状態(提示なし・結果なし・まだ締めていない)を足す。市場の段階の途中なら、
    それまでに獲得した選手はそのままで、残りの選手は提示の方式で続ける(順番と巡の値は使わない)。"""
    proc = bundle["state"].get("procedure")
    if isinstance(proc, dict):
        proc.setdefault("market_offers", {})
        proc.setdefault("market_results", [])
        proc.setdefault("market_log", [])
        proc.setdefault("market_done", False)
    return bundle
