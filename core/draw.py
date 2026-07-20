"""抽签逻辑: 全部纯函数。随机性只来自调用方传入的 seed,同 seed 结果必然一致。"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .models import Player, TournamentState


def draw_teams(players: Dict[int, Player], 队伍数量: int, seed: int) -> Dict[int, List[int]]:
    """初始抽签分队。

    队长、女生、男生三个池分别洗牌后依次切分入队,保证每队恰好
    1 队长 + N 女 + M 普通男。输入池先排序再洗牌,确保与 dict 顺序无关。
    """
    rng = random.Random(seed)
    captains = sorted(p.序号 for p in players.values() if p.是否队长)
    females = sorted(p.序号 for p in players.values() if p.性别 == "女")
    males = sorted(
        p.序号 for p in players.values() if p.性别 == "男" and not p.是否队长
    )
    if len(captains) != 队伍数量:
        raise ValueError(f"队长数{len(captains)} != 队伍数量{队伍数量}")
    女每队 = len(females) // 队伍数量
    男每队 = len(males) // 队伍数量
    rng.shuffle(captains)
    rng.shuffle(females)
    rng.shuffle(males)
    teams: Dict[int, List[int]] = {}
    for i in range(队伍数量):
        teams[i + 1] = (
            [captains[i]]
            + females[女每队 * i : 女每队 * (i + 1)]
            + males[男每队 * i : 男每队 * (i + 1)]
        )
    return teams


def withdraw_redraw(
    state: TournamentState,
    退赛者序号: int,
    候补姓名: str,
    seed: int,
    新队长序号: Optional[int] = None,
) -> Tuple[Dict[int, Player], Dict[int, List[int]], List[str]]:
    """赛前退赛重抽(PDF 规则)。

    1. 若退赛者是队长,先由指定的新队长接任(必须是本队非队长男队员);
    2. 从其余 7 支完整队伍各随机抽 1 名与退赛者同性别的非队长成员;
    3. 该 7 人 + 候补(编为新序号)共 8 人重新洗牌,分入 8 支队伍的空缺;
    4. 结构不变: 每队 1 队长 + 5 普通男 + 2 女。

    返回 (新 players, 新 teams, 变更日志)。不修改传入的 state。
    """
    rng = random.Random(seed)
    players = dict(state.players)
    teams = {tid: list(m) for tid, m in state.teams.items()}
    if 退赛者序号 not in players:
        raise ValueError(f"退赛者 #{退赛者序号} 不存在")
    quitter = players[退赛者序号]
    home_tid = state.team_of(退赛者序号)
    if home_tid is None:
        raise ValueError(f"退赛者 #{退赛者序号} 不在任何队伍中")
    log: List[str] = []

    if quitter.是否队长:
        if 新队长序号 is None:
            raise ValueError("队长退赛必须指定新队长序号")
        successor = players.get(新队长序号)
        if (
            successor is None
            or state.team_of(新队长序号) != home_tid
            or successor.是否队长
            or successor.性别 != "男"
        ):
            raise ValueError("新队长必须是本队非队长男队员")
        players[新队长序号] = Player(successor.序号, successor.姓名, successor.性别, True)
        teams[home_tid].remove(新队长序号)
        teams[home_tid].insert(0, 新队长序号)
        # 原队长退赛后按普通队员处理空缺
        players[退赛者序号] = Player(quitter.序号, quitter.姓名, quitter.性别, False)
        quitter = players[退赛者序号]
        log.append(f"队长 {players[新队长序号].姓名}(#{新队长序号}) 接任队伍{home_tid}队长")

    gender = quitter.性别
    teams[home_tid].remove(退赛者序号)
    players.pop(退赛者序号)
    log.append(f"{quitter.姓名}(#{退赛者序号}, {gender}) 退出队伍{home_tid}")

    # 其余 7 队各随机抽 1 名同性别非队长成员入池
    pool: List[int] = []
    for tid in sorted(teams):
        if tid == home_tid:
            continue
        cands = sorted(
            m for m in teams[tid] if players[m].性别 == gender and not players[m].是否队长
        )
        if not cands:
            raise ValueError(f"队伍{tid}没有可抽取的{gender}性非队长成员")
        picked = rng.choice(cands)
        teams[tid].remove(picked)
        pool.append(picked)
        log.append(f"队伍{tid} 抽出 {players[picked].姓名}(#{picked}) 参与重抽")

    # 候补编入新序号
    sub_id = max(players) + 1
    players[sub_id] = Player(sub_id, 候补姓名, gender, False)
    pool.append(sub_id)
    log.append(f"候补 {候补姓名} 编为 #{sub_id} 入池")

    # 8 人重新洗牌,按队伍编号顺序分入空缺
    rng.shuffle(pool)
    for tid, pid in zip(sorted(teams), pool):
        teams[tid].append(pid)
        log.append(f"{players[pid].姓名}(#{pid}) 抽入队伍{tid}")

    return players, teams, log
