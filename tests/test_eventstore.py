"""EventStore tests: sqlite append, jsonl sync/export round-trip, seq ordering."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.eventstore import EventStore
from core.io_utils import read_events
from core.models import Event


class TestEventStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "tournament.db"
        self.jsonl = root / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_assigns_increasing_seq_and_exports(self):
        store = EventStore(self.db, self.jsonl)
        e1 = store.append("group_draw", "admin", {"groups": {}})
        e2 = store.append("game_finished", "VolunteerA",
                          {"node": "R1-1", "slot": "WD", "game": 1, "score": [21, 15]})
        self.assertEqual((e1.seq, e2.seq), (1, 2))
        archived = read_events(self.jsonl)
        self.assertEqual([e.seq for e in archived], [1, 2])
        self.assertEqual(archived[1].payload["score"], [21, 15])

    def test_sync_from_jsonl_imports_cli_events(self):
        # simulate a pre-match CLI append to the jsonl archive
        with open(self.jsonl, "w", encoding="utf-8") as f:
            import json

            f.write(json.dumps(Event(1, "t", "assign_teams", "organizer",
                                     {"teams": {}}).to_dict()) + "\n")
        store = EventStore(self.db, self.jsonl)
        self.assertEqual(len(store.events()), 1)
        e2 = store.append("group_draw", "admin", {"groups": {}})
        self.assertEqual(e2.seq, 2)

    def test_reopen_keeps_events(self):
        EventStore(self.db, self.jsonl).append("group_draw", "admin", {"groups": {}})
        store2 = EventStore(self.db, self.jsonl)
        self.assertEqual(len(store2.events()), 1)


if __name__ == "__main__":
    unittest.main()
