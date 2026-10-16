"""画面から呼ぶ操作の関数のうち、自球団のスタメンと打順(D-373)。

- 打順の型(二番強打者・二番つなぎ役・機動力重視・長打重視)を選ぶと、AI がその型でスタメンと打順を組む。
- 自分で組んだスタメンと打順は、次の見直し(Season.review_days ごと)まで毎試合使う。見直しの日と、一軍にいない選手が入ったときは AI が組み直す。
- 主力の休養(一定の確率で控えが入る)は、どちらにも働く。ここで見せるのは休養の前の顔ぶれ。
"""

from __future__ import annotations

from .. import ability_model
from ..abilities import POSITION_LABELS
from ..manager import DEFAULT_LINEUP_STYLE, DH, LINEUP_POSITIONS, LINEUP_STYLES
from ..skills import public_skills
from .common import BATS_LABELS

POSITION_NAMES = {**POSITION_LABELS, DH: "指名打者"}
STEAL_LABELS = {"aggressive": "積極的", "normal": "普通", "cautious": "慎重"}  # 盗塁の作戦(D-429)
NOTICE_TEXT = {"review": "{day}日目の見直しで、AI が型({style})で組み直しました。", "roster": "一軍にいない選手がいたので、{day}日目から AI が型({style})で組み直しました。"}
SHOWN_ABILITIES = ("meet", "power", "speed", "defense")


