#!/usr/bin/env python3
"""Withdrawal redraw: append a withdraw_redraw event, replay, validate, export
a date-stamped snapshot.

Can be run multiple times; each run must supply an explicit seed, and the same
seed always reproduces the same result.

Usage:
    python3 cli/withdraw.py --withdrawn 33 --substitute "Xin Wang" --seed 777
    python3 cli/withdraw.py --withdrawn 17 --new-captain 25 --substitute "Xin Wang" --seed 778
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
    ap = argparse.ArgumentParser(description="withdrawal redraw")
    ap.add_argument("--withdrawn", type=int, required=True, help="withdrawing player id")
    ap.add_argument("--substitute", required=True, help="substitute name (entered by user)")
    ap.add_argument("--seed", type=int, required=True,
                    help="random seed (entered by user, logged)")
    ap.add_argument("--new-captain", type=int, default=None,
                    help="required when the withdrawing player is a captain: successor id")
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
    if not any(e.type in ("initial_draw", "assign_teams") for e in events):
        raise SystemExit("no team assignment yet; run draw_teams.py or import_teams.py first")

    payload = {"withdrawn_id": args.withdrawn, "substitute_name": args.substitute}
    if args.new_captain is not None:
        payload["new_captain_id"] = args.new_captain
    ev = Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type="withdraw_redraw",
        actor=args.actor,
        payload=payload,
        seed=args.seed,
    )
    io_utils.append_event(ev)

    state = replay(players, io_utils.read_events(), cfg["players"]["num_teams"])
    state.validate(cfg["players"]["females_per_team"], cfg["players"]["males_per_team"])

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    out = io_utils.snapshot_path(date_str)
    io_utils.export_teams_csv(state, out)

    print(f"redraw complete seed={args.seed}, snapshot: {out}")
    print("changes:")
    for line in state.changelog[-18:]:
        print("  " + line)


if __name__ == "__main__":
    main()
