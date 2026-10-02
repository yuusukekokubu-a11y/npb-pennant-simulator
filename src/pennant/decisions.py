"""勝利・敗戦・セーブ・ホールドの判定(実装⑤。D-094。公式記録の簡易版)。

打席ログで点差を追いかけて決める。
  - 勝ち越しの得点:勝ったチームが、最後にリードを奪った打席の得点。その打席の生還(走者の動きの並び)を
    前から数えて、勝ち越しに必要な分に当たる走者を、決勝点の走者とする。
  - 勝利投手:その時点で、勝ったチームの投手として試合にいた投手(先発は試合の初めから、
    救援は最初に対戦した打席から、試合にいるとみなす)。それが先発で、5回(15アウト)に満たなければ、
    勝ったチームの最初の救援投手。
  - 敗戦投手:決勝点の走者を出塁させた投手。
  - セーブ:勝ったチームの、勝利投手でない最後の投手が、リードしている場面で登板し、
    ①3点以内のリードで1イニング以上 ②同点・勝ち越し点の走者が塁上か打席にいる ③3イニング以上、のどれか。
  - ホールド:勝ったチームの、勝利投手でもセーブ投手でもない救援投手が、リードしている場面で登板し、
    リードを保ったまま、試合終了前に降板した。
引き分けの試合では、どれも付かない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .game import GameResult

STARTER_MIN_OUTS = 15  # 先発が勝利投手になるのに必要な投球回(5回)
SAVE_MAX_LEAD = 3
SAVE_MIN_OUTS = 3  # ①の1イニング
SAVE_LONG_OUTS = 9  # ③の3イニング


@dataclass
class Decisions:
    win: str | None = None
    loss: str | None = None
    save: str | None = None
    holds: list[str] = field(default_factory=list)


def decide(result: GameResult) -> Decisions:
    if result.tie:
        return Decisions()
    winner = result.winner
    loser = result.away_team_id if winner == result.home_team_id else result.home_team_id

    # 打席ごとの「打席の前の点差」(勝ったチームから見た)と、最後にリードを奪った打席
    lead_before: list[int] = []
    lead = 0
    go_ahead = None  # (打席の番号, 決勝点の走者の動き)
    for i, x in enumerate(result.log):
        lead_before.append(lead)
        if x.batting_team_id == winner:
            if x.runs and lead <= 0 < lead + x.runs:
                scored = [m for m in x.moves if m.scored]
                go_ahead = (i, scored[-lead])  # 勝ち越しに必要な分(-lead + 1 番目)
            lead += x.runs
        else:
            lead -= x.runs

    lines = [p for p in result.pitchers if p.team_id == winner]
    first_pa = {}
    for i, x in enumerate(result.log):
        first_pa.setdefault(x.pitcher_id, i)
    entry = {p.pitcher_id: (-1 if p.role == "starter" else first_pa.get(p.pitcher_id, len(result.log))) for p in lines}

    k, run = go_ahead
    of_record = [p for p in lines if entry[p.pitcher_id] <= k][-1]
    if of_record.role == "starter" and of_record.outs < STARTER_MIN_OUTS:
        relievers = [p for p in lines if p.role != "starter"]
        win = relievers[0].pitcher_id if relievers else of_record.pitcher_id
    else:
        win = of_record.pitcher_id
    loss = run.responsible_pitcher_id

    def entry_state(pid: str) -> tuple[int, int]:
        """登板した最初の打席の前の(リード, 塁上の走者の数)。"""
        i = first_pa[pid]
        bo = result.log[i].base_out
        return lead_before[i], int(bo.first) + int(bo.second) + int(bo.third)

    save = None
    last = lines[-1]
    if last.pitcher_id != win and last.role != "starter" and last.pitcher_id in first_pa:
        entered_lead, runners = entry_state(last.pitcher_id)
        if entered_lead > 0 and (
            (entered_lead <= SAVE_MAX_LEAD and last.outs >= SAVE_MIN_OUTS)
            or entered_lead <= runners + 1
            or last.outs >= SAVE_LONG_OUTS
        ):
            save = last.pitcher_id

    holds = []
    for p in lines:
        pid = p.pitcher_id
        if p.role == "starter" or pid in (win, save) or p is last or pid not in first_pa:
            continue
        entered_lead, _ = entry_state(pid)
        if entered_lead <= 0:
            continue
        kept = True
        for i, x in enumerate(result.log):
            if x.pitcher_id == pid and lead_before[i] - x.runs <= 0:  # 相手の攻撃で点を取られた後の点差
                kept = False
                break
        if kept:
            holds.append(pid)
    return Decisions(win, loss, save, holds)
