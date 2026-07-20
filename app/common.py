"""Shared helpers for the Streamlit pages."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils  # noqa: E402
from core.eventstore import EventStore  # noqa: E402
from core.matchday import MatchDayState, replay_matchday  # noqa: E402
from core.replay import PRE_MATCH_EVENTS, replay  # noqa: E402
from core.scheduler import Slot, plan  # noqa: E402


@st.cache_resource
def get_store() -> EventStore:
    return EventStore()


def load_state() -> Tuple[dict, MatchDayState, List[Slot], float]:
    """Config + match-day state + current plan + minutes since session start."""
    cfg = io_utils.load_config()
    players = io_utils.load_players()
    events = get_store().events()
    base = replay(
        players,
        [e for e in events if e.type in PRE_MATCH_EVENTS],
        cfg["players"]["num_teams"],
    )
    md = replay_matchday(base, events, cfg)
    now = now_minutes(cfg)
    return cfg, md, plan(md, now=max(now, 0.0)), now


def now_minutes(cfg: dict) -> float:
    """Wall-clock minutes since the configured session start (can be negative)."""
    start = cfg["broadcast"].get("session_start", "17:10")
    now = datetime.now()
    session = now.replace(hour=int(start[:2]), minute=int(start[3:]), second=0, microsecond=0)
    return (now - session).total_seconds() / 60.0


def fmt_clock(minutes: float, cfg: dict) -> str:
    start = cfg["broadcast"].get("session_start", "17:10")
    total = int(start[:2]) * 60 + int(start[3:]) + int(round(minutes))
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def names(md: MatchDayState, pids: List[int]) -> str:
    return " / ".join(md.base.players[p].name for p in pids) if pids else "TBD"


def team_label(md: MatchDayState, g: str) -> str:
    tid = md.group_of.get(g)
    if tid is None:
        return g
    captain = md.base.players[md.base.teams[tid][0]].name
    return f"{g} (T{tid} {captain})"


def court_status(
    md: MatchDayState, slots: List[Slot], court: int, now: float, warmup_lookahead: float = 10.0
) -> Tuple[str, Optional[Slot]]:
    """Returns (status, slot) where status is idle/warmup/game1/game2/game3."""
    active = [s for s in slots if s.court == court and s.start <= now < s.end]
    if active:
        s = active[0]
        return f"game{min(s.game, 3)}", s
    upcoming = sorted(
        (s for s in slots if s.court == court and s.start >= now), key=lambda s: s.start
    )
    if upcoming and upcoming[0].start - now <= warmup_lookahead:
        return "warmup", upcoming[0]
    return "idle", upcoming[0] if upcoming else None


def match_desc(md: MatchDayState, slot: Slot) -> Dict[str, str]:
    """Displayable details of the match a slot belongs to."""
    gs = md.node_groups(slot.node)
    m = md.matches.get((slot.node, slot.match_slot))
    side_a = names(md, m.a) if m else "TBD"
    side_b = names(md, m.b) if m else "TBD"
    score = "  ".join(f"{a}:{b}" for a, b in m.games) if m and m.games else "-"
    return {
        "matchup": f"{team_label(md, gs[0])} vs {team_label(md, gs[1])}" if gs else "TBD",
        "match": slot.match_slot,
        "game": str(slot.game),
        "players": f"{side_a}  vs  {side_b}",
        "score": score,
        "points": str(md.points_for(slot.node)),
    }
