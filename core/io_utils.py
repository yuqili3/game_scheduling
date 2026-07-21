"""IO layer (impure): config / roster / event log / CSV snapshot export.

Everything else in core stays pure; file and clock access live here and in cli/.

Environments: data lives under data/<env>/ — one self-contained directory per
tournament (config.yaml, players_*.csv, events.jsonl, tournament.db,
snapshots). The environment is selected by the GS_ENV environment variable
(default "prod"); "sim" is the rehearsal/simulation sandbox. This keeps
simulation data strictly apart from production and lets the same codebase
host future events (XD/WD/MD-only tournaments) as separate env directories.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .models import Event, Player, TournamentState

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"

ENV = os.environ.get("GS_ENV", "prod")


def set_env(env: str) -> None:
    """Switch the active data environment (e.g. from a CLI --env flag)."""
    global ENV
    ENV = env


def data_dir() -> Path:
    return DATA_ROOT / ENV


def events_path() -> Path:
    return data_dir() / "events.jsonl"


def config_path() -> Path:
    return data_dir() / "config.yaml"


def load_config(path: Optional[Path] = None) -> dict:
    with open(path or config_path(), encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_players(directory: Optional[Path] = None) -> Dict[int, Player]:
    directory = directory or data_dir()
    players: Dict[int, Player] = {}
    for name in ("players_female.csv", "players_male.csv"):
        with open(directory / name, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                p = Player(
                    id=int(row["id"]),
                    name=row["name"].strip(),
                    gender=row["gender"].strip(),
                    is_captain=row["is_captain"].strip().lower() == "true",
                )
                players[p.id] = p
    return players


def read_events(path: Optional[Path] = None) -> List[Event]:
    path = path or events_path()
    if not path.exists():
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(Event.from_dict(json.loads(line)))
    return events


def append_event(event: Event, path: Optional[Path] = None) -> None:
    path = path or events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def next_seq(path: Optional[Path] = None) -> int:
    events = read_events(path or events_path())
    return (events[-1].seq + 1) if events else 1


def snapshot_path(date_str: str, directory: Optional[Path] = None) -> Path:
    """teams_YYYYMMDD.csv; later snapshots on the same day get _2/_3 suffixes."""
    directory = directory or data_dir()
    base = directory / f"teams_{date_str}.csv"
    if not base.exists():
        return base
    k = 2
    while (directory / f"teams_{date_str}_{k}.csv").exists():
        k += 1
    return directory / f"teams_{date_str}_{k}.csv"


def export_teams_csv(state: TournamentState, out_path: Path) -> None:
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "gender", "role", "team_id", "captain_name"])
        for tid in sorted(state.teams):
            members = state.teams[tid]
            captain = state.players[members[0]]
            for pid in members:
                p = state.players[pid]
                w.writerow(
                    [p.id, p.name, p.gender, "captain" if p.is_captain else "member",
                     tid, captain.name]
                )
