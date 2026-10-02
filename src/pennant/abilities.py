"""能力項目・ポジションの定義(D-022、D-023、D-029)。

ここにあるのは「項目の名前」だけで、数値は設定ファイル(data/generation.json)に置く。
"""

# 打者の能力 10 項目(D-022)
BATTER_ITEMS = (
    "contact",
    "eye",
    "power",
    "batted_ball_quality",
    "gb_fb",
    "speed",
    "baserunning",
    "range",
    "arm",
    "fielding",
)

# 投手の能力 8 項目(D-023)
PITCHER_ITEMS = (
    "strikeout",
    "control",
    "stuff",
    "contact_suppression",
    "gb_fb",
    "stamina",
    "recovery",
    "holding",
)

# 強弱ではなく「型」を表す項目(D-022)。高いほどゴロ寄り(DESIGN.md 検討中の論点)
STYLE_ITEMS = ("gb_fb",)

ALL_ITEMS = tuple(dict.fromkeys(BATTER_ITEMS + PITCHER_ITEMS))

ITEM_LABELS = {
    "contact": "コンタクト",
    "eye": "選球眼",
    "power": "長打力",
    "batted_ball_quality": "打球の質",
    "gb_fb": "ゴロ/フライ傾向",
    "speed": "走力",
    "baserunning": "走塁判断",
    "range": "守備範囲",
    "arm": "肩",
    "fielding": "捕球",
    "strikeout": "奪三振力",
    "control": "制球力",
    "stuff": "球威",
    "contact_suppression": "打球抑制",
    "stamina": "スタミナ",
    "recovery": "回復力",
    "holding": "走者抑制",
}

PITCHER_POSITIONS = ("SP", "RP")
FIELDER_POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")

POSITION_LABELS = {
    "SP": "先発",
    "RP": "救援",
    "C": "捕手",
    "1B": "一塁手",
    "2B": "二塁手",
    "3B": "三塁手",
    "SS": "遊撃手",
    "LF": "左翼手",
    "CF": "中堅手",
    "RF": "右翼手",
}

PITCHER = "pitcher"
BATTER = "batter"


def items_for(role: str) -> tuple[str, ...]:
    """役割(投手/打者)ごとの能力項目。"""
    return PITCHER_ITEMS if role == PITCHER else BATTER_ITEMS


def strength_items_for(role: str) -> tuple[str, ...]:
    """強弱を表す項目(「型」の項目を除いたもの)。"""
    return tuple(i for i in items_for(role) if i not in STYLE_ITEMS)
