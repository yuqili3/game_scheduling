"""Pre-match event replay: (initial players, [events]) -> TournamentState.
Pure: no IO, no clock, randomness only via the seeds stored in the events."""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from .draw import assign_pairs, draw_teams, replace_in_pairs, substitute_direct, withdraw_redraw
from .models import Event, Player, TeamComposition, TournamentState, roster_fingerprint

PRE_MATCH_EVENTS = ("initial_draw", "assign_teams", "withdraw_redraw", "substitute_direct")


def _check_draw_payload(
    ev: Event, num_teams: int, composition: TeamComposition, players: Dict[int, Player]
) -> None:
    """An initial_draw event records the structure and roster it was drawn
    with; the config and players*.csv used for replay must still agree,
    otherwise the replayed teams would silently differ from the published ones."""
    p = ev.payload or {}
    if "roster_fingerprint" in p and p["roster_fingerprint"] != roster_fingerprint(players):
        raise ValueError(
            f"event seq={ev.seq} initial_draw was made from a different roster "
            f"(fingerprint {p['roster_fingerprint']}, now {roster_fingerprint(players)}); "
            "players*.csv changed after the draw (gender/captain/level/fixed_team edits)"
        )
    if "num_teams" in p and int(p["num_teams"]) != num_teams:
        raise ValueError(
            f"event seq={ev.seq} initial_draw was made with num_teams={p['num_teams']}, "
            f"config now says {num_teams}"
        )
    if "team_composition" in p:
        logged = TeamComposition.from_payload(p["team_composition"])
        if logged != composition:
            raise ValueError(
                f"event seq={ev.seq} initial_draw was made with composition "
                f"[{logged.describe()}], config now says [{composition.describe()}]"
            )


def replay(
    initial_players: Dict[int, Player],
    events: Iterable[Event],
    num_teams: int = 8,
    composition: Optional[TeamComposition] = None,
    balance_by_level: bool = True,
    assign_pairs_flag: bool = False,
    **kwargs,
) -> TournamentState:
    """Replay the pre-match events. `composition` defaults to the 2026 melee
    structure so existing callers keep working; new tournaments pass
    `**io_utils.draw_params(cfg)` (which supplies `assign_pairs`)."""
    if "assign_pairs" in kwargs:  # the config key name, via io_utils.draw_params
        assign_pairs_flag = bool(kwargs.pop("assign_pairs"))
    if kwargs:
        raise TypeError(f"replay() got unexpected arguments: {sorted(kwargs)}")
    composition = composition or TeamComposition.legacy_default()
    state = TournamentState(players=dict(initial_players))

    def pair_up(seed) -> None:
        if assign_pairs_flag:
            state.pairs = assign_pairs(state.teams, state.players, seed)
            state.changelog.append(f"[{ev.ts}] mixed-doubles pairs drawn (seed={seed!r})")

    for ev in events:
        if ev.type == "initial_draw":
            if ev.seed is None:
                raise ValueError(f"event seq={ev.seq} initial_draw is missing a seed")
            _check_draw_payload(ev, num_teams, composition, state.players)
            state.teams = draw_teams(
                state.players, composition, num_teams, ev.seed, balance_by_level
            )
            state.changelog.append(
                f"[{ev.ts}] initial draw seed={ev.seed!r} ({num_teams} teams of {composition.describe()})"
            )
            pair_up(ev.seed)
        elif ev.type == "assign_teams":
            # payload: {"teams": {"1": [17, 1, 2, ...], ...}} — imports an
            # offline-published assignment verbatim; optional "pairs" likewise
            state.teams = {
                int(t): [int(m) for m in ms] for t, ms in ev.payload["teams"].items()
            }
            state.pairs = {
                int(t): [[int(a), int(b)] for a, b in prs]
                for t, prs in ev.payload.get("pairs", {}).items()
            }
            state.changelog.append(f"[{ev.ts}] teams assigned (imported offline result)")
        elif ev.type == "withdraw_redraw":
            if ev.seed is None:
                raise ValueError(f"event seq={ev.seq} withdraw_redraw is missing a seed")
            p = ev.payload
            new_cap = p.get("new_captain_id")
            players, teams, log = withdraw_redraw(
                state,
                int(p["withdrawn_id"]),
                p["substitute_name"],
                ev.seed,
                int(new_cap) if new_cap is not None else None,
            )
            state.players, state.teams = players, teams
            state.changelog += [f"[{ev.ts}] (seed={ev.seed!r}) {line}" for line in log]
            pair_up(ev.seed)
        elif ev.type == "substitute_direct":
            p = ev.payload
            wid = int(p["withdrawn_id"])
            new_cap = p.get("new_captain_id")
            home = state.team_of(wid)
            players, teams, log = substitute_direct(
                state, wid, p["substitute_name"], int(new_cap) if new_cap is not None else None
            )
            state.players, state.teams = players, teams
            if state.pairs:
                sub_id = max(players)
                cap = teams[home][0] if composition.has_captain and home is not None else None
                state.pairs = replace_in_pairs(state.pairs, wid, sub_id, cap)
            state.changelog += [f"[{ev.ts}] {line}" for line in log]
        else:
            raise ValueError(f"unknown pre-match event type: {ev.type} (seq={ev.seq})")
    return state
