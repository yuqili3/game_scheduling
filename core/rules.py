"""赛制规则: 对阵树、晋级逻辑、比分判定、最终排名。纯函数。

对阵树共 12 个节点(3 轮 × 4 场对抗),队伍以抽签编号 G1-G8 表示:
  R1: G1vG2, G3vG4(上半区) | G5vG6, G7vG8(下半区)
  R2: 胜者组 W(R1-1)vW(R1-2), W(R1-3)vW(R1-4);败者组 L 同理
  R3: 争1/2 = 两个胜者组胜者;争3/4 = 其败者;争5/6、争7/8 来自败者组
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Node:
    id: str
    轮次: int
    半区: int  # 0 = 上(场地组1), 1 = 下(场地组2)
    # R1: ("G1", "G2");R2/R3: ((feeder_id, "胜"/"负"), (feeder_id, "胜"/"负"))
    feeders: tuple
    tag: str


def bracket() -> Dict[str, Node]:
    n = {}
    n["R1-1"] = Node("R1-1", 1, 0, ("G1", "G2"), "初赛·上")
    n["R1-2"] = Node("R1-2", 1, 0, ("G3", "G4"), "初赛·上")
    n["R1-3"] = Node("R1-3", 1, 1, ("G5", "G6"), "初赛·下")
    n["R1-4"] = Node("R1-4", 1, 1, ("G7", "G8"), "初赛·下")
    n["R2-W上"] = Node("R2-W上", 2, 0, (("R1-1", "胜"), ("R1-2", "胜")), "半决赛·胜者组")
    n["R2-L上"] = Node("R2-L上", 2, 0, (("R1-1", "负"), ("R1-2", "负")), "半决赛·败者组")
    n["R2-W下"] = Node("R2-W下", 2, 1, (("R1-3", "胜"), ("R1-4", "胜")), "半决赛·胜者组")
    n["R2-L下"] = Node("R2-L下", 2, 1, (("R1-3", "负"), ("R1-4", "负")), "半决赛·败者组")
    n["R3-12"] = Node("R3-12", 3, 0, (("R2-W上", "胜"), ("R2-W下", "胜")), "总决赛·争1/2")
    n["R3-34"] = Node("R3-34", 3, 0, (("R2-W上", "负"), ("R2-W下", "负")), "总决赛·争3/4")
    n["R3-56"] = Node("R3-56", 3, 1, (("R2-L上", "胜"), ("R2-L下", "胜")), "总决赛·争5/6")
    n["R3-78"] = Node("R3-78", 3, 1, (("R2-L上", "负"), ("R2-L下", "负")), "总决赛·争7/8")
    return n


# 每场对抗的 5 个场次;盲抽局的实际类别由 config 的 盲抽局类型[轮次-1] 决定
MATCH_SLOTS = ("女双", "男双1", "男双2", "男双3", "盲抽")

# R3 各节点的名次归属: (胜者名次, 败者名次)
R3_PLACEMENTS = {"R3-12": (1, 2), "R3-34": (3, 4), "R3-56": (5, 6), "R3-78": (7, 8)}


def game_winner(比分: Sequence[int]) -> str:
    if 比分[0] == 比分[1]:
        raise ValueError(f"比分不能相等: {比分}")
    return "a" if 比分[0] > 比分[1] else "b"


def match_winner(局分: Sequence[Sequence[int]], 需胜局: int = 2) -> Optional[str]:
    """三局两胜的胜方;未决出返回 None。"""
    wins = {"a": 0, "b": 0}
    for g in 局分:
        wins[game_winner(g)] += 1
    for side in ("a", "b"):
        if wins[side] >= 需胜局:
            return side
    return None


def matchup_winner(
    各场局分: Dict[str, Sequence[Sequence[int]]],
    获胜所需场次: int = 3,
    需胜局: int = 2,
) -> Optional[str]:
    """对抗的胜方: 5 场中先拿到指定场次的一方;未决出返回 None。"""
    pts = {"a": 0, "b": 0}
    for 局分 in 各场局分.values():
        w = match_winner(局分, 需胜局)
        if w:
            pts[w] += 1
    for side in ("a", "b"):
        if pts[side] >= 获胜所需场次:
            return side
    return None


def resolve_groups(
    node: Node, winners: Dict[str, str], nodes: Dict[str, Node]
) -> Optional[Tuple[str, str]]:
    """解析节点两侧的 G 编号;上游胜负未出返回 None。

    winners: node_id -> 胜方 G 编号。
    """
    sides: List[str] = []
    for f in node.feeders:
        if isinstance(f, str):  # R1 直接是 G 编号
            sides.append(f)
            continue
        feeder_id, need = f
        w = winners.get(feeder_id)
        if w is None:
            return None
        if need == "胜":
            sides.append(w)
        else:
            fnode = nodes[feeder_id]
            resolved = resolve_groups(fnode, winners, nodes)
            if resolved is None:
                return None
            sides.append(resolved[0] if resolved[1] == w else resolved[1])
    return sides[0], sides[1]


def final_ranking(winners: Dict[str, str], nodes: Dict[str, Node]) -> Dict[int, str]:
    """三轮全部结束后的最终名次 {1: "G?", ..., 8: "G?"};未结束的名次缺席。"""
    ranking: Dict[int, str] = {}
    for nid, (win_rank, lose_rank) in R3_PLACEMENTS.items():
        w = winners.get(nid)
        if w is None:
            continue
        resolved = resolve_groups(nodes[nid], winners, nodes)
        if resolved is None:
            continue
        ranking[win_rank] = w
        ranking[lose_rank] = resolved[0] if resolved[1] == w else resolved[1]
    return ranking
