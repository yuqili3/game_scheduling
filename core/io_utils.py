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

from .models import Event, Player, TeamComposition, TournamentState, pair_label

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


def _optional(row: dict, key: str) -> Optional[str]:
    v = row.get(key)
    if v is None:
        return None
    v = v.strip()
    return v or None


def load_players(directory: Optional[Path] = None) -> Dict[int, Player]:
    """Read every players*.csv in the environment directory.

    Required columns: id, name, gender. Optional: is_captain (default false),
    level (skill tier label; blank = untiered) and fixed_team (team id the
    player is pinned to before the draw; blank = drawn normally)."""
    directory = directory or data_dir()
    files = sorted(directory.glob("players*.csv"))
    if not files:
        raise FileNotFoundError(f"no players*.csv in {directory}")
    players: Dict[int, Player] = {}
    for path in files:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pid = int(row["id"])
                if pid in players:
                    raise ValueError(f"duplicate player id {pid} ({path.name})")
                players[pid] = Player(
                    id=pid,
                    name=row["name"].strip(),
                    gender=row["gender"].strip().upper(),
                    is_captain=(_optional(row, "is_captain") or "false").lower() == "true",
                    level=_optional(row, "level"),
                    fixed_team=int(_optional(row, "fixed_team")) if _optional(row, "fixed_team") else None,
                )
    return players


def draw_params(cfg: dict) -> dict:
    """Keyword arguments for replay() from a config: number of teams, the
    TeamComposition, the level-balancing switch and whether mixed-doubles
    pairs are drawn along with the teams."""
    pc = cfg["players"]
    return {
        "num_teams": int(pc["num_teams"]),
        "composition": TeamComposition.from_config(pc),
        "balance_by_level": bool(pc.get("balance_by_level", True)),
        "assign_pairs": bool(pc.get("assign_pairs", False)),
    }


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
        w.writerow(["id", "name", "gender", "level", "role", "team_id", "captain_name", "pair"])
        for tid in sorted(state.teams):
            captain = state.captain_of(tid)
            pair_of = {pid: pair_label(tid, i) for i, pr in enumerate(state.pairs.get(tid, [])) for pid in pr}
            for pid in state.teams[tid]:
                p = state.players[pid]
                w.writerow(
                    [p.id, p.name, p.gender, p.level or "", p.role, tid,
                     captain.name if captain else "", pair_of.get(pid, "")]
                )
