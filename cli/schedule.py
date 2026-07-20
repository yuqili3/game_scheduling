#!/usr/bin/env python3
"""Match-day schedule view: replay events, print the court/time plan, bracket
status and live ranking.

Usage:
    python3 cli/schedule.py [--now 45]   # now = minutes since session start
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils, rules
from core.matchday import replay_matchday
from core.replay import PRE_MATCH_EVENTS, replay
from core.scheduler import plan, utilization


def fmt_t(minutes: float, start: str) -> str:
    h, m = int(start[:2]), int(start[3:])
    total = h * 60 + m + int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="match-day schedule")
    ap.add_argument("--now", type=float, default=0.0, help="minutes since session start")
    args = ap.parse_args()

    cfg = io_utils.load_config()
    session_start = cfg["broadcast"].get("session_start", "17:10")
    players = io_utils.load_players()
    events = io_utils.read_events()
    base = replay(players, [e for e in events if e.type in PRE_MATCH_EVENTS],
                  cfg["players"]["num_teams"])
    md = replay_matchday(base, events, cfg)

    if not md.group_of:
        print("no group_draw event yet; planning by G labels:")

    slots = plan(md, now=args.now)
    print(f"\n=== schedule ({len(slots)} slots, now={args.now}min) ===")
    print(f"{'start':>6} {'end':>6} {'court':>5}  {'node':<7} {'match':<6} game  note")
    for s in slots:
        print(
            f"{fmt_t(s.start, session_start):>6} {fmt_t(s.end, session_start):>6} "
            f"{s.court:>5}  {s.node:<7} {s.match_slot:<6} {s.game}     {s.note}"
        )
    print(f"\ncourt utilization: {utilization(slots, cfg['courts']['total'], args.now):.1%}")

    winners = md.node_winners()
    print("\n=== bracket ===")
    for nid in sorted(md.nodes, key=lambda i: (md.nodes[i].round, i)):
        gs = md.node_groups(nid)
        teams = md.node_teams(nid)
        desc = f"{gs[0]} vs {gs[1]}" if gs else "TBD"
        if teams:
            desc += f" (team {teams[0]} vs team {teams[1]})"
        status = f"winner {winners[nid]}" if nid in winners else (
            "finished" if md.node_finished(nid) else "pending")
        print(f"  {nid:<7} [{md.nodes[nid].tag}] {desc:<34} {status}")

    ranking = rules.final_ranking(winners, md.nodes)
    if ranking:
        print("\n=== current ranking ===")
        for place in sorted(ranking):
            g = ranking[place]
            tid = md.group_of.get(g)
            print(f"  #{place}: {g}" + (f" (team {tid})" if tid else ""))


if __name__ == "__main__":
    main()
