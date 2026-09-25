"""Composition-driven draw: any gender mix, optional captains, level tiers,
self-describing initial_draw events, direct substitution, xd2026 environment.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils
from core.draw import draw_teams, substitute_direct, withdraw_redraw
from core.models import Event, Player, Slot, TeamComposition, TournamentState, roster_fingerprint
from core.replay import replay

XD = TeamComposition((Slot("M", 2), Slot("F", 2)))
MELEE = TeamComposition.legacy_default()


def roster(n_f=12, n_m=12, levels=None, captains=0):
    """Synthetic roster: females 1..n_f, males after. `levels` = list of tier
    labels cycled over each gender pool; `captains` = first k males flagged."""
    players = {}
    for i in range(n_f):
        lvl = levels[i % len(levels)] if levels else None
        players[i + 1] = Player(i + 1, f"F{i + 1}", "F", False, lvl)
    for j in range(n_m):
        pid = n_f + j + 1
        lvl = levels[j % len(levels)] if levels else None
        players[pid] = Player(pid, f"M{j + 1}", "M", j < captains, lvl)
    return players


def tier_counts(players, teams, gender):
    """team -> Counter(level) for one gender."""
    return {
        tid: Counter(players[m].level for m in ms if players[m].gender == gender)
        for tid, ms in teams.items()
    }


class TestComposition(unittest.TestCase):
    def test_legacy_config_derives_melee_structure(self):
        cfg = {"females_per_team": 2, "males_per_team": 6, "captain_gender": "M"}
        self.assertEqual(TeamComposition.from_config(cfg), MELEE)

    def test_explicit_config(self):
        cfg = {"team_composition": [{"gender": "m", "count": 2}, {"gender": "F", "count": 2}]}
        self.assertEqual(TeamComposition.from_config(cfg), XD)
        self.assertFalse(XD.has_captain)
        self.assertEqual(XD.team_size, 4)
        self.assertTrue(MELEE.has_captain)
        self.assertEqual(MELEE.captain_gender, "M")

    def test_payload_round_trip(self):
        for comp in (XD, MELEE):
            self.assertEqual(TeamComposition.from_payload(comp.to_payload()), comp)

    def test_rejects_bad_slots(self):
        with self.assertRaises(ValueError):
            Slot("X", 1)
        with self.assertRaises(ValueError):
            Slot("M", 0)
        with self.assertRaises(ValueError):
            Slot("M", 2, "captain")
        with self.assertRaises(ValueError):
            TeamComposition((Slot("M", 1), Slot("M", 2)))  # duplicate key
        with self.assertRaises(ValueError):
            TeamComposition(())


class TestGenericDraw(unittest.TestCase):
    def test_xd_structure(self):
        players = roster()
        teams = draw_teams(players, XD, 6, seed=1)
        self.assertEqual(sorted(teams), [1, 2, 3, 4, 5, 6])
        for ms in teams.values():
            self.assertEqual(len(ms), 4)
            self.assertEqual(sum(players[m].gender == "M" for m in ms), 2)
            self.assertEqual(sum(players[m].gender == "F" for m in ms), 2)
        TournamentState(players=players, teams=teams).validate(XD)
        self.assertEqual(sorted(m for ms in teams.values() for m in ms), sorted(players))

    def test_deterministic_and_seed_sensitive(self):
        players = roster()
        self.assertEqual(draw_teams(players, XD, 6, 7), draw_teams(players, XD, 6, 7))
        self.assertNotEqual(draw_teams(players, XD, 6, 7), draw_teams(players, XD, 6, 8))

    def test_independent_of_dict_order(self):
        players = roster()
        reversed_players = dict(reversed(list(players.items())))
        self.assertEqual(draw_teams(players, XD, 6, 3), draw_teams(reversed_players, XD, 6, 3))

    def test_other_mixes(self):
        # 4 teams x (3 M + 1 F), no captain
        comp = TeamComposition((Slot("M", 3), Slot("F", 1)))
        players = roster(n_f=4, n_m=12)
        teams = draw_teams(players, comp, 4, seed=5)
        TournamentState(players=players, teams=teams).validate(comp)
        # 5 teams x (1 F captain + 1 F + 2 M)
        comp = TeamComposition((Slot("F", 1, "captain"), Slot("F", 1), Slot("M", 2)))
        players = roster(n_f=10, n_m=10)
        players = {
            pid: Player(p.id, p.name, p.gender, p.gender == "F" and pid <= 5, p.level)
            for pid, p in players.items()
        }
        teams = draw_teams(players, comp, 5, seed=5)
        st = TournamentState(players=players, teams=teams)
        st.validate(comp)
        for tid in teams:
            self.assertTrue(players[teams[tid][0]].is_captain)

    def test_pool_size_mismatch_raises(self):
        players = roster(n_f=11, n_m=12)
        with self.assertRaises(ValueError) as cm:
            draw_teams(players, XD, 6, seed=1)
        self.assertIn("F/member", str(cm.exception))
        with self.assertRaises(ValueError):
            draw_teams(roster(n_f=13, n_m=12), XD, 6, seed=1)

    def test_players_matching_no_slot_raise(self):
        players = roster(captains=1)  # a captain but XD has no captain slot
        players[25] = Player(25, "extra", "M", False)  # M/member pool back to 12
        with self.assertRaises(ValueError) as cm:
            draw_teams(players, XD, 6, seed=1)
        self.assertIn("match no slot", str(cm.exception))

    def test_melee_structure_still_works(self):
        players = roster(n_f=16, n_m=48, captains=8)
        teams = draw_teams(players, MELEE, 8, seed=2026)
        st = TournamentState(players=players, teams=teams)
        st.validate(MELEE)
        for tid in teams:
            self.assertTrue(players[teams[tid][0]].is_captain)


class TestLevelBalance(unittest.TestCase):
    def test_even_tiers_exact(self):
        # 12 per gender, tiers A/B cycled -> 6 A + 6 B per gender -> 1 A + 1 B per team
        players = roster(levels=["A", "B"])
        teams = draw_teams(players, XD, 6, seed=11)
        for g in ("M", "F"):
            for tid, c in tier_counts(players, teams, g).items():
                self.assertEqual(c, Counter({"A": 1, "B": 1}), (g, tid, c))

    def test_uneven_tiers_within_one(self):
        # 3 tiers of 4 per gender over 6 teams -> every team 0 or 1 of each tier
        players = roster(levels=["A", "B", "C"])
        for seed in range(20):
            teams = draw_teams(players, XD, 6, seed=seed)
            for g in ("M", "F"):
                per_tier = {}
                for tid, c in tier_counts(players, teams, g).items():
                    for lvl in ("A", "B", "C"):
                        per_tier.setdefault(lvl, []).append(c.get(lvl, 0))
                for lvl, counts in per_tier.items():
                    self.assertLessEqual(max(counts) - min(counts), 1, (seed, g, lvl, counts))

    def test_which_team_gets_extra_depends_on_seed(self):
        players = roster(levels=["A", "B", "C"])
        seen = set()
        for seed in range(30):
            teams = draw_teams(players, XD, 6, seed=seed)
            seen.add(tuple(sorted(tid for tid, c in tier_counts(players, teams, "M").items() if c.get("A"))))
        self.assertGreater(len(seen), 1)

    def test_partial_levels(self):
        # only some players tiered: the untiered form their own tier, still valid
        players = roster()
        players[1] = Player(1, "F1", "F", False, "A")
        players[13] = Player(13, "M1", "M", False, "A")
        teams = draw_teams(players, XD, 6, seed=4)
        TournamentState(players=players, teams=teams).validate(XD)

    def test_balancing_can_be_switched_off(self):
        players = roster(levels=["A", "B"])
        on = draw_teams(players, XD, 6, seed=9, balance_by_level=True)
        off = draw_teams(players, XD, 6, seed=9, balance_by_level=False)
        TournamentState(players=players, teams=off).validate(XD)
        self.assertNotEqual(on, off)


class TestReplayAndSubstitution(unittest.TestCase):
    def draw_event(self, seed=20260925, comp=XD, n=6, seq=1):
        return Event(seq, "2026-09-25T12:00:00", "initial_draw", "organizer",
                     {"num_teams": n, "team_composition": comp.to_payload()}, seed)

    def test_replay_initial_draw_with_payload(self):
        players = roster()
        st = replay(players, [self.draw_event()], num_teams=6, composition=XD)
        st.validate(XD)
        self.assertEqual(st.teams, draw_teams(players, XD, 6, 20260925))

    def test_replay_rejects_changed_config(self):
        players = roster()
        other = TeamComposition((Slot("M", 1), Slot("F", 3)))
        with self.assertRaises(ValueError):
            replay(players, [self.draw_event()], num_teams=6, composition=other)
        with self.assertRaises(ValueError):
            replay(players, [self.draw_event()], num_teams=4, composition=XD)

    def test_replay_rejects_edited_roster(self):
        players = roster()
        ev = Event(1, "t", "initial_draw", "organizer",
                   {"num_teams": 6, "team_composition": XD.to_payload(),
                    "roster_fingerprint": roster_fingerprint(players)}, 5)
        replay(players, [ev], num_teams=6, composition=XD).validate(XD)  # unchanged: fine
        edited = dict(players)
        edited[1] = Player(1, "F1", "F", False, "A")  # someone fills in a level later
        with self.assertRaises(ValueError) as cm:
            replay(edited, [ev], num_teams=6, composition=XD)
        self.assertIn("different roster", str(cm.exception))
        renamed = dict(players)
        renamed[1] = Player(1, "Real Name", "F", False, None)  # names are not part of the draw
        replay(renamed, [ev], num_teams=6, composition=XD).validate(XD)

    def test_replay_legacy_event_without_payload(self):
        players = roster()
        ev = Event(1, "t", "initial_draw", "organizer", {}, 5)
        st = replay(players, [ev], num_teams=6, composition=XD)
        st.validate(XD)

    def test_direct_substitute_keeps_seat(self):
        players = roster(levels=["A", "B", "C"])
        st = replay(players, [self.draw_event()], num_teams=6, composition=XD)
        quitter = 7
        tid = st.team_of(quitter)
        idx = st.teams[tid].index(quitter)
        new_players, new_teams, log = substitute_direct(st, quitter, "Waitlist One")
        new_st = TournamentState(players=new_players, teams=new_teams)
        new_st.validate(XD)
        self.assertNotIn(quitter, new_players)
        sub_id = max(new_players)
        self.assertEqual(new_players[sub_id].name, "Waitlist One")
        self.assertEqual(new_players[sub_id].gender, "F")
        self.assertEqual(new_players[sub_id].level, players[quitter].level)
        self.assertEqual(new_teams[tid][idx], sub_id)
        # every other team untouched
        for t in new_teams:
            if t != tid:
                self.assertEqual(new_teams[t], st.teams[t])
        self.assertEqual(len(log), 2)
        # and state was not mutated
        self.assertIn(quitter, st.players)

    def test_direct_substitute_via_replay(self):
        players = roster()
        evs = [self.draw_event(),
               Event(2, "t2", "substitute_direct", "organizer",
                     {"withdrawn_id": 20, "substitute_name": "Waitlist Two"})]
        st = replay(players, evs, num_teams=6, composition=XD)
        st.validate(XD)
        self.assertNotIn(20, st.players)
        self.assertTrue(any("Waitlist Two" in line for line in st.changelog))

    def test_direct_substitute_captain_needs_successor(self):
        players = roster(n_f=16, n_m=48, captains=8)
        st = replay(players, [self.draw_event(comp=MELEE, n=8)], num_teams=8, composition=MELEE)
        cap = st.teams[1][0]
        with self.assertRaises(ValueError):
            substitute_direct(st, cap, "Sub")
        successor = next(m for m in st.teams[1] if players[m].gender == "M" and not players[m].is_captain)
        new_players, new_teams, _ = substitute_direct(st, cap, "Sub", new_captain_id=successor)
        new_st = TournamentState(players=new_players, teams=new_teams)
        new_st.validate(MELEE)
        self.assertEqual(new_teams[1][0], successor)
        self.assertTrue(new_players[successor].is_captain)

    def test_withdraw_redraw_generic_team_count(self):
        players = roster()
        st = replay(players, [self.draw_event()], num_teams=6, composition=XD)
        new_players, new_teams, log = withdraw_redraw(st, 3, "Sub F", seed=77)
        TournamentState(players=new_players, teams=new_teams).validate(XD)
        self.assertEqual(sum("gives up" in line for line in log), 5)


class TestOtherEnvConfigs(unittest.TestCase):
    def test_prod_config_still_melee(self):
        cfg = io_utils.load_config(io_utils.DATA_ROOT / "prod" / "config.yaml")
        self.assertEqual(io_utils.draw_params(cfg)["composition"], MELEE)


if __name__ == "__main__":
    unittest.main()
