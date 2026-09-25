"""M1 unit tests: draw determinism, structure constraints, withdrawal redraw,
event replay.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.draw import draw_teams, withdraw_redraw
from core.io_utils import draw_params, load_config, load_players
from core.models import Event, TournamentState
from core.replay import replay

CFG = load_config()
N_TEAMS = CFG["players"]["num_teams"]
COMP = draw_params(CFG)["composition"]


def real_players():
    return load_players()


def make_state(seed=1):
    players = real_players()
    teams = draw_teams(players, COMP, N_TEAMS, seed)
    return TournamentState(players=players, teams=teams)


class TestDraw(unittest.TestCase):
    def test_roster_counts(self):
        players = real_players()
        self.assertEqual(len(players), 64)
        self.assertEqual(sum(p.gender == "F" for p in players.values()), 16)
        self.assertEqual(sum(p.gender == "M" for p in players.values()), 48)
        self.assertEqual(sum(p.is_captain for p in players.values()), 8)
        self.assertTrue(all(p.gender == "M" for p in players.values() if p.is_captain))

    def test_deterministic_same_seed(self):
        players = real_players()
        self.assertEqual(draw_teams(players, COMP, N_TEAMS, 42), draw_teams(players, COMP, N_TEAMS, 42))

    def test_different_seed_differs(self):
        players = real_players()
        self.assertNotEqual(draw_teams(players, COMP, N_TEAMS, 1), draw_teams(players, COMP, N_TEAMS, 2))

    def test_structure_valid(self):
        state = make_state(seed=7)
        state.validate(COMP)  # passing = no exception
        self.assertEqual(sum(len(m) for m in state.teams.values()), 64)


class TestWithdraw(unittest.TestCase):
    def test_member_withdraw(self):
        state = make_state(seed=3)
        quitter = next(
            pid for pid, p in state.players.items() if p.gender == "M" and not p.is_captain
        )
        players, teams, log = withdraw_redraw(state, quitter, "Sub A", seed=99)
        new_state = TournamentState(players=players, teams=teams)
        new_state.validate(COMP)
        self.assertNotIn(quitter, players)
        sub_id = max(players)
        self.assertEqual(players[sub_id].name, "Sub A")
        self.assertIsNotNone(new_state.team_of(sub_id))
        self.assertTrue(log)

    def test_female_withdraw_keeps_gender_balance(self):
        state = make_state(seed=3)
        quitter = next(pid for pid, p in state.players.items() if p.gender == "F")
        players, teams, _ = withdraw_redraw(state, quitter, "Sub F", seed=5)
        new_state = TournamentState(players=players, teams=teams)
        new_state.validate(COMP)
        self.assertEqual(players[max(players)].gender, "F")

    def test_captain_withdraw_requires_successor(self):
        state = make_state(seed=3)
        captain = next(pid for pid, p in state.players.items() if p.is_captain)
        with self.assertRaises(ValueError):
            withdraw_redraw(state, captain, "Sub B", seed=1)

    def test_captain_withdraw_with_successor(self):
        state = make_state(seed=3)
        captain = next(pid for pid, p in state.players.items() if p.is_captain)
        tid = state.team_of(captain)
        successor = next(
            m
            for m in state.teams[tid]
            if state.players[m].gender == "M" and not state.players[m].is_captain
        )
        players, teams, _ = withdraw_redraw(state, captain, "Sub C", seed=11,
                                            new_captain_id=successor)
        new_state = TournamentState(players=players, teams=teams)
        new_state.validate(COMP)
        self.assertTrue(players[successor].is_captain)
        self.assertEqual(teams[tid][0], successor)

    def test_deterministic(self):
        state = make_state(seed=3)
        quitter = next(
            pid for pid, p in state.players.items() if p.gender == "M" and not p.is_captain
        )
        r1 = withdraw_redraw(state, quitter, "Sub D", seed=2024)
        r2 = withdraw_redraw(state, quitter, "Sub D", seed=2024)
        self.assertEqual(r1[1], r2[1])
        r3 = withdraw_redraw(state, quitter, "Sub D", seed=2025)
        self.assertNotEqual(r1[1], r3[1])


class TestReplay(unittest.TestCase):
    def _events(self):
        return [
            Event(1, "2026-06-12T12:00:00", "initial_draw", "organizer", {}, seed=20260612),
            Event(
                2,
                "2026-07-01T09:00:00",
                "withdraw_redraw",
                "organizer",
                {"withdrawn_id": 30, "substitute_name": "Sub One"},
                seed=701,
            ),
            Event(
                3,
                "2026-07-10T09:00:00",
                "withdraw_redraw",
                "organizer",
                {"withdrawn_id": 5, "substitute_name": "Sub Two"},
                seed=710,
            ),
        ]

    def test_replay_reproducible(self):
        players = real_players()
        s1 = replay(players, self._events(), N_TEAMS)
        s2 = replay(players, self._events(), N_TEAMS)
        self.assertEqual(s1.teams, s2.teams)
        self.assertEqual(s1.players, s2.players)
        s1.validate(COMP)

    def test_replay_prefix_is_intermediate_state(self):
        """Replaying an event prefix yields the state at that time (acceptance #3)."""
        players = real_players()
        mid = replay(players, self._events()[:2], N_TEAMS)
        self.assertNotIn(30, mid.players)
        self.assertIn(5, mid.players)  # second withdrawal has not happened yet
        mid.validate(COMP)

    def test_assign_teams_event(self):
        players = real_players()
        teams = draw_teams(players, COMP, N_TEAMS, 123)
        ev = Event(
            1,
            "2026-06-12T12:00:00",
            "assign_teams",
            "organizer",
            {"teams": {str(t): ms for t, ms in teams.items()}},
        )
        state = replay(players, [ev], N_TEAMS)
        self.assertEqual(state.teams, teams)
        state.validate(COMP)

    def test_unknown_event_rejected(self):
        players = real_players()
        with self.assertRaises(ValueError):
            replay(players, [Event(1, "t", "mystery_event", "x", {})], N_TEAMS)


if __name__ == "__main__":
    unittest.main()
