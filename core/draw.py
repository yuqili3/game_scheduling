"""Draw logic: pure functions. Randomness comes only from the caller-supplied
seed, so identical seeds always produce identical results."""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .models import Player, TournamentState


def draw_teams(players: Dict[int, Player], num_teams: int, seed: int) -> Dict[int, List[int]]:
    """Initial team draw.

    Captains, females and non-captain males are shuffled as three separate
    pools and dealt into teams, guaranteeing each team gets exactly
    1 captain + N females + M regular males. Pools are sorted before
    shuffling so the result is independent of dict ordering.
    """
    rng = random.Random(seed)
    captains = sorted(p.id for p in players.values() if p.is_captain)
    females = sorted(p.id for p in players.values() if p.gender == "F")
    males = sorted(p.id for p in players.values() if p.gender == "M" and not p.is_captain)
    if len(captains) != num_teams:
        raise ValueError(f"{len(captains)} captains != {num_teams} teams")
    f_per = len(females) // num_teams
    m_per = len(males) // num_teams
    rng.shuffle(captains)
    rng.shuffle(females)
    rng.shuffle(males)
    teams: Dict[int, List[int]] = {}
    for i in range(num_teams):
        teams[i + 1] = (
            [captains[i]]
            + females[f_per * i : f_per * (i + 1)]
            + males[m_per * i : m_per * (i + 1)]
        )
    return teams


def blind_draw_pick(
    category: str, females: List[int], males: List[int], seed: int
) -> List[int]:
    """Seed-determined blind-match pick from pre-filtered eligible pools.

    XD: 1 female + 1 non-captain male; MD: 2 non-captain males. Pools must
    already exclude captains and players drawn in earlier rounds' blind
    matches (see MatchDayState.blind_eligible). Same seed, same pick.
    """
    rng = random.Random(seed)
    if category == "XD":
        if not females or not males:
            raise ValueError("no eligible players left for an XD blind draw")
        return [rng.choice(sorted(females)), rng.choice(sorted(males))]
    if category == "MD":
        if len(males) < 2:
            raise ValueError("fewer than 2 eligible males for an MD blind draw")
        return rng.sample(sorted(males), 2)
    raise ValueError(f"unknown blind category: {category}")


def withdraw_redraw(
    state: TournamentState,
    withdrawn_id: int,
    substitute_name: str,
    seed: int,
    new_captain_id: Optional[int] = None,
) -> Tuple[Dict[int, Player], Dict[int, List[int]], List[str]]:
    """Pre-event withdrawal redraw (tournament rules).

    1. If the withdrawn player is a captain, the designated successor
       (a non-captain male on the same team) takes over first;
    2. each of the other 7 full teams randomly gives up one non-captain
       member of the same gender as the withdrawn player;
    3. those 7 + the substitute (assigned a fresh id) are reshuffled into
       the 8 vacancies;
    4. team structure is unchanged: 1 captain + 5 regular males + 2 females.

    Returns (new players, new teams, change log). Does not mutate `state`.
    """
    rng = random.Random(seed)
    players = dict(state.players)
    teams = {tid: list(m) for tid, m in state.teams.items()}
    if withdrawn_id not in players:
        raise ValueError(f"withdrawn player #{withdrawn_id} does not exist")
    quitter = players[withdrawn_id]
    home_tid = state.team_of(withdrawn_id)
    if home_tid is None:
        raise ValueError(f"withdrawn player #{withdrawn_id} is not on any team")
    log: List[str] = []

    if quitter.is_captain:
        if new_captain_id is None:
            raise ValueError("captain withdrawal requires a successor (new_captain_id)")
        successor = players.get(new_captain_id)
        if (
            successor is None
            or state.team_of(new_captain_id) != home_tid
            or successor.is_captain
            or successor.gender != "M"
        ):
            raise ValueError("successor must be a non-captain male on the same team")
        players[new_captain_id] = Player(successor.id, successor.name, successor.gender, True)
        teams[home_tid].remove(new_captain_id)
        teams[home_tid].insert(0, new_captain_id)
        # the outgoing captain is then handled like a regular member
        players[withdrawn_id] = Player(quitter.id, quitter.name, quitter.gender, False)
        quitter = players[withdrawn_id]
        log.append(
            f"{players[new_captain_id].name}(#{new_captain_id}) takes over as captain of team {home_tid}"
        )

    gender = quitter.gender
    teams[home_tid].remove(withdrawn_id)
    players.pop(withdrawn_id)
    log.append(f"{quitter.name}(#{withdrawn_id}, {gender}) withdrew from team {home_tid}")

    # each of the other 7 teams gives up one same-gender non-captain member
    pool: List[int] = []
    for tid in sorted(teams):
        if tid == home_tid:
            continue
        cands = sorted(
            m for m in teams[tid] if players[m].gender == gender and not players[m].is_captain
        )
        if not cands:
            raise ValueError(f"team {tid} has no drawable {gender} non-captain member")
        picked = rng.choice(cands)
        teams[tid].remove(picked)
        pool.append(picked)
        log.append(f"team {tid} gives up {players[picked].name}(#{picked}) for redraw")

    # substitute joins with a fresh id
    sub_id = max(players) + 1
    players[sub_id] = Player(sub_id, substitute_name, gender, False)
    pool.append(sub_id)
    log.append(f"substitute {substitute_name} registered as #{sub_id}")

    # reshuffle the 8 into the vacancies, one per team in team-id order
    rng.shuffle(pool)
    for tid, pid in zip(sorted(teams), pool):
        teams[tid].append(pid)
        log.append(f"{players[pid].name}(#{pid}) drawn into team {tid}")

    return players, teams, log
