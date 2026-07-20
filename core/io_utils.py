"""IO layer (impure): config / roster / event log / CSV snapshot export.

Everything else in core stays pure; file and clock access live here and in cli/.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List

import yaml

from .models import Event, Player, TournamentState

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
EVENTS_PATH = DATA_DIR / "events.jsonl"


def load_config(path: Path = REPO_ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_players(data_dir: Path = DATA_DIR) -> Dict[int, Player]:
    players: Dict[int, Player] = {}
    for name in ("players_female.csv", "players_male.csv"):
        with open(data_dir / name, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                p = Player(
                    id=int(row["id"]),
                    name=row["name"].strip(),
                    gender=row["gender"].strip(),
                    is_captain=row["is_captain"].strip().lower() == "true",
                )
                players[p.id] = p
    return players


def read_events(path: Path = EVENTS_PATH) -> List[Event]:
    if not path.exists():
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(Event.from_dict(json.loads(line)))
    return events


def append_event(event: Event, path: Path = EVENTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def next_seq(path: Path = EVENTS_PATH) -> int:
    events = read_events(path)
    return (events[-1].seq + 1) if events else 1


def snapshot_path(date_str: str, data_dir: Path = DATA_DIR) -> Path:
    """teams_YYYYMMDD.csv; later snapshots on the same day get _2/_3 suffixes."""
    base = data_dir / f"teams_{date_str}.csv"
    if not base.exists():
        return base
    k = 2
    while (data_dir / f"teams_{date_str}_{k}.csv").exists():
        k += 1
    return data_dir / f"teams_{date_str}_{k}.csv"


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
