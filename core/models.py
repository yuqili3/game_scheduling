"""Data models: pure data structures, no IO / clock / global randomness."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    gender: str  # "M" / "F"
    is_captain: bool = False


@dataclass(frozen=True)
class Event:
    """One line of the event log. All state is the result of replaying events."""

    seq: int
    ts: str
    type: str
    actor: str
    payload: dict
    seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "actor": self.actor,
            "payload": self.payload,
            "seed": self.seed,
        }

    @staticmethod
    def from_dict(d: dict) -> "Event":
        return Event(
            seq=int(d["seq"]),
            ts=d["ts"],
            type=d["type"],
            actor=d.get("actor", ""),
            payload=d.get("payload", {}),
            seed=d.get("seed"),
        )


@dataclass
class TournamentState:
    players: Dict[int, Player] = field(default_factory=dict)
    # team id -> member ids, captain always first
    teams: Dict[int, List[int]] = field(default_factory=dict)
    changelog: List[str] = field(default_factory=list)

    def team_of(self, pid: int) -> Optional[int]:
        for tid, members in self.teams.items():
            if pid in members:
                return tid
        return None

    def validate(self, females_per_team: int = 2, males_per_team: int = 6) -> None:
        """Each team: exactly 1 captain, males_per_team males (captain included),
        females_per_team females; nobody on two teams."""
        seen: List[int] = []
        for tid, members in self.teams.items():
            ps = [self.players[m] for m in members]
            captains = [p for p in ps if p.is_captain]
            males = [p for p in ps if p.gender == "M"]
            females = [p for p in ps if p.gender == "F"]
            if len(captains) != 1:
                raise ValueError(f"team {tid} has {len(captains)} captains, expected 1")
            if len(males) != males_per_team or len(females) != females_per_team:
                raise ValueError(
                    f"team {tid} malformed: {len(males)}/{males_per_team} males, "
                    f"{len(females)}/{females_per_team} females"
                )
            if not self.players[members[0]].is_captain:
                raise ValueError(f"team {tid} first member is not the captain")
            seen += members
        if len(seen) != len(set(seen)):
            raise ValueError("some player appears on two teams")
