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


def env_badge() -> None:
    """Make the active data environment unmistakable on every page."""
    if io_utils.ENV != "prod":
        st.warning(f"SIMULATION environment (GS_ENV={io_utils.ENV}) — "
                   "not live tournament data", icon="🧪")


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
    """Returns (status, slot) where status is idle/warmup/game1/game2/game3.

    A host-confirmed (match_started) match in play on this court takes
    priority over the clock-derived plan."""
    from core.scheduler import game_minutes

    confirmed = md.started_on_court(court)
    if confirmed is not None:
        nid, mslot = confirmed
        m = md.matches.get((nid, mslot))
        game_no = len(m.games) + 1 if m else 1
        dur = game_minutes(md, nid, mslot)
        s = Slot(now, now + dur, court, nid, mslot, game_no, "in play (confirmed)")
        return f"game{min(game_no, 3)}", s
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


STATUS_LABEL = {
    "idle": "Idle",
    "warmup": "Warm-up",
    "game1": "Game 1",
    "game2": "Game 2",
    "game3": "Game 3",
}


def render_court_grid(cfg: dict, md: MatchDayState, slots: List[Slot], now: float) -> None:
    """The color-coded court grid, shared by the spectator and score-entry pages."""
    colors = cfg["visual"]["court_colors"]
    total = cfg["courts"]["total"]
    for row_start in range(1, total + 1, 5):
        cols = st.columns(5)
        for i, court in enumerate(range(row_start, min(row_start + 5, total + 1))):
            status, slot = court_status(md, slots, court, now)
            color = colors[status]
            with cols[i]:
                if slot is not None:
                    d = match_desc(md, slot)
                    when = (
                        "in play"
                        if status.startswith("game")
                        else f"next {fmt_clock(slot.start, cfg)}"
                    )
                    body = (
                        f"<b>{d['matchup']}</b><br>{d['match']} · game {d['game']} · "
                        f"to {d['points']} pts<br>{d['players']}<br>score: {d['score']}<br>{when}"
                    )
                else:
                    body = "no matches planned"
                st.markdown(
                    f"""<div style="background:{color};border-radius:10px;padding:10px;
                    min-height:150px;color:#111;font-size:0.82rem;line-height:1.35">
                    <b>Court {court}</b> — {STATUS_LABEL[status]}<br>{body}</div>""",
                    unsafe_allow_html=True,
                )


def render_bracket(cfg: dict, md: MatchDayState, slots: List[Slot]) -> None:
    st.subheader("Bracket")
    winners = md.node_winners()
    cols = st.columns(3)
    for r, col in zip((1, 2, 3), cols):
        with col:
            st.markdown(f"**Round {r}**")
            for nid in sorted(n for n in md.nodes if md.nodes[n].round == r):
                gs = md.node_groups(nid)
                desc = (f"{team_label(md, gs[0])} vs {team_label(md, gs[1])}"
                        if gs else "TBD")
                if nid in winners:
                    status = f"✅ winner {winners[nid]}"
                elif any(k[0] == nid and m.games for k, m in md.matches.items()):
                    status = "🟠 in progress"
                else:
                    starts = [s.start for s in slots if s.node == nid]
                    status = f"est. {fmt_clock(min(starts), cfg)}" if starts else "pending"
                st.markdown(f"- `{nid}` [{md.nodes[nid].tag}] {desc} — {status}")


def render_ranking(md: MatchDayState) -> None:
    from core import rules

    ranking = rules.final_ranking(md.node_winners(), md.nodes)
    if ranking:
        st.subheader("Ranking")
        st.table(
            [{"place": p, "group": ranking[p], "team": team_label(md, ranking[p])}
             for p in sorted(ranking)]
        )


def render_blind_board(md: MatchDayState) -> None:
    blind = [(nid, m) for (nid, s), m in md.matches.items()
             if s == "BLIND" and (m.a or m.b)]
    if blind:
        st.subheader("Blind draw results")
        for nid, m in sorted(blind):
            gs = md.node_groups(nid)
            a = ", ".join(md.base.players[p].name for p in m.a) or "TBD"
            b = ", ".join(md.base.players[p].name for p in m.b) or "TBD"
            st.markdown(f"- `{nid}` ({m.category}): {gs[0] if gs else '?'}: {a} — "
                        f"{gs[1] if gs else '?'}: {b}")


def render_next_up(cfg: dict, md: MatchDayState, slots: List[Slot], now: float,
                   limit: int = 12) -> None:
    st.subheader("Next up")
    upcoming = [s for s in slots if s.start >= now][:limit]
    if upcoming:
        st.table(
            [{"time": fmt_clock(s.start, cfg), "court": s.court, "node": s.node,
              "match": s.match_slot, "game": s.game,
              "players": match_desc(md, s)["players"]}
             for s in upcoming]
        )
    else:
        st.write("Nothing left to play 🎉")


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
