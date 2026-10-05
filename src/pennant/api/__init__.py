"""画面から呼ぶ「操作の関数」(最小のブラウザ画面。D-080、D-107、D-108)。

計算本体の側にあり、画面からは独立している。戻り値は、画面がそのまま使える JSON にできる形
(辞書・リスト・文字列・数・真偽値・None)。bytes はセーブデータの中身だけ。

公開用の関数だけを置く:選手の能力値・能力の見積もり・隠し情報は返さない(D-108)。
答え合わせ用の関数は、別のモジュール(answers.py)に置く(D-114)。

成績の画面(②。D-114):個人成績・選手・試合・チームの詳細。集計(試合ごとの元の数)は、
進めた試合の分だけ足していき、日が進むまで使い回す(_StatsCache)。

ファイルの分け方(保守②。D-291):
    common.py     共通の部品(表示用の名前・指標の列・成績の集計の使い回し・シーズンのまとまり)
    base.py       ゲームの土台(状態・集計・基準値・球場補正・WAR・シーズンの選び方)
    progress.py   新規開始・開く・保存・進める・状態・年度の確定(preview_teams・check_team_names も)
    stats.py      成績の表(個人成績・WAR・成績つきの選手の表)
    teams.py      順位表・チーム・球場
    games.py      試合
    players.py    選手のページ
    contract.py   契約(予算・契約更改・契約の画面・自由契約)
    fa.py         FA
    draft.py      ドラフト(指名の番)・ドラフトの振り返り・入退団の記録
    decisions.py  判断の画面の表(FA・ドラフト・市場)・加入後の序列・手薄なポジション・自球団の状況(①b)
    market.py     自由契約市場の提示(FA と同じ提示の方式。①b)
    offseason.py  オフの手続きの流れ(段階・次の手続きへ・おまかせ・オフの結果)
Game は、これらの部品(Mixin:機能ごとに分けたクラスの部品)を合わせた 1 つのクラス。画面から呼ぶ名前は分ける前と同じ。
新しい関数は、分野の合うファイルに足す(どこにも合わないときは、ファイルを足して、ここの一覧に書く)。
"""

from __future__ import annotations

# 分ける前の api.py と同じ名前で使えるようにする(scripts・tests・answers.py などが参照する)
import copy
from dataclasses import dataclass
import math
from collections import Counter
from fractions import Fraction
from datetime import date, datetime
from typing import Mapping, Sequence
from ..abilities import POSITION_LABELS
from ..baselines import Baselines, RunTally, blend, compute_baselines, load_baseline_settings, tally_game, trial_baselines
from ..config import load_generation_config, load_name_parts
from ..decisions import Decisions, decide
from ..game_stats import game_story
from ..metrics import baseline_dependent, MetricsConfig, compute, format_value, formula_text, innings_text, load_metrics_config
from ..parkfactors import FACTOR_KEYS, FACTOR_LABELS, ParkEstimate, ParkTally, add_game, estimate_parks, load_park_settings, player_park_factor, raw_ratio, season_tallies
from ..war import WarLine, load_war_settings, war_for_results, war_totals
from ..history import ArchivedStanding, SeasonArchive
from ..offseason import OffseasonResult, age_update_retire, load_offseason_settings
from .. import draft as draftmod
from ..draft import PHASE_LABELS, STAGE_LABELS, STAGES, load_draft_settings, stage_of
from ..scouting import ScoutReport
from ..contracts import MONEY_RULES, RULE_LABELS, RULE_NOTES, TIER_LABELS, assign_tiers, budget_of, cap_of, is_hard, load_contract_settings, remaining_years, team_salary
from .. import fa as famod
from ..negotiation import load_negotiation_settings
from ..season import derive_seed
from ..records import (
    Records,
    game_records,
    qualified_batters,
    qualified_pitchers,
)
from ..newgame import check_team_name, new_league, public_player, resolve_team_names, TeamNameError
from ..savegame import GameState, SaveDataError, default_file_name, load_game, save_game
from ..season import Season

from .common import (
    _pct_text,
    _gb_text,
    ROLE_LABELS,
    ORIGIN_LABELS,
    KIND_LABELS,
    BATS_LABELS,
    THROWS_LABELS,
    QUALIFY_RULES,
    _metrics_config,
    metrics_config,
    column_info,
    BASELINE_MODES,
    SOURCE_LABELS,
    _BASELINE_METRICS,
    WAR_KIND,
    WAR_COLUMNS,
    _WAR_SORT_KEYS,
    CONTRACT_STATUS_LABELS,
    CONTRACT_GROUPS,
    CONTRACT_COLUMNS,
    CONTRACT_REASONS,
    ROSTER_BASE_COLUMNS,
    _war_text,
    _war_values,
    table_cols,
    sortable_keys,
    is_sortable,
    glossary_view,
    raw_rate,
    _values,
    _StatsCache,
    _sum,
    _SeasonView,
    _add_war,
)
from .base import GameBase
from .contract import ContractMixin
from .decisions import DecisionMixin
from .draft import DraftMixin
from .fa import FaMixin
from .games import GamesMixin
from .market import MarketMixin
from .offseason import OffseasonMixin
from .players import PlayersMixin
from .progress import ProgressMixin, check_team_names, preview_teams
from .stats import StatsMixin
from .teams import TeamsMixin


class Game(ProgressMixin, StatsMixin, TeamsMixin, GamesMixin, PlayersMixin, ContractMixin, FaMixin, DraftMixin, MarketMixin, DecisionMixin, OffseasonMixin, GameBase):
    """遊んでいる1つのゲーム(画面は、これを1つ持って操作する)。"""


__all__ = ["Game", "SaveDataError", "TeamNameError", "check_team_names", "preview_teams"]
