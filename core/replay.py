"""Pre-event replay: state = replay(initial roster, events). Core of reproducibility."""
from __future__ import annotations

from typing import Dict, Iterable

from .draw import draw_teams, withdraw_redraw
from .models import Event, Player, TournamentState

PRE_MATCH_EVENTS = ("initial_draw", "assign_teams", "withdraw_redraw")


def replay(
    initial_players: Dict[int, Player],
    events: Iterable[Event],
    num_teams: int = 8,
) -> TournamentState:
    state = TournamentState(players=dict(initial_players))
    for ev in events:
        if ev.type == "initial_draw":
            if ev.seed is None:
                raise ValueError(f"event seq={ev.seq} initial_draw is missing a seed")
            state.teams = draw_teams(state.players, num_teams, ev.seed)
            state.changelog.append(f"[{ev.ts}] initial draw seed={ev.seed}")
        elif ev.type == "assign_teams":
            # payload: {"teams": {"1": [17, 1, 2, ...], ...}} — imports an
            # offline-published assignment verbatim
            state.teams = {
                int(t): [int(m) for m in ms] for t, ms in ev.payload["teams"].items()
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
            state.changelog += [f"[{ev.ts}] (seed={ev.seed}) {line}" for line in log]
        else:
            raise ValueError(f"unknown pre-match event type: {ev.type} (seq={ev.seq})")
    return state
