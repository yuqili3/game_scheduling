#!/usr/bin/env python3
"""Direct waitlist substitution (no randomness): the substitute takes the
withdrawn player's seat in the same team. Appends a substitute_direct event.

Intended for tournaments whose rules say "no withdrawals after the draw is
published; emergencies are filled from the waitlist in order" (2026 mixed
doubles friendly). For the melee's 7+1 redraw rule use cli/withdraw.py.

Usage:
    python3 cli/substitute.py --env xd2026 --withdrawn 7 --substitute "Sub Name" [--dry-run]
    python3 cli/substitute.py --withdrawn 17 --new-captain 25 --substitute "Sub Name"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli._pre import add_common_args, commit, new_event, setup  # noqa: E402
from core import io_utils  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="direct waitlist substitution")
    ap.add_argument("--withdrawn", type=int, required=True, help="withdrawing player id")
    ap.add_argument("--substitute", required=True, help="substitute name (from the waitlist)")
    ap.add_argument("--new-captain", type=int, default=None,
                    help="required when the withdrawing player is a captain: successor id")
    add_common_args(ap)
    args = ap.parse_args()

    cfg, players = setup(args)
    if not any(e.type in ("initial_draw", "assign_teams") for e in io_utils.read_events()):
        raise SystemExit("no team assignment yet; run draw_teams.py or import_teams.py first")

    payload = {"withdrawn_id": args.withdrawn, "substitute_name": args.substitute}
    if args.new_captain is not None:
        payload["new_captain_id"] = args.new_captain
    ev = new_event(args, "substitute_direct", payload)
    commit(args, cfg, players, ev, "substitution complete", changelog_tail=3)


if __name__ == "__main__":
    main()
