#!/usr/bin/env python3
"""Import an offline-published team assignment (e.g. the PDF roster):
appends an assign_teams event.

Reads a roster CSV (columns: team_id, name, role[, team_captain, gender]),
matches names against the initial player tables of the environment, writes
the event and exports a snapshot. Works for any team composition; `role` is
"captain" or "member" (no captains at all is fine).

Usage:
    python3 cli/import_teams.py [--roster players.csv] [--env xd2026] [--date 20260925] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli._pre import add_common_args, commit, new_event, setup  # noqa: E402
from core import io_utils  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="import offline team assignment")
    ap.add_argument("--roster", default=str(io_utils.REPO_ROOT / "players.csv"))
    add_common_args(ap)
    args = ap.parse_args()

    cfg, players = setup(args)
    by_name = {p.name: p.id for p in players.values()}
    if len(by_name) != len(players):
        raise SystemExit("initial roster has duplicate names; cannot import by name")

    teams: dict = {}
    with open(args.roster, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            if name not in by_name:
                raise SystemExit(f"{name} from the roster is not in the initial player tables")
            tid = int(row["team_id"])
            pid = by_name[name]
            # captain goes first
            if (row.get("role") or "").strip() == "captain":
                teams.setdefault(tid, []).insert(0, pid)
            else:
                teams.setdefault(tid, []).append(pid)

    ev = new_event(
        args, "assign_teams", {"teams": {str(t): ms for t, ms in sorted(teams.items())}}
    )
    commit(args, cfg, players, ev, f"import complete, {len(teams)} teams")


if __name__ == "__main__":
    main()
