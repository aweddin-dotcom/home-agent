"""Local SQLite store for synced emails and calendar events (data/structured.db).

This is a copy of what's in the providers, rebuilt by syncing. When the
layout changes (SCHEMA_VERSION), a copy one version back is upgraded in
place where that's cheap (MIGRATIONS); anything older is cleared and
re-synced.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
create table emails (
    account text,
    id text,
    thread_id text,
    sender text,
    recipients text,  -- JSON list
    cc text,          -- JSON list
    date text,
    subject text,
    labels text,      -- JSON list
    body text,
    snippet text,
    synced_at text,
    folders text default '[]',  -- JSON list of folder/label names
    primary key (account, id)
);
create table events (
    account text,
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
    ical_uid text,
    synced_at text,
    primary key (account, calendar_id, id)
);
"""

# Upgrades from one version to the next, applied in place.
MIGRATIONS = {
    2: ["alter table emails add column folders text default '[]'"],
}

EVENT_COLUMNS = (
    "account, id, calendar_id, summary, start, end, all_day, location, description, attendees,"
    " organizer, status, ical_uid"
)


def _now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, readonly=False):
        self.rebuilt = False
        if readonly:
            # For services that only read (the chat API). Tolerates a database
            # that hasn't been synced yet.
            self.db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
            return
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self._ensure_schema()

    def _ensure_schema(self):
        version = self.db.execute("pragma user_version").fetchone()[0]
        while version in MIGRATIONS and version < SCHEMA_VERSION:
            with self.db:
                for statement in MIGRATIONS[version]:
                    self.db.execute(statement)
                version += 1
                self.db.execute(f"pragma user_version = {version}")
        if version == SCHEMA_VERSION:
            return
        tables = [r[0] for r in self.db.execute("select name from sqlite_master where type = 'table'")]
        self.rebuilt = bool(tables)  # an older copy existed and is being cleared
        with self.db:
            for table in tables:
                self.db.execute(f'drop table "{table}"')
            self.db.executescript(SCHEMA)
            self.db.execute(f"pragma user_version = {SCHEMA_VERSION}")

    # --- emails ---

    def known_email_ids(self, account):
        return {row[0] for row in self.db.execute("select id from emails where account = ?", (account,))}

    def save_email(self, email):
        e = asdict(email)
        self.db.execute(
            "insert or replace into emails (account, id, thread_id, sender, recipients, cc, date, subject,"
            " labels, body, snippet, synced_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["account"], e["id"], e["thread_id"], e["sender"], json.dumps(e["to"]), json.dumps(e["cc"]),
                e["date"], e["subject"], json.dumps(e["labels"]), e["body"], e["snippet"], _now(),
            ),
        )
        self.db.commit()

    def update_folders(self, account, folders_by_id):
        """Set the folder/label names of an account's emails, e.g. after the
        user moved some. Emails not in the mapping are left as they are."""
        with self.db:
            self.db.executemany(
                "update emails set folders = ? where account = ? and id = ?",
                [(json.dumps(sorted(names)), account, email_id) for email_id, names in folders_by_id.items()],
            )

    def folders_for(self, keys):
        """{(account, id): [folder names]} for the given emails."""
        result = {}
        for account, email_id in keys:
            row = self._read_one("select folders from emails where account = ? and id = ?", (account, email_id))
            result[(account, email_id)] = json.loads(row[0]) if row and row[0] else []
        return result

    def folder_names(self):
        names = set()
        for (folders,) in self._read_all("select distinct folders from emails"):
            names.update(json.loads(folders or "[]"))
        return sorted(names)

    def emails_in_folder(self, name, limit=10):
        """Most recent emails in any folder whose name contains every word of
        `name`. Returns (matching folder names, total count, rows)."""
        words = name.lower().split()
        matched = sorted(n for n in self.folder_names() if all(w in n.lower() for w in words))
        if not matched:
            return [], 0, []
        rows = [
            {"account": r[0], "email_id": r[1], "date": r[2], "sender": r[3], "subject": r[4],
             "snippet": r[5], "folders": json.loads(r[6] or "[]")}
            for r in self._read_all("select account, id, date, sender, subject, snippet, folders from emails")
        ]
        inside = sorted((r for r in rows if set(r["folders"]) & set(matched)), key=lambda r: r["date"], reverse=True)
        return matched, len(inside), inside[:limit]

    def _read_all(self, sql, params=()):
        try:
            return self.db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:  # read-only store before the first sync
            return []

    def _read_one(self, sql, params=()):
        rows = self._read_all(sql, params)
        return rows[0] if rows else None

    # --- events ---

    def replace_events(self, account, events):
        """Replace an account's events with a fresh sync, so events deleted or
        moved out of the sync window at the provider disappear here too."""
        now = _now()
        with self.db:
            self.db.execute("delete from events where account = ?", (account,))
            self.db.executemany(
                f"insert or replace into events ({EVENT_COLUMNS}, synced_at)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        account, ev.id, ev.calendar_id, ev.summary, ev.start, ev.end, int(ev.all_day),
                        ev.location, ev.description, json.dumps(ev.attendees), ev.organizer, ev.status,
                        ev.ical_uid, now,
                    )
                    for ev in events
                ],
            )

    def all_events(self):
        from .calendar_source import Event

        try:
            rows = self.db.execute(f"select {EVENT_COLUMNS} from events").fetchall()
        except sqlite3.OperationalError:  # read-only store before the first sync
            return []
        return [
            Event(
                account=r[0], id=r[1], calendar_id=r[2], summary=r[3], start=r[4], end=r[5], all_day=bool(r[6]),
                location=r[7], description=r[8], attendees=json.loads(r[9]), organizer=r[10], status=r[11],
                ical_uid=r[12],
            )
            for r in rows
        ]

    # --- accounts ---

    def remove_account(self, account):
        """Delete an account's local copy. Nothing at the provider is touched."""
        with self.db:
            self.db.execute("delete from emails where account = ?", (account,))
            self.db.execute("delete from events where account = ?", (account,))

    def accounts_present(self):
        rows = self.db.execute("select account from emails union select account from events")
        return sorted(r[0] for r in rows)

    def count(self, table, account=None):
        assert table in ("emails", "events")
        if account is None:
            return self.db.execute(f"select count(*) from {table}").fetchone()[0]
        return self.db.execute(f"select count(*) from {table} where account = ?", (account,)).fetchone()[0]
