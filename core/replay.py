"""事件重放: 状态 = replay(初始人员表, 事件序列)。核心可复现机制。"""
from __future__ import annotations

from typing import Dict, Iterable

from .draw import draw_teams, withdraw_redraw
from .models import Event, Player, TournamentState


def replay(
    initial_players: Dict[int, Player],
    events: Iterable[Event],
    队伍数量: int = 8,
) -> TournamentState:
    state = TournamentState(players=dict(initial_players))
    for ev in events:
        if ev.type == "初始抽签":
            if ev.seed is None:
                raise ValueError(f"事件 seq={ev.seq} 初始抽签缺少 seed")
            state.teams = draw_teams(state.players, 队伍数量, ev.seed)
            state.changelog.append(f"[{ev.ts}] 初始抽签 seed={ev.seed}")
        elif ev.type == "指定分队":
            # payload: {"队伍": {"1": [17, 1, 2, ...], ...}},用于导入线下已公布的分队结果
            state.teams = {
                int(t): [int(m) for m in ms] for t, ms in ev.payload["队伍"].items()
            }
            state.changelog.append(f"[{ev.ts}] 指定分队(导入线下结果)")
        elif ev.type == "退赛重抽":
            if ev.seed is None:
                raise ValueError(f"事件 seq={ev.seq} 退赛重抽缺少 seed")
            p = ev.payload
            新队长 = p.get("新队长序号")
            players, teams, log = withdraw_redraw(
                state,
                int(p["退赛者序号"]),
                p["候补姓名"],
                ev.seed,
                int(新队长) if 新队长 is not None else None,
            )
            state.players, state.teams = players, teams
            state.changelog += [f"[{ev.ts}] (seed={ev.seed}) {line}" for line in log]
        else:
            raise ValueError(f"未知事件类型: {ev.type} (seq={ev.seq})")
    return state
