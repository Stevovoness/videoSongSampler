"""A small SQLite database: which ideas, people and songs were used when, and what happened to each run."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    run_id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    idea_id TEXT,
    person TEXT,
    song TEXT,
    status TEXT NOT NULL            -- built | approved | published | skipped | expired | failed
);
CREATE TABLE IF NOT EXISTS posts (
    run_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    post_id TEXT,
    posted_at TEXT,
    PRIMARY KEY (run_id, platform)
);
CREATE TABLE IF NOT EXISTS claims (
    channel TEXT PRIMARY KEY,       -- a footage source that claimed one of our videos: blocked from then on
    noted_at TEXT
);
"""


class DB:
    def __init__(self, path: str | Path):
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.executescript(SCHEMA)

    def record(self, run_id: str, day: str, idea_id: str | None, person: str | None, song: str | None,
               status: str) -> None:
        with self.con:
            self.con.execute("INSERT INTO history VALUES (?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                             "idea_id=excluded.idea_id, person=excluded.person, song=excluded.song, "
                             "status=excluded.status", (run_id, day, idea_id, person, song, status))

    def used_ideas(self) -> set[str]:
        rows = self.con.execute("SELECT idea_id FROM history WHERE status IN ('approved','published')")
        return {r[0] for r in rows if r[0]}

    def recent_people(self, today: date, days: int) -> set[str]:
        since = (today - timedelta(days=days)).isoformat()
        rows = self.con.execute("SELECT person FROM history WHERE day >= ? AND status IN ('approved','published')",
                                (since,))
        return {r[0].lower() for r in rows if r[0]}

    def blocked_channels(self) -> set[str]:
        return {r[0] for r in self.con.execute("SELECT channel FROM claims")}
