"""Local SQLite store for synced emails and calendar events (data/structured.db).

This is a copy of what's in the providers, rebuilt by syncing. When the
layout changes (SCHEMA_VERSION), a copy one version back is upgraded in
place where that's cheap (MIGRATIONS); anything older is cleared and
re-synced.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 6

# Two dates this close in one email are read as a span (check-in to check-out).
SPAN_DAYS = 21

DIGESTS_TABLE = """create table if not exists digests (
    day text primary key,  -- YYYY-MM-DD, the user's local date
    created_at text,
    covers_from text,      -- mail received from this moment was considered
    body text
)"""

SYNC_STATUS_TABLE = """create table if not exists sync_status (
    account text primary key,
    last_attempt text,
    last_success text,
    ok integer,
    detail text       -- counts on success, the error on failure
)"""

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
    mentioned_dates text default '[]',  -- JSON list of ISO dates the email mentions
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
""" + SYNC_STATUS_TABLE + ";\n" + DIGESTS_TABLE + ";\n"

def _email_dates(subject, body, received):
    from .dates import mentioned_dates

    try:
        arrived = datetime.fromisoformat(received).date()
    except (TypeError, ValueError):
        return []
    return mentioned_dates(f"{subject}\n{body}", arrived)


def _backfill_mentioned_dates(db):
    rows = db.execute("select account, id, subject, body, date from emails").fetchall()
    db.executemany(
        "update emails set mentioned_dates = ? where account = ? and id = ?",
        [(json.dumps(_email_dates(subject, body, received)), account, email_id)
         for account, email_id, subject, body, received in rows],
    )


def _add_column(table, column, declaration):
    """A migration step that adds a column unless it's already there."""
    def step(db):
        existing = {row[1] for row in db.execute(f"pragma table_info({table})")}
        if column not in existing:
            db.execute(f"alter table {table} add column {column} {declaration}")
    return step


# Upgrades from one version to the next, applied in place: SQL statements,
# or functions given the connection.
MIGRATIONS = {
    2: [_add_column("emails", "folders", "text default '[]'")],
    3: [SYNC_STATUS_TABLE],
    4: [DIGESTS_TABLE],
    5: [_add_column("emails", "mentioned_dates", "text default '[]'"), _backfill_mentioned_dates],
}

