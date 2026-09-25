"""Pinned seats (fixed_team), layered dealing, mixed-doubles pair assignment
and the real xd2026 roster.

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
from core.draw import assign_pairs, draw_teams, substitute_direct
from core.models import (Event, Player, Slot, TeamComposition, TournamentState, pair_label,
                         roster_fingerprint, team_letter)
from core.replay import replay

XD_CAP = TeamComposition((Slot("M", 1, "captain"), Slot("M", 1), Slot("F", 2)))
CAPTAIN_TEAMS = {13: 1, 14: 2, 17: 3, 19: 4, 22: 5, 21: 6}


def roster(levels=None):
    players = {}
    for i in range(1, 13):
        lvl = levels[i % len(levels)] if levels else None
        players[i] = Player(i, f"F{i}", "F", False, lvl)
    for i in range(13, 25):
        lvl = levels[i % len(levels)] if levels else None
        cap = i in CAPTAIN_TEAMS
        players[i] = Player(i, f"M{i}", "M", cap, lvl, CAPTAIN_TEAMS.get(i))
    return players


class TestFixedSeats(unittest.TestCase):
    def test_captains_land_in_their_teams_first(self):
        players = roster()
        for seed in ("a", "b", 3):
            teams = draw_teams(players, XD_CAP, 6, seed)
            for pid, tid in CAPTAIN_TEAMS.items():
                self.assertEqual(teams[tid][0], pid)
            st = TournamentState(players=players, teams=teams)
            st.validate(XD_CAP)
            for ms in teams.values():
                self.assertEqual(len(ms), 4)
                self.assertEqual(sum(players[m].gender == "F" for m in ms), 2)

    def test_deterministic_and_seed_sensitive(self):
        players = roster()
        self.assertEqual(draw_teams(players, XD_CAP, 6, "x"), draw_teams(players, XD_CAP, 6, "x"))
        self.assertNotEqual(draw_teams(players, XD_CAP, 6, "x"), draw_teams(players, XD_CAP, 6, "y"))

    def test_fixed_out_of_range(self):
        players = roster()
        players[13] = Player(13, "M13", "M", True, None, 7)
        with self.assertRaises(ValueError) as cm:
            draw_teams(players, XD_CAP, 6, 1)
        self.assertIn("teams are 1..6", str(cm.exception))

    def test_two_fixed_into_one_seat(self):
        players = roster()
        players[14] = Player(14, "M14", "M", True, None, 1)  # second captain pinned to team 1
        with self.assertRaises(ValueError) as cm:
            draw_teams(players, XD_CAP, 6, 1)
        self.assertIn("fixed into slot M/captain", str(cm.exception))

    def test_fixed_player_without_slot(self):
        players = roster()
        players[1] = Player(1, "F1", "F", True, None, 1)  # female captain, no such slot
        with self.assertRaises(ValueError) as cm:
            draw_teams(players, XD_CAP, 6, 1)
        self.assertIn("matches no slot", str(cm.exception))

    def test_pool_count_message_mentions_fixed(self):
        players = roster()
        players[15] = Player(15, "M15", "M", False, None, 2)  # a regular male pinned to team 2
        teams = draw_teams(players, XD_CAP, 6, 1)  # still fine: team 2 needs 0 from M/member
        self.assertIn(15, teams[2])
        del players[16]  # now one regular male short
        with self.assertRaises(ValueError) as cm:
            draw_teams(players, XD_CAP, 6, 1)
        self.assertIn("minus 1 fixed", str(cm.exception))

    def test_partial_fixed_layered_dealing_keeps_level_balance(self):
        # pin 3 of the 6 captains only; regular males tiered A/B/C
        players = roster(levels=["A", "B", "C"])
        for pid in (19, 22, 21):
            p = players[pid]
            players[pid] = Player(p.id, p.name, p.gender, True, p.level, None)
        for seed in range(15):
            teams = draw_teams(players, XD_CAP, 6, seed)
            TournamentState(players=players, teams=teams).validate(XD_CAP)
            for tid in (1, 2, 3):
                self.assertEqual(players[teams[tid][0]].fixed_team, tid)
            for g in ("M", "F"):
                for lvl in ("A", "B", "C"):
                    counts = [
                        sum(1 for m in ms if players[m].gender == g and not players[m].is_captain
                            and players[m].level == lvl)
                        for ms in teams.values()
                    ]
                    self.assertLessEqual(max(counts) - min(counts), 1, (seed, g, lvl, counts))

    def test_fingerprint_includes_fixed_team(self):
        a = roster()
        b = dict(a)
        b[13] = Player(13, "M13", "M", True, None, 2)
        self.assertNotEqual(roster_fingerprint(a), roster_fingerprint(b))

    def test_validate_rejects_misplaced_fixed_player(self):
        players = roster()
        teams = draw_teams(players, XD_CAP, 6, 1)
        teams[1][0], teams[2][0] = teams[2][0], teams[1][0]
        with self.assertRaises(ValueError) as cm:
            TournamentState(players=players, teams=teams).validate(XD_CAP)
        self.assertIn("fixed to team", str(cm.exception))


class TestPairLabels(unittest.TestCase):
    def test_letters(self):
        self.assertEqual([team_letter(t) for t in (1, 2, 6, 26, 27, 28, 52, 53)],
                         ["A", "B", "F", "Z", "AA", "AB", "AZ", "BA"])
        self.assertEqual(pair_label(1, 0), "A1")
        self.assertEqual(pair_label(2, 1), "B2")
        self.assertEqual(pair_label(6, 0), "F1")
        with self.assertRaises(ValueError):
            team_letter(0)


class TestPairs(unittest.TestCase):
    def test_pairs_shape_and_captain_first(self):
        players = roster()
        teams = draw_teams(players, XD_CAP, 6, "s")
        pairs = assign_pairs(teams, players, "s")
        for tid, prs in pairs.items():
            self.assertEqual(len(prs), 2)
            self.assertIn(teams[tid][0], prs[0])  # captain in pair 1
            for m, f in prs:
                self.assertEqual((players[m].gender, players[f].gender), ("M", "F"))
            self.assertEqual(sorted(x for pr in prs for x in pr), sorted(teams[tid]))
        st = TournamentState(players=players, teams=teams, pairs=pairs)
        st.validate(XD_CAP)

    def test_pairs_deterministic_and_vary(self):
        players = roster()
        teams = draw_teams(players, XD_CAP, 6, 1)
        self.assertEqual(assign_pairs(teams, players, "p"), assign_pairs(teams, players, "p"))
        seen = {str(assign_pairs(teams, players, s)) for s in range(10)}
        self.assertGreater(len(seen), 1)

    def test_unequal_genders_rejected(self):
        players = {1: Player(1, "F1", "F"), 2: Player(2, "M2", "M"), 3: Player(3, "M3", "M")}
        with self.assertRaises(ValueError):
            assign_pairs({1: [2, 3, 1]}, players, 0)

    def test_validate_pairs(self):
        players = roster()
        teams = draw_teams(players, XD_CAP, 6, 2)
        pairs = assign_pairs(teams, players, 2)
        pairs[1] = pairs[1][::-1]  # captain no longer in pair 1
        with self.assertRaises(ValueError) as cm:
            TournamentState(players=players, teams=teams, pairs=pairs).validate(XD_CAP)
        self.assertIn("pair 1", str(cm.exception))


class TestReplayWithPairs(unittest.TestCase):
    def draw_event(self, seed="draw"):
        return Event(1, "t", "initial_draw", "organizer",
                     {"num_teams": 6, "team_composition": XD_CAP.to_payload()}, seed)

    def test_initial_draw_sets_pairs_when_enabled(self):
        players = roster()
        st = replay(players, [self.draw_event()], num_teams=6, composition=XD_CAP, assign_pairs=True)
        st.validate(XD_CAP)
        self.assertEqual(set(st.pairs), set(range(1, 7)))
        off = replay(players, [self.draw_event()], num_teams=6, composition=XD_CAP)
        self.assertEqual(off.pairs, {})
        self.assertEqual(off.teams, st.teams)

    def test_direct_substitute_inherits_pair(self):
        players = roster()
        st = replay(players, [self.draw_event()], num_teams=6, composition=XD_CAP, assign_pairs=True)
        wid = st.teams[3][2]  # a female of team 3
        partner = next(pr for pr in st.pairs[3] if wid in pr)
        partner_m = partner[0]
        evs = [self.draw_event(),
               Event(2, "t2", "substitute_direct", "organizer",
                     {"withdrawn_id": wid, "substitute_name": "Sub"})]
        st2 = replay(players, evs, num_teams=6, composition=XD_CAP, assign_pairs=True)
        st2.validate(XD_CAP)
        sub_id = max(st2.players)
        self.assertIn([partner_m, sub_id], st2.pairs[3])
        for t in st2.pairs:
            if t != 3:
                self.assertEqual(st2.pairs[t], st.pairs[t])

    def test_captain_substitution_reorders_pairs(self):
        players = roster()
        st = replay(players, [self.draw_event()], num_teams=6, composition=XD_CAP, assign_pairs=True)
        cap = st.teams[2][0]
        successor = st.teams[2][1]
        evs = [self.draw_event(),
               Event(2, "t2", "substitute_direct", "organizer",
                     {"withdrawn_id": cap, "substitute_name": "New Guy",
                      "new_captain_id": successor})]
        st2 = replay(players, evs, num_teams=6, composition=XD_CAP, assign_pairs=True)
        st2.validate(XD_CAP)
        self.assertEqual(st2.teams[2][0], successor)
        self.assertIn(successor, st2.pairs[2][0])

    def test_withdraw_redraw_repairs(self):
        players = roster()
        evs = [self.draw_event(),
               Event(2, "t2", "withdraw_redraw", "organizer",
                     {"withdrawn_id": 5, "substitute_name": "Sub"}, "redraw")]
        st = replay(players, evs, num_teams=6, composition=XD_CAP, assign_pairs=True)
        st.validate(XD_CAP)
        self.assertEqual(set(st.pairs), set(range(1, 7)))

    def test_assign_teams_payload_pairs(self):
        players = roster()
        teams = draw_teams(players, XD_CAP, 6, 1)
        pairs = assign_pairs(teams, players, 1)
        ev = Event(1, "t", "assign_teams", "organizer",
                   {"teams": {str(t): ms for t, ms in teams.items()},
                    "pairs": {str(t): prs for t, prs in pairs.items()}})
        st = replay(players, [ev], num_teams=6, composition=XD_CAP, assign_pairs=True)
        st.validate(XD_CAP)
        self.assertEqual(st.pairs, pairs)


class TestXd2026RealRoster(unittest.TestCase):
    ENV_DIR = io_utils.DATA_ROOT / "xd2026"

    def test_roster(self):
        players = io_utils.load_players(self.ENV_DIR)
        self.assertEqual(sorted(players), list(range(1, 25)))
        self.assertEqual(sum(p.gender == "F" for p in players.values()), 12)
        caps = {p.fixed_team: p.name for p in players.values() if p.is_captain}
        self.assertEqual(caps, {1: "DreamWu", 2: "Michael Chou", 3: "Zack Chen",
                                4: "Yingtong Chen", 5: "Zhihao Hu", 6: "Guoquan Feng"})
        self.assertTrue(all(p.fixed_team is None for p in players.values() if not p.is_captain))
        self.assertTrue(all(p.level is None for p in players.values()))
        self.assertEqual(players[13].name, "DreamWu")   # male ids follow sign-up order
        self.assertEqual(players[24].name, "Colin Zheng")
        self.assertEqual(players[1].name, "yini liu")
        self.assertEqual(players[12].name, "Yuxin Han")

    def test_config_and_full_draw(self):
        players = io_utils.load_players(self.ENV_DIR)
        cfg = io_utils.load_config(self.ENV_DIR / "config.yaml")
        params = io_utils.draw_params(cfg)
        self.assertEqual(params["composition"], XD_CAP)
        self.assertTrue(params["assign_pairs"])
        ev = Event(1, "t", "initial_draw", "organizer",
                   {"num_teams": 6, "team_composition": XD_CAP.to_payload(),
                    "roster_fingerprint": roster_fingerprint(players)}, "friday")
        st = replay(players, [ev], **params)
        st.validate(params["composition"])
        for tid in range(1, 7):
            self.assertEqual(players[st.teams[tid][0]].fixed_team, tid)
            self.assertIn(st.teams[tid][0], st.pairs[tid][0])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "teams.csv"
            io_utils.export_teams_csv(st, out)
            with open(out, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(Counter(r["pair"] for r in rows),
                         Counter({f"{L}{k}": 2 for L in "ABCDEF" for k in (1, 2)}))
        caps = {r["team_id"]: r["captain_name"] for r in rows if r["role"] == "captain"}
        self.assertEqual(caps["1"], "DreamWu")
        self.assertTrue(all(r["pair"] == team_letter(int(r["team_id"])) + "1"
                            for r in rows if r["role"] == "captain"))
        self.assertEqual({r["pair"] for r in rows if r["team_id"] == "6"}, {"F1", "F2"})


if __name__ == "__main__":
    unittest.main()
