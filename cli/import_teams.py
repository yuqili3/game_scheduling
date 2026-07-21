#!/usr/bin/env python3
"""Import an offline-published team assignment (e.g. the PDF roster):
appends an assign_teams event.

Reads players.csv at the repo root (columns: team_id,team_captain,name,role,gender),
matches names against the initial roster, writes the event and exports a snapshot.

Usage:
    python3 cli/import_teams.py [--roster players.csv] [--actor organizer] [--date 20260612]
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils
from core.models import Event
from core.replay import replay


def main() -> None:
    ap = argparse.ArgumentParser(description="import offline team assignment")
    ap.add_argument("--roster", default=str(io_utils.REPO_ROOT / "players.csv"))
    ap.add_argument("--actor", default="organizer")
    ap.add_argument("--date", default=None)
    ap.add_argument("--env", default=None,
                    help="data environment under data/ (default: GS_ENV or prod)")
    args = ap.parse_args()
    if args.env:
        io_utils.set_env(args.env)


    cfg = io_utils.load_config()
    players = io_utils.load_players()
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
            if row["role"].strip() == "captain":
                teams.setdefault(tid, []).insert(0, pid)
            else:
                teams.setdefault(tid, []).append(pid)

    ev = Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type="assign_teams",
        actor=args.actor,
        payload={"teams": {str(t): ms for t, ms in sorted(teams.items())}},
        seed=None,
    )
    io_utils.append_event(ev)

    state = replay(players, io_utils.read_events(), cfg["players"]["num_teams"])
    state.validate(cfg["players"]["females_per_team"], cfg["players"]["males_per_team"])

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    out = io_utils.snapshot_path(date_str)
    io_utils.export_teams_csv(state, out)
    print(f"import complete, {len(teams)} teams, snapshot: {out}")


if __name__ == "__main__":
    main()
