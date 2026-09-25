"""Data models: pure data structures, no IO / clock / global randomness."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

GENDERS = ("M", "F")
ROLES = ("captain", "member")

# A random seed is an int or any text. Both feed random.Random(seed) directly;
# string seeding uses SHA-512 internally, so it is stable across machines and
# independent of PYTHONHASHSEED.
Seed = Union[int, str]
_INT_LITERAL = re.compile(r"^[+-]?\d+$")


def parse_seed(text: Union[Seed, None]) -> Seed:
    """Normalise a user-entered seed. Integer literals (after stripping
    whitespace) become int so that 20260925 and "20260925" are the same seed;
    anything else stays a str. Empty input is an error."""
    if isinstance(text, bool):  # bool is an int subclass; never meant as a seed
        raise ValueError("seed must be an integer or text")
    if isinstance(text, int):
        return text
    if text is None:
        raise ValueError("seed is required")
    s = str(text).strip()
    if not s:
        raise ValueError("seed must not be empty")
    if _INT_LITERAL.match(s):
        return int(s)
    return s


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    gender: str  # "M" / "F"
    is_captain: bool = False
    level: Optional[str] = None  # optional skill tier (e.g. "A"/"B"/"C"); None = untiered
    fixed_team: Optional[int] = None  # pinned to this team before the draw (e.g. appointed captains)

    @property
    def role(self) -> str:
        return "captain" if self.is_captain else "member"


def team_letter(tid: int) -> str:
    """Team id 1..26 -> A..Z, then AA, AB, ... (spreadsheet style)."""
    if tid < 1:
        raise ValueError("team ids start at 1")
    s = ""
    n = tid
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def pair_label(tid: int, index: int) -> str:
    """Announced name of a team's mixed-doubles pair: team letter + 1-based
    pair number, e.g. team 1 -> A1/A2, team 2 -> B1/B2 (as in the PDF)."""
    return f"{team_letter(tid)}{index + 1}"


def roster_fingerprint(players: Dict[int, "Player"]) -> str:
    """Short hash of everything the draw depends on (id, gender, role, level,
    fixed team of every player). Stored in initial_draw events so a later edit
    of the players*.csv cannot silently change the replayed teams."""
    h = hashlib.sha256()
    for pid in sorted(players):
        p = players[pid]
        fixed = "" if p.fixed_team is None else str(p.fixed_team)
        h.update(f"{pid}|{p.gender}|{p.role}|{p.level or ''}|{fixed}\n".encode("utf-8"))
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class Slot:
    """One block of a team's structure: `count` players of `gender` with `role`.

    A team composition is an ordered list of slots; the draw deals one slot
    at a time in that order, so putting the captain slot first keeps the
    captain at index 0 of every team.
    """

    gender: str
    count: int
    role: str = "member"

    def __post_init__(self) -> None:
        if self.gender not in GENDERS:
            raise ValueError(f"slot gender must be one of {GENDERS}, got {self.gender!r}")
        if self.role not in ROLES:
            raise ValueError(f"slot role must be one of {ROLES}, got {self.role!r}")
        if not isinstance(self.count, int) or self.count < 1:
            raise ValueError(f"slot count must be a positive int, got {self.count!r}")
        if self.role == "captain" and self.count != 1:
            raise ValueError("a captain slot must have count 1")

    @property
    def key(self) -> Tuple[str, str]:
        return (self.gender, self.role)

    def to_dict(self) -> dict:
        return {"gender": self.gender, "count": self.count, "role": self.role}


@dataclass(frozen=True)
class TeamComposition:
    """What every team must look like, e.g. [1 male captain, 2 F, 5 M] for the
    2026 melee or [2 M, 2 F] for the 2026 mixed-doubles friendly. Read from
    config `players.team_composition`; the draw, the validator and the
    withdrawal logic are all driven by it so a new gender mix needs no code."""

    slots: Tuple[Slot, ...]

    def __post_init__(self) -> None:
        if not self.slots:
            raise ValueError("team composition needs at least one slot")
        keys = [s.key for s in self.slots]
        if len(keys) != len(set(keys)):
            raise ValueError("team composition has duplicate (gender, role) slots; merge them")
        if sum(s.role == "captain" for s in self.slots) > 1:
            raise ValueError("at most one captain slot per team")

    # -- derived -----------------------------------------------------------
    @property
    def team_size(self) -> int:
        return sum(s.count for s in self.slots)

    @property
    def has_captain(self) -> bool:
        return any(s.role == "captain" for s in self.slots)

    @property
    def captain_gender(self) -> Optional[str]:
        for s in self.slots:
            if s.role == "captain":
                return s.gender
        return None

    def expected(self) -> Dict[Tuple[str, str], int]:
        """(gender, role) -> players per team."""
        return {s.key: s.count for s in self.slots}

    def describe(self) -> str:
        parts = []
        for s in self.slots:
            parts.append(f"{s.count} {s.gender}" + (" captain" if s.role == "captain" else ""))
        return " + ".join(parts)

    # -- (de)serialisation ---------------------------------------------------
    def to_payload(self) -> List[dict]:
        return [s.to_dict() for s in self.slots]

    @staticmethod
    def from_payload(raw: List[dict]) -> "TeamComposition":
        slots = tuple(
            Slot(
                gender=str(d["gender"]).strip().upper(),
                count=int(d["count"]),
                role=str(d.get("role", "member")).strip().lower(),
            )
            for d in raw
        )
        return TeamComposition(slots)

    @staticmethod
    def from_config(players_cfg: dict) -> "TeamComposition":
        """`players.team_composition` if present; otherwise derive the legacy
        1-captain structure from females_per_team / males_per_team /
        captain_gender (the 2026 melee config shape)."""
        raw = players_cfg.get("team_composition")
        if raw:
            return TeamComposition.from_payload(raw)
        if "females_per_team" not in players_cfg or "males_per_team" not in players_cfg:
            raise ValueError(
                "config players section needs team_composition "
                "(or legacy females_per_team/males_per_team)"
            )
        per = {"F": int(players_cfg["females_per_team"]), "M": int(players_cfg["males_per_team"])}
        cg = str(players_cfg.get("captain_gender", "M")).upper()
        per[cg] -= 1  # legacy counts include the captain
        slots = [Slot(cg, 1, "captain")]
        for g in ("F", "M"):
            if per[g] > 0:
                slots.append(Slot(g, per[g]))
        return TeamComposition(tuple(slots))

    @staticmethod
    def legacy_default() -> "TeamComposition":
        """The 2026 melee structure, used when a caller passes no composition."""
        return TeamComposition((Slot("M", 1, "captain"), Slot("F", 2), Slot("M", 5)))


@dataclass(frozen=True)
class Event:
    """One line of the event log. All state is the result of replaying events."""

    seq: int
    ts: str
    type: str
    actor: str
    payload: dict
    seed: Optional[Seed] = None  # int or str, stored verbatim (see parse_seed)

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
    # team id -> member ids; when the composition has a captain slot the
    # captain is always first
    teams: Dict[int, List[int]] = field(default_factory=dict)
    # team id -> mixed-doubles pairs [[male, female], ...]; pair 0 holds the
    # captain when there is one. Empty unless config players.assign_pairs is on.
    pairs: Dict[int, List[List[int]]] = field(default_factory=dict)
    changelog: List[str] = field(default_factory=list)

    def team_of(self, pid: int) -> Optional[int]:
        for tid, members in self.teams.items():
            if pid in members:
                return tid
        return None

    def captain_of(self, tid: int) -> Optional[Player]:
        for pid in self.teams.get(tid, []):
            if self.players[pid].is_captain:
                return self.players[pid]
        return None

    def validate(self, composition: Optional[TeamComposition] = None) -> None:
        """Every team matches `composition` exactly (per gender/role counts),
        the captain (if any) is listed first, nobody is on two teams."""
        composition = composition or TeamComposition.legacy_default()
        expected = composition.expected()
        seen: List[int] = []
        for tid, members in self.teams.items():
            counts: Dict[Tuple[str, str], int] = {}
            for m in members:
                p = self.players[m]
                counts[(p.gender, p.role)] = counts.get((p.gender, p.role), 0) + 1
            if counts != expected:
                have = ", ".join(f"{g}/{r}={n}" for (g, r), n in sorted(counts.items()))
                want = ", ".join(f"{g}/{r}={n}" for (g, r), n in sorted(expected.items()))
                raise ValueError(f"team {tid} malformed: has {have}; expected {want}")
            if composition.has_captain and not self.players[members[0]].is_captain:
                raise ValueError(f"team {tid} first member is not the captain")
            for m in members:
                ft = self.players[m].fixed_team
                if ft is not None and ft != tid:
                    raise ValueError(
                        f"{self.players[m].name}(#{m}) is fixed to team {ft} but sits in team {tid}"
                    )
            seen += members
        if len(seen) != len(set(seen)):
            raise ValueError("some player appears on two teams")
        self._validate_pairs(composition)

    def _validate_pairs(self, composition: TeamComposition) -> None:
        if not self.pairs:
            return
        if set(self.pairs) != set(self.teams):
            raise ValueError("pairs are not defined for exactly the existing teams")
        for tid, prs in self.pairs.items():
            flat = [pid for pr in prs for pid in pr]
            if sorted(flat) != sorted(self.teams[tid]):
                raise ValueError(f"team {tid} pairs do not cover its members exactly")
            for pr in prs:
                if len(pr) != 2 or {self.players[pr[0]].gender, self.players[pr[1]].gender} != {"M", "F"}:
                    raise ValueError(f"team {tid} has a pair that is not 1 M + 1 F: {pr}")
            if composition.has_captain and self.teams[tid][0] not in prs[0]:
                raise ValueError(f"team {tid}: the captain must be in pair 1")
