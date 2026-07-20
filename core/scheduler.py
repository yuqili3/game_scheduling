"""Scheduler: duration model + greedy planning. Pure functions; event-driven
re-planning = call plan() again after every event.

Rules:
- within a matchup: WD and MD1-3 run in parallel (rosters are disjoint by
  rule); the blind match waits for all four to finish plus rest_minutes
- across rounds (per-matchup granularity): a node starts as soon as both of
  its feeder matchups have finished (+ rest)
- courts are split into two banks of per_bank; greedy earliest-free within bank
- duration: base (WD/MD/XD) x short-handed scaling + strong-pair bonus;
  an undecided third game reserves its full duration (scheduling is the time
  bottleneck, so estimate worst-case); a 2:0 finish frees the slot on re-plan
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .matchday import MatchDayState


@dataclass(frozen=True)
class Slot:
    start: float  # minutes since session start
    end: float
    court: int
    node: str
    match_slot: str
    game: int  # 1/2; 3 may be conditional
    note: str = ""


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


def plan(md: MatchDayState, now: float = 0.0) -> List[Slot]:
    """Plan every unfinished game from `now` (minutes since session start).
    Deterministic greedy, no randomness."""
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
    slots: List[Slot] = []
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

    # process the 12 nodes in round order (stable id sort within a round
    # keeps the plan deterministic)
    for nid in sorted(md.nodes, key=lambda i: (md.nodes[i].round, i)):
        node = md.nodes[nid]
        if md.node_finished(nid):
            node_end[nid] = now
            continue
        ready = node_ready(nid)
        bank = banks[node.bank]
        match_end: Dict[str, float] = {}

        def schedule_match(slot: str, earliest: float) -> Optional[float]:
            games = _remaining_games(md, nid, slot)
            if not games:
                return None
            dur = game_minutes(md, nid, slot)
            court = min(bank, key=lambda c: (court_free[c], c))
            start = max(court_free[court], earliest)
            t = start
            for game_no, conditional in games:
                note = "game 3 (conditional)" if conditional else ""
                slots.append(Slot(t, t + dur, court, nid, slot, game_no, note))
                t += dur
            court_free[court] = t + changeover
            return t

        # first four matches run in parallel
        for slot in ("WD", "MD1", "MD2", "MD3"):
            end = schedule_match(slot, ready)
            if end is not None:
                match_end[slot] = end
        # blind match waits for all four + rest
        first_four_end = max(match_end.values()) if match_end else ready
        blind_ready = (first_four_end + rest) if match_end else ready
        end = schedule_match("BLIND", blind_ready)
        if end is not None:
            match_end["BLIND"] = end
        node_end[nid] = max(match_end.values()) if match_end else now

    return sorted(slots, key=lambda s: (s.start, s.court))


def utilization(slots: List[Slot], total_courts: int, now: float = 0.0) -> float:
    """Court utilization = occupied minutes / (courts x makespan)."""
    if not slots:
        return 0.0
    span = max(s.end for s in slots) - now
    used = sum(s.end - s.start for s in slots)
    return used / (total_courts * span) if span > 0 else 0.0
