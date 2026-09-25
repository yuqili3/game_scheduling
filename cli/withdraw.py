#!/usr/bin/env python3
"""Pre-event withdrawal redraw (2026 melee rules): every other team gives up
one same-gender non-captain member, those plus the substitute are reshuffled
into the vacancies. Appends a withdraw_redraw event.

Usage:
    python3 cli/withdraw.py --withdrawn 33 --substitute "Sub Name" --seed 777
    python3 cli/withdraw.py --withdrawn 17 --new-captain 25 --substitute "Sub Name" --seed 778

For tournaments that forbid withdrawals after publication and fill seats
straight from a waitlist, use cli/substitute.py instead (no redraw).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli._pre import add_common_args, commit, new_event, setup  # noqa: E402
from core import io_utils  # noqa: E402
from core.models import parse_seed  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="withdrawal redraw")
    ap.add_argument("--withdrawn", type=int, required=True, help="withdrawing player id")
    ap.add_argument("--substitute", required=True, help="substitute name (entered by user)")
    ap.add_argument("--seed", type=parse_seed, required=True,
                    help="random seed: an integer or any text (entered by user, logged)")
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
    ev = new_event(args, "withdraw_redraw", payload, seed=args.seed)
    commit(args, cfg, players, ev, f"redraw complete seed={args.seed}",
           changelog_tail=2 * cfg["players"]["num_teams"] + 2)


if __name__ == "__main__":
    main()
