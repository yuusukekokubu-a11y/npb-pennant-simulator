"""新規開始で選べる仕組みの選択肢(D-381)。始めた後は変えない(セーブデータに残る)。

- auto_fa:自動 FA(FA 権を取ったオフに、契約が残っていても必ず FA。その後は契約が満了するたびに FA。既定はなし。D-390)
- active_draft:現役ドラフト(D-380。既定はあり)
- trade:トレード(D-378。既定はあり)
- foreign_development:外国人・育成(D-375、D-376。既定はあり)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

RULE_KEYS = ("auto_fa", "active_draft", "trade", "foreign_development")

RULE_LABELS = {"auto_fa": "自動 FA", "active_draft": "現役ドラフト", "trade": "トレード", "foreign_development": "外国人・育成"}


@dataclass(frozen=True)
class GameRules:
    auto_fa: bool = False
    active_draft: bool = True
    trade: bool = True
    foreign_development: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def skipped_phases(self) -> tuple[str, ...]:
        """オフの手続きで飛ばす段階。"""
        out = []
        if not self.foreign_development:
            out += ["foreign", "dev_draft"]
        if not self.trade:
            out.append("trade")
        if not self.active_draft:
            out.append("active_draft")
        return tuple(out)


DEFAULT_RULES = GameRules()


def rules_from(data) -> GameRules:
    """辞書(画面・セーブデータ)から。None なら既定。知らない項目や真偽でない値は ValueError(理由つき)。"""
    if data is None:
        return DEFAULT_RULES
    if isinstance(data, GameRules):
        return data
    if not isinstance(data, dict):
        raise ValueError("仕組みの選択肢は、まとまり({ })にしてください")
    unknown = sorted(set(data) - set(RULE_KEYS))
    if unknown:
        raise ValueError(f"知らない仕組みの選択肢です({', '.join(unknown)})")
    values = DEFAULT_RULES.to_dict()
    for k, v in data.items():
        if not isinstance(v, bool):
            raise ValueError(f"仕組みの選択肢「{RULE_LABELS[k]}」は、あり(true)か、なし(false)にしてください(値: {v!r})")
        values[k] = v
    return GameRules(**values)
