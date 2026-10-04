"""新規リーグの作成・球団名の入力の検証・画面用の選手情報(実装⑥。D-008、D-104)。

  - 球団名は、空欄(何も入力しない)なら架空の初期名(地名 + 愛称)にする(D-008)。
  - 空白だけ・長すぎる・制御文字(画面に出ない特殊な文字)を含む・12球団の中で重複する名前は、理由を示して拒否する。
  - 入力された球団名は Team.display_name に入り、セーブデータの状態の中にだけ保存される(D-104)。
  - 画面用の選手情報(public_player)には、隠し情報(潜在能力・成長タイプ・生成時の型)と、
    真の能力値(D-017)を含めない。
"""

from __future__ import annotations

import unicodedata
from typing import Sequence

from .config import GenerationConfig, NameParts, load_generation_config, load_name_parts
from .generate import generate_league
from .models import League, Player

MAX_TEAM_NAME_LENGTH = 24  # 球団名の上限の文字数(実在の長い球団名でも収まる長さ)


class TeamNameError(ValueError):
    """球団名の入力に問題があるとき。problems は「何番目の球団: 理由」の一覧。"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("球団名に問題があります:\n" + "\n".join(f"  - {p}" for p in problems))


def _same_key(name: str) -> str:
    """重複の判定に使う形(全角・半角や大文字・小文字の違いをそろえる)。"""
    return unicodedata.normalize("NFKC", name).casefold().replace(" ", "")


def check_team_name(name: str | None) -> str | None:
    """1つの球団名の問題を返す(なければ None)。空欄(None・空文字)は問題なし(初期名になる)。"""
    if name is None or name == "":
        return None
    if not name.strip():
        return "空白だけの名前は使えません(空欄のままにすると、架空の初期名になります)"
    bad = [ch for ch in name if unicodedata.category(ch).startswith("C")]
    if bad:
        return "制御文字など、画面に表示できない文字が含まれています(" + "、".join(f"U+{ord(ch):04X}" for ch in bad[:3]) + ")"
    if len(name.strip()) > MAX_TEAM_NAME_LENGTH:
        return f"長すぎます({len(name.strip())}文字。{MAX_TEAM_NAME_LENGTH}文字までにしてください)"
    return None


def resolve_team_names(league: League, names: Sequence[str | None]) -> list[str | None]:
    """入力された球団名を検証し、球団ごとの display_name(空欄は None)を返す。問題があれば TeamNameError。"""
    teams = league.teams
    if len(names) != len(teams):
        raise TeamNameError([f"球団名は {len(teams)} 個(全球団の分)を渡してください(渡された数: {len(names)})"])
    problems = []
    resolved: list[str | None] = []
    for i, (team, name) in enumerate(zip(teams, names)):
        problem = check_team_name(name)
        if problem:
            problems.append(f"{i + 1}番目の球団: {problem}")
        resolved.append(name.strip() if name and name.strip() else None)
    finals = [r or t.default_name for r, t in zip(resolved, teams)]
    seen: dict[str, int] = {}
    for i, final in enumerate(finals):
        key = _same_key(final)
        if key in seen:
            problems.append(f"{i + 1}番目の球団: 「{final}」は {seen[key] + 1}番目の球団と同じ名前です(12球団の中で重ならないようにしてください)")
        else:
            seen[key] = i
    if problems:
        raise TeamNameError(problems)
    return resolved


def new_league(
    seed: int,
    team_names: Sequence[str | None] | None = None,
    gen_config: GenerationConfig | None = None,
    name_parts: NameParts | None = None,
    prerun: bool = True,
    offseason_settings=None,
    progress=None,
    draft_settings=None,
    scout_sd: dict | None = None,
    calibration: dict | None = None,
) -> League:
    """架空のリーグを作り、入力された球団名を付ける。team_names は球団の順(空欄は None か "")。

    prerun=True なら、初期選手の生成の後に事前運転(試合なしで年度の確定を数十年分)と校正を行う(D-190、D-197)。
    progress には事前運転の (終わった年数, 全年数) を知らせる。"""
    from .offseason import apply_calibration, load_offseason_settings
    from .offseason import prerun as run_prerun

    gen_config = gen_config or load_generation_config()
    name_parts = name_parts or load_name_parts()
    league = generate_league(seed, gen_config, name_parts)
    if prerun:
        from .draft import load_draft_settings

        settings = offseason_settings or load_offseason_settings()
        draft_settings = draft_settings or load_draft_settings()
        run_prerun(league, seed, gen_config, name_parts, settings, progress=progress, draft_settings=draft_settings, scout_sd=scout_sd)
        apply_calibration(league, calibration if calibration is not None else settings.calibration(draft_settings.default_level), gen_config)
    if team_names is not None:
        for team, name in zip(league.teams, resolve_team_names(league, team_names)):
            team.display_name = name
    return league


def public_player(player: Player, team_name: str | None = None) -> dict:
    """画面に出してよい選手の情報(隠し情報と真の能力値を含めない)。"""
    return {
        "id": player.id,
        "name": player.name,
        "family_name": player.family_name,
        "given_name": player.given_name,
        "age": player.age,
        "role": player.role,
        "position": player.position,
        "bats": player.bats,
        "throws": player.throws,
        "origin": player.origin,
        "team_id": player.team_id,
        "team_name": team_name,
    }
