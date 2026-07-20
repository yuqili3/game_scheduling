"""Scheduler: duration model + template-aligned list scheduling. Pure functions;
event-driven re-planning = call plan() again after every event.

Scheduling follows the venue's court-allocation template (tournament PDF
appendix) rather than naive greedy serialization:

- within a matchup, WD and MD1-3 play their first two games as contiguous
  blocks on parallel courts (rosters are disjoint by rule);
- the blind match becomes ready as soon as those four matches have finished
  their first two games (+ rest), NOT when they are fully decided — possible
  third games are deferred and filled into whatever courts are free, exactly
  like the "3rd, TBD" slots of the template. This is what lifts utilization
  over a plain greedy plan;
- across rounds (per-matchup granularity): a node starts once both feeder
  matchups have finished (+ rest);
- courts are split into two banks of per_bank; within a bank, free courts are
  assigned to the ready unit that can start earliest (ties broken by
  main-before-third, matchup order, slot order);
- duration: base (WD/MD/XD) x short-handed scaling + strong-pair bonus; an
  undecided third game reserves its full duration (worst case) and is
  released by re-planning when a match ends 2:0.

Planning note: a conditional third game may overlap the blind match on other
courts even though they could share a player; like the template's TBD slots,
actual conflicts resolve at re-plan time when real results arrive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .matchday import MatchDayState

FIRST_FOUR = ("WD", "MD1", "MD2", "MD3")


@dataclass(frozen=True)
class Slot:
    start: float  # minutes since session start
    end: float
    court: int
    node: str
    match_slot: str
    game: int  # 1/2; 3 may be conditional
    note: str = ""


@dataclass
class _Unit:
    """A contiguous block of games of one match assigned to one court."""

    node: str
    slot: str
    kind: str  # "main" (games 1-2) or "third"
    games: List[Tuple[int, bool]]  # (game_no, conditional)
    deps: List[Tuple[str, str, str]] = field(default_factory=list)
    base_ready: float = 0.0
    rest_after_deps: bool = False
    prio: Tuple[int, int, int] = (0, 0, 0)

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.node, self.slot, self.kind)


def game_minutes(md: MatchDayState, node_id: str, slot: str) -> float:
    """Estimated duration of one game, in minutes."""
    d = md.cfg["duration"]
    m = md.matches.get((node_id, slot))
    category = m.category if m else (
        "WD" if slot == "WD"
        else md.cfg["format"]["blind_match_types"][md.nodes[node_id].round - 1]
        if slot == "BLIND"
        else "MD"
    )
    base = float(d[f"{category.lower()}_game_minutes"])
    # short-handed matchup -> scale by reduced points
    if d.get("scale_by_points", True):
        points = md.points_for(node_id)
        base = base * points / md.cfg["format"]["points_per_game"]
    # strong-vs-strong bonus (both lineups must be known)
    if m and m.a and m.b:
        round_no = md.nodes[node_id].round
        if md.pair_strong(m.a, round_no) and md.pair_strong(m.b, round_no):
            base += float(d["strong_pair_bonus"])
    return base


def _remaining_games(md: MatchDayState, node_id: str, slot: str) -> List[Tuple[int, bool]]:
    """Unfinished games: [(game_no, is_conditional)].

    The third game reserves a full-length slot even before we know whether it
    will happen — the schedule is a time bottleneck and must be worst-case.
    When a match actually ends 2:0, re-planning releases the slot.
    """
    m = md.matches.get((node_id, slot))
    played = len(m.games) if m else 0
    if m and m.winner() is not None:
        return []
    games_per_match = md.cfg["format"]["games_per_match"]
    out: List[Tuple[int, bool]] = []
    for g in range(played + 1, games_per_match + 1):
        certain = g < games_per_match or (
            m is not None and len(m.games) == games_per_match - 1
        )
        out.append((g, not certain))
    return out


def _node_units(md: MatchDayState, nid: str, ready: float, order: int) -> List[_Unit]:
    """Split a matchup's remaining games into schedulable units with
    template-style dependencies."""
    games_per_match = md.cfg["format"]["games_per_match"]
    units: List[_Unit] = []
    main_keys: List[Tuple[str, str, str]] = []

    for si, slot in enumerate(FIRST_FOUR):
        rem = _remaining_games(md, nid, slot)
        main = [g for g in rem if g[0] < games_per_match]
        third = [g for g in rem if g[0] == games_per_match]
        if main:
            u = _Unit(nid, slot, "main", main, [], ready, False, (0, order, si))
            units.append(u)
            main_keys.append(u.key)
        if third:
            deps = [(nid, slot, "main")] if main else []
            units.append(_Unit(nid, slot, "third", third, deps, ready, False, (2, order, si)))

    rem = _remaining_games(md, nid, "BLIND")
    bmain = [g for g in rem if g[0] < games_per_match]
    bthird = [g for g in rem if g[0] == games_per_match]
    if bmain:
        # ready once the first-four matches finish their first two games + rest
        units.append(_Unit(nid, "BLIND", "main", bmain, list(main_keys), ready,
                           bool(main_keys), (1, order, 4)))
    if bthird:
        deps = [(nid, "BLIND", "main")] if bmain else list(main_keys)
        units.append(_Unit(nid, "BLIND", "third", bthird, deps, ready,
                           bool(not bmain and main_keys), (3, order, 4)))
    return units


def plan(md: MatchDayState, now: float = 0.0) -> List[Slot]:
    """Plan every unfinished game from `now` (minutes since session start).
    Deterministic template-aligned list scheduling, no randomness."""
    cfg = md.cfg
    per_bank = cfg["courts"]["per_bank"]
    total = cfg["courts"]["total"]
    rest = float(cfg["duration"]["rest_minutes"])
    changeover = float(cfg["duration"]["changeover_minutes"])
    banks = {
        0: list(range(1, per_bank + 1)),
        1: list(range(per_bank + 1, total + 1)),
    }
    court_free: Dict[int, float] = {c: now for c in range(1, total + 1)}
    slots_out: List[Slot] = []
    node_end: Dict[str, float] = {}

    def node_ready(nid: str) -> float:
        node = md.nodes[nid]
        if node.round == 1:
            return now
        t = now
        for feeder in node.feeders:
            fid = feeder[0]
            if md.node_finished(fid):
                t = max(t, now)
            else:
                t = max(t, node_end.get(fid, now) + rest)
        return t

    rounds = sorted({n.round for n in md.nodes.values()})
    for round_no in rounds:
        for bank_id in (0, 1):
            nids = sorted(
                n for n, node in md.nodes.items()
                if node.round == round_no and node.bank == bank_id
            )
            bank = banks[bank_id]
            units: List[_Unit] = []
            for order, nid in enumerate(nids):
                if md.node_finished(nid):
                    node_end[nid] = now
                    continue
                units += _node_units(md, nid, node_ready(nid), order)

            ends: Dict[Tuple[str, str, str], float] = {}
            pending = list(units)
            while pending:
                # units whose dependencies are all scheduled
                candidates = []
                for u in pending:
                    if not all(d in ends for d in u.deps):
                        continue
                    ready = u.base_ready
                    if u.deps:
                        dep_end = max(ends[d] for d in u.deps)
                        ready = max(ready, dep_end + (rest if u.rest_after_deps else 0.0))
                    candidates.append((ready, u))
                court = min(bank, key=lambda c: (court_free[c], c))
                # the unit that can actually start earliest wins;
                # ties: main before third, matchup order, slot order
                ready, u = min(
                    candidates,
                    key=lambda x: (max(court_free[court], x[0]), x[1].prio),
                )
                start = max(court_free[court], ready)
                dur = game_minutes(md, u.node, u.slot)
                t = start
                for game_no, conditional in u.games:
                    note = "game 3 (conditional)" if conditional else ""
                    slots_out.append(Slot(t, t + dur, court, u.node, u.slot, game_no, note))
                    t += dur
                court_free[court] = t + changeover
                ends[u.key] = t
                pending.remove(u)

            for nid in nids:
                if md.node_finished(nid):
                    continue
                unit_ends = [e for k, e in ends.items() if k[0] == nid]
                node_end[nid] = max(unit_ends) if unit_ends else now

    return sorted(slots_out, key=lambda s: (s.start, s.court))


def utilization(slots: List[Slot], total_courts: int, now: float = 0.0) -> float:
    """Court utilization = occupied minutes / (courts x makespan)."""
    if not slots:
        return 0.0
    span = max(s.end for s in slots) - now
    used = sum(s.end - s.start for s in slots)
    return used / (total_courts * span) if span > 0 else 0.0