EVENT_COLUMNS = (
    "account, id, calendar_id, summary, start, end, all_day, location, description, attendees,"
    " organizer, status, ical_uid"
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _moment(row):
    """An email's received time, comparable across providers and zones."""
    try:
        return datetime.fromisoformat(row["date"]).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


class Store:
    def __init__(self, path, readonly=False):
        self.rebuilt = False
        if readonly:
            # For services that only read (the chat API). Tolerates a database
            # that hasn't been synced yet, or doesn't exist yet.
            if Path(path).is_file():
                self.db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
            else:
                self.db = sqlite3.connect(":memory:")
            return
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self._ensure_schema()

    def _ensure_schema(self):
        version = self.db.execute("pragma user_version").fetchone()[0]
        while version in MIGRATIONS and version < SCHEMA_VERSION:
            with self.db:
                for step in MIGRATIONS[version]:
                    step(self.db) if callable(step) else self.db.execute(step)
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
            " labels, body, snippet, synced_at, mentioned_dates) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["account"], e["id"], e["thread_id"], e["sender"], json.dumps(e["to"]), json.dumps(e["cc"]),
                e["date"], e["subject"], json.dumps(e["labels"]), e["body"], e["snippet"], _now(),
                json.dumps(_email_dates(e["subject"], e["body"], e["date"])),
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

    def email_body(self, account, email_id):
        row = self._read_one("select body from emails where account = ? and id = ?", (account, email_id))
        return row[0] if row else ""

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
        inside = sorted(
            (r for r in self._email_rows() if set(r["folders"]) & set(matched)), key=_moment, reverse=True
        )
        return matched, len(inside), inside[:limit]

    def _email_rows(self):
        return [
            {"account": r[0], "email_id": r[1], "date": r[2], "sender": r[3], "subject": r[4],
             "snippet": r[5], "folders": json.loads(r[6] or "[]"), "body": r[7]}
            for r in self._read_all("select account, id, date, sender, subject, snippet, folders, body from emails")
        ]

    def emails_between(self, start, end, tz, limit=15):
        """Emails received from `start` through `end` (dates, in the user's time
        zone), newest first. Returns (total count, rows). Dates are compared as
        moments, since providers write them in different zones."""
        inside = []
        for row in self._email_rows():
            try:
                received = datetime.fromisoformat(row["date"]).astimezone(tz).date()
            except (TypeError, ValueError):
                continue
            if start <= received <= end:
                inside.append(row)
        inside.sort(key=_moment, reverse=True)
        return len(inside), inside[:limit]

    def emails_mentioning(self, start, end, limit=8):
        """Emails that mention a date from `start` through `end` (reservations,
        deadlines, plans), newest first. Two dates in one email up to
        SPAN_DAYS apart count as a span (check-in to check-out), so a day
        in between matches too. Returns (total count, rows); each row has
        "mentions", the matching dates or spans."""
        lo, hi = start.isoformat(), end.isoformat()
        matched = []
        for r in self._read_all("select account, id, date, sender, subject, snippet, folders, body,"
                                " mentioned_dates from emails"):
            dates = sorted(json.loads(r[8] or "[]"))
            mentions = [d for d in dates if lo <= d <= hi]
            for first, last in zip(dates, dates[1:]):
                close = date.fromisoformat(last) - date.fromisoformat(first) <= timedelta(days=SPAN_DAYS)
                if close and first < lo <= last or close and first <= hi < last:
                    mentions.append(f"{first} to {last}")
            if mentions:
                matched.append({"account": r[0], "email_id": r[1], "date": r[2], "sender": r[3], "subject": r[4],
                                "snippet": r[5], "folders": json.loads(r[6] or "[]"), "body": r[7],
                                "mentions": mentions})
        matched.sort(key=_moment, reverse=True)
        return len(matched), matched[:limit]

    def stats(self, tz, day=None):
        """Counts only, per account: emails, oldest/newest received date, events,
        and (with `day`) emails received that day. No content."""
        result = {}
        for row in self._email_rows():
            entry = result.setdefault(row["account"], {"emails": 0, "oldest": None, "newest": None, "on_day": 0})
            try:
                received = datetime.fromisoformat(row["date"]).astimezone(tz).date()
            except (TypeError, ValueError):
                continue
            entry["emails"] += 1
            entry["oldest"] = min(filter(None, [entry["oldest"], received]))
            entry["newest"] = max(filter(None, [entry["newest"], received]))
            if day and received == day:
                entry["on_day"] += 1
        for account, count in self._read_all("select account, count(*) from events group by account"):
            result.setdefault(account, {"emails": 0, "oldest": None, "newest": None, "on_day": 0})["events"] = count
        return result

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

    # --- sync status ---

    def record_sync(self, account, ok, detail, when=None):
        when = when or _now()
        with self.db:
            self.db.execute(
                "insert into sync_status (account, last_attempt, last_success, ok, detail) values (?, ?, ?, ?, ?)"
                " on conflict (account) do update set last_attempt = excluded.last_attempt,"
                " last_success = coalesce(excluded.last_success, sync_status.last_success),"
                " ok = excluded.ok, detail = excluded.detail",
                (account, when, when if ok else None, int(ok), detail),
            )

    def sync_statuses(self):
        rows = self._read_all("select account, last_attempt, last_success, ok, detail from sync_status")
        return {
            r[0]: {"last_attempt": r[1], "last_success": r[2], "ok": bool(r[3]), "detail": r[4]} for r in rows
        }

    # --- digests ---

    def emails_since(self, moment):
        """Emails received at or after a moment, newest first."""
        return sorted((r for r in self._email_rows() if _moment(r) >= moment), key=_moment, reverse=True)

    def save_digest(self, day, body, covers_from, created_at=None):
        with self.db:
            self.db.execute(
                "insert or replace into digests (day, created_at, covers_from, body) values (?, ?, ?, ?)",
                (day.isoformat(), created_at or _now(), covers_from, body),
            )

    def _digest(self, where, params=()):
        row = self._read_one(f"select day, created_at, covers_from, body from digests {where}", params)
        return dict(zip(("day", "created_at", "covers_from", "body"), row)) if row else None

    def digest_for(self, day):
        return self._digest("where day = ?", (day.isoformat(),))

    def latest_digest(self):
        return self._digest("order by day desc limit 1")

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
