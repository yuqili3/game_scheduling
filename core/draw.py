"""Draw logic: pure functions. Randomness comes only from the caller-supplied
seed, so identical seeds always produce identical results.

Everything here is driven by a TeamComposition (config `players.team_composition`)
rather than a hard-coded "1 captain + 5 men + 2 women", so a tournament with a
different gender mix (e.g. 6 teams of 2 M + 2 F) only needs a new config."""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .models import Player, Seed, Slot, TeamComposition, TournamentState


def _tier_key(level: Optional[str]) -> Tuple[int, str]:
    # tiered players first (sorted by tier label), untiered last
    return (1, "") if level is None else (0, str(level))


def _deal_order(
    pool: List[int], players: Dict[int, Player], rng: random.Random, balance_by_level: bool
) -> List[int]:
    """Shuffle a pool into dealing order. With level balancing the pool is
    split into tiers, each tier shuffled separately and the tiers concatenated,
    so a round-robin deal spreads every tier evenly across the teams."""
    if not balance_by_level:
        cards = list(pool)
        rng.shuffle(cards)
        return cards
    tiers: Dict[Tuple[int, str], List[int]] = {}
    for pid in pool:  # pool is sorted, so tier contents are order-independent
        tiers.setdefault(_tier_key(players[pid].level), []).append(pid)
    cards: List[int] = []
    for key in sorted(tiers):
        tier = tiers[key]
        rng.shuffle(tier)
        cards += tier
    return cards


def slot_pool(players: Dict[int, Player], slot: Slot) -> List[int]:
    """Sorted ids of the players eligible for a slot (gender + role match)."""
    return sorted(
        p.id for p in players.values() if p.gender == slot.gender and p.role == slot.role
    )


def draw_teams(
    players: Dict[int, Player],
    composition: TeamComposition,
    num_teams: int,
    seed: Seed,
    balance_by_level: bool = True,
) -> Dict[int, List[int]]:
    """Initial team draw for any team structure.

    For each slot of the composition, in order: take the eligible pool (which
    must contain exactly count x num_teams players), put it in dealing order
    (shuffled; tier-stratified when levels are present and balancing is on),
    shuffle the team order once, then deal round-robin. Every team therefore
    receives exactly `slot.count` players from every slot, and with tiers each
    team gets floor/ceil(tier_size / num_teams) from every tier. Which players
    land where, and which teams get the extra higher-tier player, are decided
    by the seed alone.

    Players with `fixed_team` set (e.g. appointed captains) are seated in that
    team before dealing and leave the pool; the remaining seats of every slot
    are dealt layer by layer (one card to every team that still needs one,
    then the next layer), so level balancing is unaffected.
    """
    if num_teams < 1:
        raise ValueError("num_teams must be >= 1")
    rng = random.Random(seed)
    team_ids = list(range(1, num_teams + 1))
    teams: Dict[int, List[int]] = {tid: [] for tid in team_ids}
    assigned: set = set()

    # pinned players, grouped by slot key and team
    fixed: Dict[Tuple[str, str], Dict[int, List[int]]] = {}
    slot_keys = {s.key for s in composition.slots}
    for p in players.values():
        if p.fixed_team is None:
            continue
        if p.fixed_team not in teams:
            raise ValueError(
                f"{p.name}(#{p.id}) is fixed to team {p.fixed_team}; teams are 1..{num_teams}"
            )
        if (p.gender, p.role) not in slot_keys:
            raise ValueError(
                f"{p.name}(#{p.id}) is fixed to team {p.fixed_team} but {p.gender}/{p.role} "
                f"matches no slot of the composition ({composition.describe()})"
            )
        fixed.setdefault((p.gender, p.role), {}).setdefault(p.fixed_team, []).append(p.id)

    for slot in composition.slots:
        pinned_by_team = fixed.get(slot.key, {})
        need: Dict[int, int] = {}
        for tid in team_ids:
            pinned = sorted(pinned_by_team.get(tid, []))
            if len(pinned) > slot.count:
                raise ValueError(
                    f"team {tid} has {len(pinned)} players fixed into slot "
                    f"{slot.gender}/{slot.role}, which allows {slot.count}"
                )
            teams[tid] += pinned
            assigned.update(pinned)
            need[tid] = slot.count - len(pinned)
        pool = [pid for pid in slot_pool(players, slot) if players[pid].fixed_team is None]
        n_fixed = sum(len(v) for v in pinned_by_team.values())
        total_need = sum(need.values())
        if len(pool) != total_need:
            raise ValueError(
                f"slot {slot.gender}/{slot.role}: {len(pool)} eligible players to draw, "
                f"need exactly {total_need} ({slot.count} x {num_teams} teams"
                + (f" minus {n_fixed} fixed" if n_fixed else "") + ")"
            )
        cards = _deal_order(pool, players, rng, balance_by_level)
        order = list(team_ids)
        rng.shuffle(order)
        seats = [tid for layer in range(slot.count) for tid in order if need[tid] > layer]
        for pid, tid in zip(cards, seats):
            teams[tid].append(pid)
        assigned.update(cards)
    leftover = sorted(pid for pid in players if pid not in assigned)
    if leftover:
        raise ValueError(
            f"{len(leftover)} players match no slot of the composition "
            f"({composition.describe()}): ids {leftover[:10]}"
        )
    return teams


