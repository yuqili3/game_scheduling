"""Seeds may be integers or arbitrary text; parse_seed normalises user input,
draws are deterministic for text seeds, and both event stores keep the type.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.draw import blind_draw_pick, draw_teams, group_draw_assign, withdraw_redraw
from core.eventstore import EventStore
from core.io_utils import append_event, read_events
from core.models import Event, Player, Slot, TeamComposition, TournamentState, parse_seed
from core.replay import replay

XD = TeamComposition((Slot("M", 2), Slot("F", 2)))


def roster():
    players = {i: Player(i, f"F{i}", "F") for i in range(1, 13)}
    players.update({i: Player(i, f"M{i}", "M") for i in range(13, 25)})
    return players


class TestParseSeed(unittest.TestCase):
    def test_integers(self):
        self.assertEqual(parse_seed("20260925"), 20260925)
        self.assertEqual(parse_seed(" 42 "), 42)
        self.assertEqual(parse_seed("-7"), -7)
        self.assertEqual(parse_seed("007"), 7)
        self.assertEqual(parse_seed(123), 123)

    def test_text(self):
        self.assertEqual(parse_seed("周五晚上见"), "周五晚上见")
        self.assertEqual(parse_seed("  hello world "), "hello world")
        self.assertEqual(parse_seed("2026-09-25"), "2026-09-25")
        self.assertEqual(parse_seed("1e3"), "1e3")

    def test_rejects_empty(self):
        for bad in ("", "   ", None, True):
            with self.assertRaises(ValueError):
                parse_seed(bad)

    def test_int_and_digit_string_are_the_same_seed(self):
        players = roster()
        self.assertEqual(
            draw_teams(players, XD, 6, parse_seed("20260925")),
            draw_teams(players, XD, 6, 20260925),
        )


class TestTextSeedDraws(unittest.TestCase):
    def test_draw_teams_text_seed_deterministic(self):
        players = roster()
        a = draw_teams(players, XD, 6, "我们来打羽毛球")
        b = draw_teams(players, XD, 6, "我们来打羽毛球")
        c = draw_teams(players, XD, 6, "我们来打羽毛球!")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        TournamentState(players=players, teams=a).validate(XD)

    def test_text_seed_stable_across_processes(self):
        # string seeding must not depend on PYTHONHASHSEED / process state
        code = (
            "import sys; sys.path.insert(0, %r); from core.draw import group_draw_assign; "
            "print(group_draw_assign(list(range(1, 9)), 'friday night'))" % str(ROOT)
        )
        outs = set()
        for h in ("0", "1", "random"):
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               env={"PYTHONHASHSEED": h, "PATH": "/usr/bin:/bin"})
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.add(r.stdout.strip())
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs.pop(), str(group_draw_assign(list(range(1, 9)), "friday night")))

    def test_other_draw_functions_accept_text(self):
        self.assertEqual(group_draw_assign([1, 2, 3], "x"), group_draw_assign([1, 2, 3], "x"))
        self.assertEqual(blind_draw_pick("XD", [1, 2], [13, 14], "go"),
                         blind_draw_pick("XD", [1, 2], [13, 14], "go"))
        players = roster()
        st = TournamentState(players=players, teams=draw_teams(players, XD, 6, "s"))
        r1 = withdraw_redraw(st, 3, "Sub", "redraw one")
        r2 = withdraw_redraw(st, 3, "Sub", "redraw one")
        self.assertEqual(r1[1], r2[1])


class TestSeedPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jsonl_round_trip_keeps_type_and_replays(self):
        path = self.root / "events.jsonl"
        ev = Event(1, "2026-09-25T12:00:00", "initial_draw", "organizer",
                   {"num_teams": 6, "team_composition": XD.to_payload()}, "周五晚上见")
        append_event(ev, path)
        append_event(Event(2, "t", "withdraw_redraw", "organizer",
                           {"withdrawn_id": 1, "substitute_name": "Sub"}, 20260925), path)
        back = read_events(path)
        self.assertEqual(back[0].seed, "周五晚上见")
        self.assertIsInstance(back[0].seed, str)
        self.assertEqual(back[1].seed, 20260925)
        self.assertIsInstance(back[1].seed, int)
        players = roster()
        st = replay(players, back, num_teams=6, composition=XD)
        st.validate(XD)
        self.assertEqual(st.teams, replay(players, back, num_teams=6, composition=XD).teams)

    def test_sqlite_store_keeps_text_seed(self):
        store = EventStore(self.root / "t.db", self.root / "events.jsonl")
        e1 = store.append("group_draw", "admin", {"groups": {}}, seed="friday night")
        e2 = store.append("group_draw", "admin", {"groups": {}}, seed=20260925)
        e3 = store.append("group_draw", "admin", {"groups": {}}, seed="123")  # not normalised by the store
        got = {e.seq: e.seed for e in store.events()}
        self.assertEqual(got[e1.seq], "friday night")
        self.assertEqual(got[e2.seq], 20260925)
        # INTEGER affinity turns the text "123" into 123 — identical to what
        # parse_seed would have produced, so callers that normalise see no difference
        self.assertEqual(got[e3.seq], parse_seed("123"))
        archived = {e.seq: e.seed for e in read_events(self.root / "events.jsonl")}
        self.assertEqual(archived[e1.seq], "friday night")


if __name__ == "__main__":
    unittest.main()
