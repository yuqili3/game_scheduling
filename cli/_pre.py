"""Shared plumbing for the pre-match CLIs (draw / import / withdraw / substitute).

Every tool builds one event, replays the log *with that event appended in
memory*, validates the resulting roster against the config's team composition,
and only then either prints a preview (--dry-run) or appends the event to
events.jsonl and exports a dated snapshot."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils  # noqa: E402
from core.models import Event, Player, TournamentState, pair_label  # noqa: E402
from core.replay import replay  # noqa: E402


def add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--actor", default="organizer")
    ap.add_argument("--date", default=None, help="snapshot date YYYYMMDD, default today")
    ap.add_argument("--env", default=None,
                    help="data environment under data/ (default: GS_ENV or prod)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the resulting teams without writing the event or a snapshot")


def setup(args: argparse.Namespace) -> Tuple[dict, Dict[int, Player]]:
    if args.env:
        io_utils.set_env(args.env)
    cfg = io_utils.load_config()
    players = io_utils.load_players()
    return cfg, players


def new_event(args: argparse.Namespace, type_: str, payload: dict, seed=None) -> Event:
    return Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type=type_,
        actor=args.actor,
        payload=payload,
        seed=seed,
    )


def _tag(p: Player) -> str:
    tag = f"{p.name}"
    if p.is_captain:
        tag += " (C)"
    if p.level:
        tag += f" [{p.level}]"
    return tag


def print_teams(state: TournamentState) -> None:
    for tid in sorted(state.teams):
        cells = [_tag(state.players[m]) for m in state.teams[tid]]
        line = f"  team {tid}: " + ", ".join(cells)
        if state.pairs.get(tid):
            prs = " | ".join(
                f"{pair_label(tid, i)} = {_tag(state.players[a])} + {_tag(state.players[b])}"
                for i, (a, b) in enumerate(state.pairs[tid])
            )
            line += f"\n          pairs: {prs}"
        print(line)


def commit(
    args: argparse.Namespace, cfg: dict, players: Dict[int, Player], ev: Event, label: str,
    changelog_tail: int = 0,
) -> TournamentState:
    """Replay with `ev` appended; validate; then preview or persist."""
    params = io_utils.draw_params(cfg)
    events = io_utils.read_events() + [ev]
    state = replay(players, events, **params)
    state.validate(params["composition"])

    print(f"environment: {io_utils.ENV}  teams: {params['num_teams']} x "
          f"[{params['composition'].describe()}]")
    if args.dry_run:
        print(f"DRY RUN - {label}; nothing written")
    else:
        io_utils.append_event(ev)
        date_str = args.date or datetime.now().strftime("%Y%m%d")
        out = io_utils.snapshot_path(date_str)
        io_utils.export_teams_csv(state, out)
        print(f"{label}; event seq={ev.seq} appended, snapshot: {out}")
    if changelog_tail:
        print("changes:")
        for line in state.changelog[-changelog_tail:]:
            print("  " + line)
    print_teams(state)
    return state
