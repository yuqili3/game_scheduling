#!/usr/bin/env python3
"""Initial team draw: append an initial_draw event, replay, validate, export snapshot.

The team structure (how many of each gender / role per team) comes from the
environment's config `players.team_composition`; the event records it so a
later config change cannot silently change the replayed roster.

Usage:
    python3 cli/draw_teams.py --seed 20260925 --env xd2026 --dry-run   # preview
    python3 cli/draw_teams.py --seed 20260925 --env xd2026             # write
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli._pre import add_common_args, commit, new_event, setup  # noqa: E402
from core import io_utils  # noqa: E402
from core.models import parse_seed, roster_fingerprint  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="initial team draw")
    ap.add_argument("--seed", type=parse_seed, required=True,
                    help="random seed: an integer or any text (logged with the event, reproducible)")
    add_common_args(ap)
    args = ap.parse_args()

    cfg, players = setup(args)
    if any(e.type in ("initial_draw", "assign_teams") for e in io_utils.read_events()):
        print("warning: the log already contains a team assignment; "
              "this draw will supersede it")

    params = io_utils.draw_params(cfg)
    ev = new_event(
        args,
        "initial_draw",
        {"num_teams": params["num_teams"],
         "team_composition": params["composition"].to_payload(),
         "balance_by_level": params["balance_by_level"],
         "assign_pairs": params["assign_pairs"],
         "roster_fingerprint": roster_fingerprint(players)},
        seed=args.seed,
    )
    commit(args, cfg, players, ev, f"draw complete seed={args.seed}")


if __name__ == "__main__":
    main()