def assign_pairs(
    teams: Dict[int, List[int]], players: Dict[int, Player], seed: Seed
) -> Dict[int, List[List[int]]]:
    """Seed-determined mixed-doubles pairing inside every team: each male is
    paired with a female (teams must have equally many of each). Males are
    taken in team order, so with a captain-first team the captain's pair is
    pair index 0 (announced as A1); the females are shuffled by the seed."""
    rng = random.Random(seed)
    pairs: Dict[int, List[List[int]]] = {}
    for tid in sorted(teams):
        males = [m for m in teams[tid] if players[m].gender == "M"]
        females = [m for m in teams[tid] if players[m].gender == "F"]
        if len(males) != len(females):
            raise ValueError(
                f"team {tid} has {len(males)} males and {len(females)} females; "
                "cannot form mixed-doubles pairs"
            )
        fs = sorted(females)
        rng.shuffle(fs)
        pairs[tid] = [[m, f] for m, f in zip(males, fs)]
    return pairs


def replace_in_pairs(
    pairs: Dict[int, List[List[int]]], old_id: int, new_id: int, captain_first: Optional[int] = None
) -> Dict[int, List[List[int]]]:
    """Pairs after a direct substitution: `new_id` takes `old_id`'s seat. If
    `captain_first` (the current captain id of that team) is given, the pair
    containing it is moved to index 0 (captain succession)."""
    out: Dict[int, List[List[int]]] = {}
    for tid, prs in pairs.items():
        new_prs = [[new_id if x == old_id else x for x in pr] for pr in prs]
        if captain_first is not None:
            new_prs.sort(key=lambda pr: 0 if captain_first in pr else 1)
        out[tid] = new_prs
    return out


def group_draw_assign(team_ids: List[int], seed: Seed) -> Dict[str, int]:
    """Seed-determined group draw: shuffle the teams into G1..Gn."""
    rng = random.Random(seed)
    ids = sorted(team_ids)
    rng.shuffle(ids)
    return {f"G{i + 1}": tid for i, tid in enumerate(ids)}


def blind_draw_pick(
    category: str, females: List[int], males: List[int], seed: Seed
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


# ---------------------------------------------------------------------------
# withdrawal handling
# ---------------------------------------------------------------------------

def _remove_withdrawn(
    state: TournamentState, withdrawn_id: int, new_captain_id: Optional[int]
) -> Tuple[Dict[int, Player], Dict[int, List[int]], Player, int, int, List[str]]:
    """Shared first half of both withdrawal flows. Handles the captain
    succession and removes the withdrawn player. Returns copies of players and
    teams plus (withdrawn player, home team id, index in that team, log)."""
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
            or successor.gender != quitter.gender
        ):
            raise ValueError(
                "successor must be a non-captain member of the same team and gender"
            )
        players[new_captain_id] = Player(
            successor.id, successor.name, successor.gender, True, successor.level
        )
        teams[home_tid].remove(new_captain_id)
        teams[home_tid].insert(0, new_captain_id)
        # the outgoing captain is then handled like a regular member
        players[withdrawn_id] = Player(quitter.id, quitter.name, quitter.gender, False, quitter.level)
        quitter = players[withdrawn_id]
        log.append(
            f"{players[new_captain_id].name}(#{new_captain_id}) takes over as captain of team {home_tid}"
        )

    idx = teams[home_tid].index(withdrawn_id)
    teams[home_tid].remove(withdrawn_id)
    players.pop(withdrawn_id)
    log.append(f"{quitter.name}(#{withdrawn_id}, {quitter.gender}) withdrew from team {home_tid}")
    return players, teams, quitter, home_tid, idx, log


def withdraw_redraw(
    state: TournamentState,
    withdrawn_id: int,
    substitute_name: str,
    seed: Seed,
    new_captain_id: Optional[int] = None,
) -> Tuple[Dict[int, Player], Dict[int, List[int]], List[str]]:
    """Pre-event withdrawal redraw (2026 melee rules, generic in team count).

    1. If the withdrawn player is a captain, the designated successor
       (a non-captain member of the same team and gender) takes over first;
    2. each of the other teams randomly gives up one non-captain member of
       the same gender as the withdrawn player;
    3. those + the substitute (assigned a fresh id) are reshuffled into the
       vacancies, one per team;
    4. team structure is unchanged.

    Returns (new players, new teams, change log). Does not mutate `state`.
    """
    rng = random.Random(seed)
    players, teams, quitter, home_tid, _, log = _remove_withdrawn(
        state, withdrawn_id, new_captain_id
    )
    gender = quitter.gender

    # each other team gives up one same-gender non-captain member
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

    # substitute joins with a fresh id, inheriting the vacated player's tier
    sub_id = max(players) + 1
    players[sub_id] = Player(sub_id, substitute_name, gender, False, quitter.level)
    pool.append(sub_id)
    log.append(f"substitute {substitute_name} registered as #{sub_id}")

    # reshuffle into the vacancies, one per team in team-id order
    rng.shuffle(pool)
    for tid, pid in zip(sorted(teams), pool):
        teams[tid].append(pid)
        log.append(f"{players[pid].name}(#{pid}) drawn into team {tid}")

    return players, teams, log


def substitute_direct(
    state: TournamentState,
    withdrawn_id: int,
    substitute_name: str,
    new_captain_id: Optional[int] = None,
) -> Tuple[Dict[int, Player], Dict[int, List[int]], List[str]]:
    """Direct waitlist substitution, no randomness: the substitute takes the
    withdrawn player's exact seat (team, position, gender, tier). Used by
    events whose rules forbid withdrawals after the draw is published and
    fill emergencies from a waitlist in order (2026 mixed-doubles friendly).

    Returns (new players, new teams, change log). Does not mutate `state`.
    """
    players, teams, quitter, home_tid, idx, log = _remove_withdrawn(
        state, withdrawn_id, new_captain_id
    )
    sub_id = max(players) + 1
    players[sub_id] = Player(sub_id, substitute_name, quitter.gender, False, quitter.level)
    teams[home_tid].insert(idx, sub_id)
    log.append(
        f"substitute {substitute_name} registered as #{sub_id} and placed in team {home_tid}"
    )
    return players, teams, log
