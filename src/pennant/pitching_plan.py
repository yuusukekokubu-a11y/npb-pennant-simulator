"""自球団の投手の起用の計画(先発ローテーションの順と救援の役割。D-449、D-450)。

計画は {starters: [先発の ID の順], roles: {救援の ID: 役割}, set_day, notice}。
arrange は、計画を一軍(ActiveRoster)に当てはめる計算だけの関数。一軍から抜けた投手の枠だけを組み直し、
自分で決めたほかの投手の起用はできるだけ変えない。
- 先発:残った先発は計画の順のまま。上がってきた先発は、抜けた番手から下の先発と総合を比べ、上回った最初の番手に入る。
- 救援:役割の上から空きを埋める。空きは、その役割より下の役割の投手と上がってきた投手の中で、総合が最も高い投手。
"""

from __future__ import annotations

from .manager import RELIEF_ROLES, ActiveRoster
from .stats import overall


def _fill_starters(plan_ids: list[str], starters: list) -> list:
    by_id = {p.id: p for p in starters}
    order = [by_id[pid] for pid in plan_ids if pid in by_id]
    vacant = [i for i, pid in enumerate(plan_ids) if pid not in by_id]
    newcomers = sorted((p for p in starters if p.id not in plan_ids), key=overall, reverse=True)  # 同じ総合なら AI の並び
    for n, p in enumerate(newcomers):
        j = min(vacant[n] if n < len(vacant) else len(order), len(order))
        while j < len(order) and overall(order[j]) >= overall(p):
            j += 1
        order.insert(j, p)
    return order


def _targets(plan_roles: dict, total: int) -> dict[str, int]:
    """役割ごとの人数(計画の人数。救援の人数が変わったときは中継ぎで合わせる。抑えは 1 人)。"""
    want = {r: sum(1 for v in plan_roles.values() if v == r) for r in RELIEF_ROLES}
    want["closer"] = 1
    diff = total - sum(want.values())
    if diff > 0:
        want["middle"] += diff
    for r in ("middle", "mopup", "setup", "closer"):
        if diff >= 0:
            break
        cut = min(want[r], -diff)
        want[r] -= cut
        diff += cut
    return want


def _fill_roles(plan_roles: dict, relievers: list) -> dict[str, str]:
    members = {r: [p for p in relievers if plan_roles.get(p.id) == r] for r in RELIEF_ROLES}
    newcomers = [p for p in relievers if p.id not in plan_roles]
    want = _targets(plan_roles, len(relievers))
    out: dict[str, str] = {}
    for i, role in enumerate(RELIEF_ROLES):
        mine = members[role]
        below = RELIEF_ROLES[i + 1 :]
        while len(mine) < want[role]:
            cands = newcomers + [p for r in below for p in members[r]]
            best = max(cands, key=overall)  # 同じ総合なら先に並んだ投手(上がってきた投手、上の役割の順)
            if best in newcomers:
                newcomers.remove(best)
            else:
                next(members[r] for r in below if best in members[r]).remove(best)
            mine.append(best)
        if len(mine) > want[role]:  # 人数を減らしたときだけ:総合の低い投手を 1 つ下の役割へ
            mine.sort(key=overall, reverse=True)
            extra = mine[want[role] :]
            del mine[want[role] :]
            (members[below[0]] if below else newcomers).extend(extra)
        for p in mine:
            out[p.id] = role
    return out


def arrange(plan: dict, active: ActiveRoster, day: int) -> ActiveRoster:
    """計画を一軍に当てはめた一軍を返す。計画は結果に書き直し、顔ぶれが変わったときは知らせ(notice)を残す。"""
    starters = _fill_starters(list(plan.get("starters") or []), active.starters)
    roles = _fill_roles(dict(plan.get("roles") or {}), active.relievers)
    before = set(plan.get("starters") or []) | set(plan.get("roles") or {})
    after = {p.id for p in active.starters + active.relievers}
    if before and before != after:
        n = plan.get("notice")
        if not (isinstance(n, dict) and n.get("day") == day):
            n = {"day": int(day), "out": [], "in": []}
        out_ids = [x for x in n["out"] if x not in after] + sorted(before - after - set(n["out"]))
        in_ids = [x for x in n["in"] if x in after] + sorted(after - before - set(n["in"]))
        plan["notice"] = {"day": int(day), "out": out_ids, "in": in_ids}
    plan["starters"] = [p.id for p in starters]
    plan["roles"] = roles
    return ActiveRoster(starters=starters, relievers=list(active.relievers), relief_roles=roles, batters=list(active.batters))
