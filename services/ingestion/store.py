"""Local SQLite store for synced emails and calendar events (data/structured.db)."""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

SCHEMA = """
create table if not exists emails (
    id text primary key,
    thread_id text,
    sender text,
    recipients text,  -- JSON list
    cc text,          -- JSON list
    date text,
    subject text,
    labels text,      -- JSON list
    body text,
    snippet text,
    synced_at text
);
create table if not exists events (
    calendar_id text,
    id text,
    summary text,
    start text,
    end text,
    all_day integer,
    location text,
    description text,
    attendees text,   -- JSON list
    organizer text,
    status text,
    synced_at text,
    primary key (calendar_id, id)
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def known_email_ids(self):
        return {row[0] for row in self.db.execute("select id from emails")}

    def save_email(self, email):
        e = asdict(email)
        self.db.execute(
            "insert or replace into emails (id, thread_id, sender, recipients, cc, date, subject,"
            " labels, body, snippet, synced_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["id"], e["thread_id"], e["sender"], json.dumps(e["to"]), json.dumps(e["cc"]),
                e["date"], e["subject"], json.dumps(e["labels"]), e["body"], e["snippet"], _now(),
            ),
        )
        self.db.commit()

    def replace_events(self, events):
        """Replace all stored events with a fresh sync, so events deleted or
        moved out of the sync window in Google disappear here too."""
        with self.db:
            self.db.execute("delete from events")
            self._insert_events(events)

    def all_events(self):
        from .calendar_source import Event

        rows = self.db.execute(
            "select id, calendar_id, summary, start, end, all_day, location, description, attendees,"
            " organizer, status from events"
        )
        return [
            Event(
                id=r[0], calendar_id=r[1], summary=r[2], start=r[3], end=r[4], all_day=bool(r[5]),
                location=r[6], description=r[7], attendees=json.loads(r[8]), organizer=r[9], status=r[10],
            )
            for r in rows
        ]

    def _insert_events(self, events):
        now = _now()
        self.db.executemany(
            "insert or replace into events (calendar_id, id, summary, start, end, all_day, location,"
            " description, attendees, organizer, status, synced_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    ev.calendar_id, ev.id, ev.summary, ev.start, ev.end, int(ev.all_day), ev.location,
                    ev.description, json.dumps(ev.attendees), ev.organizer, ev.status, now,
                )
                for ev in events
            ],
        )

    def count(self, table):
        assert table in ("emails", "events")
        return self.db.execute(f"select count(*) from {table}").fetchone()[0]