class LineupMixin:
    """Game の一部:自球団のスタメンと打順(D-373)。"""

    def _lineup_check(self):
        if self.state.my_team_id is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        if self.state.procedure is not None or self.state.season.is_over:
            raise ValueError("シーズン中だけ、スタメンと打順を決められます")
        return self.state.season

    def _plan(self) -> dict:
        season = self.state.season
        return season.lineup_plans.setdefault(self.state.my_team_id, {"style": DEFAULT_LINEUP_STYLE, "slots": None})

    def lineup_view(self) -> dict:
        """スタメンと打順の画面(公開用)。slots は今の顔ぶれ(自分で組んだもの、なければ型で AI が組んだもの。休養の前)。"""
        state = self.state
        my = state.my_team_id
        if my is None:
            raise ValueError("操作する球団がありません(観戦のみ)")
        season = state.season
        cfg = state.gen_config
        active = season.actives[my]
        plan = season.lineup_plans.get(my) or {"style": DEFAULT_LINEUP_STYLE, "slots": None}
        manager = season.manager
        ordered = manager.planned_order(active, plan)
        user = manager.custom_order(active, plan.get("slots")) is not None
        by_id = {p.id: p for p in active.batters}

        def brief(p) -> dict:
            shown = {a["key"]: a["text"] for a in ability_model.public_abilities(cfg, p)}
            ov = ability_model.display(cfg, ability_model.overall(p))
            return {"player_id": p.id, "name": p.name, "position": p.position, "position_label": POSITION_LABELS[p.position], "hand": BATS_LABELS.get(p.bats),
                    "overall": f"{ability_model.rank(cfg, ov)} {ov}", "abilities": {k: shown.get(k, "") for k in ("trajectory",) + SHOWN_ABILITIES},
                    "skills": public_skills(p), "condition": self.player_condition(p.id)}

        slots = []
        for order, (p, pos) in enumerate(ordered, start=1):
            base = by_id[p.id]
            penalty = manager.position_penalty(base, pos)
            info = brief(base)
            info["native_position"], info["native_label"] = info.pop("position"), info.pop("position_label")
            slots.append({"order": order, **info, "position": pos, "position_label": POSITION_NAMES[pos], "penalty": penalty,
                          "warning": "" if not penalty else f"本来は{POSITION_LABELS[base.position]}(守備の能力 −{penalty:g})"})
        in_lineup = {s["player_id"] for s in slots}
        days = season.review_days()
        next_review = season.next_review(plan.get("set_day")) + 1  # 見直しは review_days ごとの日の試合の前(何日目の試合から AI に戻るか)
        notice = plan.get("notice") if not user else None
        style_label = LINEUP_STYLES[plan.get("style") or DEFAULT_LINEUP_STYLE]
        return {
            "team_id": my,
            "style": plan.get("style") or DEFAULT_LINEUP_STYLE,
            "styles": [{"key": k, "label": v} for k, v in LINEUP_STYLES.items()],
            "steal": plan.get("steal") or "normal",
            "steals": [{"key": k, "label": v} for k, v in STEAL_LABELS.items()],
            "source": "user" if user else "ai",
            "source_text": f"自分で組んだスタメンと打順({next_review}日目の見直しまで使います)" if user else f"AI が型({style_label})で組んだスタメンと打順",
            "notice": "" if not notice else NOTICE_TEXT[notice["reason"]].format(day=notice["day"], style=style_label),
            "slots": slots,
            "bench": [brief(p) for p in active.batters if p.id not in in_lineup],
            "positions": [{"key": k, "label": POSITION_NAMES[k]} for k in LINEUP_POSITIONS],
            "review_days": days,
            "next_review_day": next_review,
            "editable": self.state.procedure is None and not season.is_over,
        }

    def set_lineup_style(self, style: str) -> dict:
        """打順の型を選ぶ:AI がその型でスタメンと打順を組み直す(自分で組んだものは外す)。"""
        self._lineup_check()
        if style not in LINEUP_STYLES:
            raise ValueError(f"打順の型は {' / '.join(LINEUP_STYLES.values())} のどれかにしてください(値: {style!r})")
        plan = self._plan()
        plan.update({"style": style, "slots": None, "notice": None, "set_day": None})
        self.dirty = True
        return self.lineup_view()

    def set_steal(self, steal: str) -> dict:
        """盗塁の作戦(積極的・普通・慎重。D-429)を選ぶ。スタメンと打順はそのまま。"""
        self._lineup_check()
        if steal not in STEAL_LABELS:
            raise ValueError(f"盗塁の作戦は {' / '.join(STEAL_LABELS.values())} のどれかにしてください(値: {steal!r})")
        self._plan()["steal"] = steal
        self.dirty = True
        return self.lineup_view()

    def lineup_auto(self) -> dict:
        """今の型で AI に組み直させる。"""
        self._lineup_check()
        plan = self._plan()
        plan.update({"slots": None, "notice": None, "set_day": None})
        self.dirty = True
        return self.lineup_view()

    def set_lineup(self, slots: list) -> dict:
        """自分でスタメンと打順を決める。slots は打順の順に [{player_id, position}] × 9(守備位置 8 つと指名打者に 1 人ずつ)。"""
        season = self._lineup_check()
        active = season.actives[self.state.my_team_id]
        ids = {p.id for p in active.batters}
        if not isinstance(slots, list) or len(slots) != len(LINEUP_POSITIONS):
            raise ValueError(f"スタメンは {len(LINEUP_POSITIONS)} 人にしてください")
        out = []
        for i, s in enumerate(slots, start=1):
            pid = str((s or {}).get("player_id") or "")
            pos = str((s or {}).get("position") or "")
            if pos not in LINEUP_POSITIONS:
                raise ValueError(f"{i} 番の守備位置が正しくありません(値: {pos!r})")
            if pid not in ids:
                raise ValueError(f"{i} 番の選手が、一軍の野手ではありません")
            out.append([pid, pos])
        missing = [POSITION_NAMES[p] for p in LINEUP_POSITIONS if p not in {pos for _, pos in out}]
        if missing:
            raise ValueError("守備位置が足りません:" + "・".join(missing) + "(同じ守備位置に 2 人は入れられません)")
        if len({pid for pid, _ in out}) != len(out):
            raise ValueError("同じ選手が 2 つの打順に入っています")
        plan = self._plan()
        plan.update({"slots": out, "notice": None, "set_day": season.day})
        self.dirty = True
        return self.lineup_view()
