#!/usr/bin/env python3
"""Initial team draw: append an initial_draw event, replay, validate, export snapshot.

Usage:
    python3 cli/draw_teams.py --seed 20260719 [--actor organizer] [--date 20260612]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils
from core.models import Event
from core.replay import replay


def main() -> None:
    ap = argparse.ArgumentParser(description="initial team draw")
    ap.add_argument("--seed", type=int, required=True,
                    help="random seed (logged with the event, reproducible)")
    ap.add_argument("--actor", default="organizer")
    ap.add_argument("--date", default=None, help="snapshot date YYYYMMDD, default today")
    ap.add_argument("--env", default=None,
                    help="data environment under data/ (default: GS_ENV or prod)")
    args = ap.parse_args()
    if args.env:
        io_utils.set_env(args.env)


    cfg = io_utils.load_config()
    players = io_utils.load_players()
    events = io_utils.read_events()
    if any(e.type in ("initial_draw", "assign_teams") for e in events):
        print("warning: the log already contains a team assignment; "
              "this draw will supersede it")

    ev = Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type="initial_draw",
        actor=args.actor,
        payload={},
        seed=args.seed,
    )
    io_utils.append_event(ev)

    state = replay(players, io_utils.read_events(), cfg["players"]["num_teams"])
    state.validate(cfg["players"]["females_per_team"], cfg["players"]["males_per_team"])

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    out = io_utils.snapshot_path(date_str)
    io_utils.export_teams_csv(state, out)

    print(f"draw complete seed={args.seed}, snapshot: {out}")
    for tid in sorted(state.teams):
        names = [state.players[m].name for m in state.teams[tid]]
        print(f"  team {tid}: captain {names[0]} | " + ", ".join(names[1:]))


if __name__ == "__main__":
    main()
