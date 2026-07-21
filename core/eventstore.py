"""SQLite-backed event store for match day.

Multiple volunteers append concurrently; SQLite (WAL mode) serializes the
writes and assigns strictly increasing seq numbers. After every append the
full log is exported back to events.jsonl so the plain-text audit file and
git history stay current.

Pre-match CLI tools append straight to events.jsonl; the store imports any
missing rows on startup, so the jsonl file remains the canonical archive.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import io_utils
from .io_utils import read_events
from .models import Event


class EventStore:
    def __init__(self, db_path: Optional[Path] = None, jsonl_path: Optional[Path] = None):
        self.db_path = db_path or io_utils.data_dir() / "tournament.db"
        self.jsonl_path = jsonl_path or io_utils.events_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.sync_from_jsonl()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    seed INTEGER
                )"""
            )

    def sync_from_jsonl(self) -> None:
        """Import events that exist only in the jsonl archive (e.g. written by
        the pre-match CLI tools)."""
        rows = [
            (e.seq, e.ts, e.type, e.actor, json.dumps(e.payload, ensure_ascii=False), e.seed)
            for e in read_events(self.jsonl_path)
        ]
        if rows:
            with self._conn() as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)", rows
                )

    def events(self) -> List[Event]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT seq, ts, type, actor, payload, seed FROM events ORDER BY seq"
            ).fetchall()
        return [
            Event(seq=r[0], ts=r[1], type=r[2], actor=r[3],
                  payload=json.loads(r[4]), seed=r[5])
            for r in rows
        ]

    def append(
        self, type_: str, actor: str, payload: dict, seed: Optional[int] = None
    ) -> Event:
        ts = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            seq = (conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]) + 1
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (seq, ts, type_, actor, json.dumps(payload, ensure_ascii=False), seed),
            )
        self.export_jsonl()
        return Event(seq=seq, ts=ts, type=type_, actor=actor, payload=payload, seed=seed)

    def export_jsonl(self) -> None:
        """Atomically rewrite the jsonl archive from the database."""
        tmp = self.jsonl_path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for e in self.events():
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, self.jsonl_path)
