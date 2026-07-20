"""Tournament rules: bracket tree, progression, score judging, final ranking. Pure.

The bracket has 12 nodes (3 rounds x 4 matchups); teams are referred to by
their draw labels G1-G8:
  R1: G1vG2, G3vG4 (upper half) | G5vG6, G7vG8 (lower half)
  R2: winners bracket W(R1-1)vW(R1-2), W(R1-3)vW(R1-4); losers bracket likewise
  R3: places 1/2 = winners-bracket winners; 3/4 = their losers;
      5/6 and 7/8 come from the losers bracket
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Node:
    id: str
    round: int
    bank: int  # 0 = upper (court bank 1), 1 = lower (court bank 2)
    # R1: ("G1", "G2"); R2/R3: ((feeder_id, "win"|"lose"), (feeder_id, "win"|"lose"))
    feeders: tuple
    tag: str


def bracket() -> Dict[str, Node]:
    n = {}
    n["R1-1"] = Node("R1-1", 1, 0, ("G1", "G2"), "opening, upper")
    n["R1-2"] = Node("R1-2", 1, 0, ("G3", "G4"), "opening, upper")
    n["R1-3"] = Node("R1-3", 1, 1, ("G5", "G6"), "opening, lower")
    n["R1-4"] = Node("R1-4", 1, 1, ("G7", "G8"), "opening, lower")
    n["R2-WU"] = Node("R2-WU", 2, 0, (("R1-1", "win"), ("R1-2", "win")), "semis, winners")
    n["R2-LU"] = Node("R2-LU", 2, 0, (("R1-1", "lose"), ("R1-2", "lose")), "semis, losers")
    n["R2-WL"] = Node("R2-WL", 2, 1, (("R1-3", "win"), ("R1-4", "win")), "semis, winners")
    n["R2-LL"] = Node("R2-LL", 2, 1, (("R1-3", "lose"), ("R1-4", "lose")), "semis, losers")
    n["R3-12"] = Node("R3-12", 3, 0, (("R2-WU", "win"), ("R2-WL", "win")), "final, places 1/2")
    n["R3-34"] = Node("R3-34", 3, 0, (("R2-WU", "lose"), ("R2-WL", "lose")), "final, places 3/4")
    n["R3-56"] = Node("R3-56", 3, 1, (("R2-LU", "win"), ("R2-LL", "win")), "final, places 5/6")
    n["R3-78"] = Node("R3-78", 3, 1, (("R2-LU", "lose"), ("R2-LL", "lose")), "final, places 7/8")
    return n


# the five match slots of a matchup; the blind slot's actual category is
# config format.blind_match_types[round - 1]
MATCH_SLOTS = ("WD", "MD1", "MD2", "MD3", "BLIND")

# R3 node -> (winner place, loser place)
R3_PLACEMENTS = {"R3-12": (1, 2), "R3-34": (3, 4), "R3-56": (5, 6), "R3-78": (7, 8)}


def game_winner(score: Sequence[int]) -> str:
    if score[0] == score[1]:
        raise ValueError(f"game score cannot be a tie: {score}")
    return "a" if score[0] > score[1] else "b"


def match_winner(games: Sequence[Sequence[int]], games_to_win: int = 2) -> Optional[str]:
    """Best-of-N winner; None if undecided."""
    wins = {"a": 0, "b": 0}
    for g in games:
        wins[game_winner(g)] += 1
    for side in ("a", "b"):
        if wins[side] >= games_to_win:
            return side
    return None


def matchup_winner(
    match_games: Dict[str, Sequence[Sequence[int]]],
    matches_to_win: int = 3,
    games_to_win: int = 2,
) -> Optional[str]:
    """Winner of a matchup: first side to the required match count; None if undecided."""
    pts = {"a": 0, "b": 0}
    for games in match_games.values():
        w = match_winner(games, games_to_win)
        if w:
            pts[w] += 1
    for side in ("a", "b"):
        if pts[side] >= matches_to_win:
            return side
    return None


def resolve_groups(
    node: Node, winners: Dict[str, str], nodes: Dict[str, Node]
) -> Optional[Tuple[str, str]]:
    """Resolve the two G labels of a node; None while upstream is undecided.

    winners: node_id -> winning G label.
    """
    sides: List[str] = []
    for f in node.feeders:
        if isinstance(f, str):  # R1 feeders are direct G labels
            sides.append(f)
            continue
        feeder_id, need = f
        w = winners.get(feeder_id)
        if w is None:
            return None
        if need == "win":
            sides.append(w)
        else:
            fnode = nodes[feeder_id]
            resolved = resolve_groups(fnode, winners, nodes)
            if resolved is None:
                return None
            sides.append(resolved[0] if resolved[1] == w else resolved[1])
    return sides[0], sides[1]


def final_ranking(winners: Dict[str, str], nodes: Dict[str, Node]) -> Dict[int, str]:
    """Final places {1: "G?", ...} once round 3 concludes; undecided places absent."""
    ranking: Dict[int, str] = {}
    for nid, (win_place, lose_place) in R3_PLACEMENTS.items():
        w = winners.get(nid)
        if w is None:
            continue
        resolved = resolve_groups(nodes[nid], winners, nodes)
        if resolved is None:
            continue
        ranking[win_place] = w
        ranking[lose_place] = resolved[0] if resolved[1] == w else resolved[1]
    return ranking
