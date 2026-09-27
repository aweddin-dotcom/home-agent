"""iCloud calendars via CalDAV, with invented iCalendar data."""

from dataclasses import replace
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from services.ingestion.icloud_source import events_from_ics, fetch_events
from services.ingestion.sources import ICloudSource
from services.ingestion.store import Store
from services.ingestion.sync import sync_accounts
from services.retrieval.router import Route
from services.retrieval.structured_query import describe_time, lookup, merge_duplicates

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
START = datetime.combine(date(2026, 8, 28), time.min, TZ)
END = datetime.combine(date(2026, 12, 26), time.min, TZ)


def ics(*events):
    body = "\n".join(events)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n{body}\nEND:VCALENDAR\n".replace("\n", "\r\n")


PIANO = """BEGIN:VEVENT
UID:piano@example.com
SUMMARY:Piano lesson
DTSTART;TZID=America/New_York:20260930T160000
DTEND;TZID=America/New_York:20260930T164500
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=America/New_York:20261007T160000
LOCATION:Music studio
END:VEVENT
BEGIN:VEVENT
UID:piano@example.com
RECURRENCE-ID;TZID=America/New_York:20261014T160000
SUMMARY:Piano lesson (moved)
DTSTART;TZID=America/New_York:20261015T170000
DTEND;TZID=America/New_York:20261015T174500
END:VEVENT"""

VET = """BEGIN:VEVENT
UID:vet@example.com
SUMMARY:Vet appointment
DTSTART:20261002T140000Z
DTEND:20261002T143000Z
ATTENDEE;CN=Clinic:mailto:front@vet.example
ORGANIZER:mailto:alex@example.com
DESCRIPTION:Annual checkup. Bring vaccination records.
END:VEVENT"""

VACATION = """BEGIN:VEVENT
UID:beach@example.com
SUMMARY:Beach week
DTSTART;VALUE=DATE:20261101
DTEND;VALUE=DATE:20261108
END:VEVENT"""

FLOATING = """BEGIN:VEVENT
UID:yoga@example.com
SUMMARY:Yoga
DTSTART:20261003T080000
DTEND:20261003T090000
END:VEVENT"""

CANCELLED = """BEGIN:VEVENT
UID:gala@example.com
SUMMARY:Charity gala
DTSTART;TZID=America/New_York:20261010T190000
DTEND;TZID=America/New_York:20261010T220000
STATUS:CANCELLED
END:VEVENT"""

OUTSIDE = """BEGIN:VEVENT
UID:old@example.com
SUMMARY:Long ago
DTSTART;VALUE=DATE:20250101
DTEND;VALUE=DATE:20250102
END:VEVENT"""


def parse(*events):
    return events_from_ics(ics(*events), "cal-home", "icloud", START, END, TZ)


# --- parsing ------------------------------------------------------------------


def test_recurring_series_with_exclusion_and_moved_occurrence():
    lessons = sorted(parse(PIANO), key=lambda e: e.start)
    assert [(e.summary, e.start) for e in lessons] == [
        ("Piano lesson", "2026-09-30T16:00:00-04:00"),
        ("Piano lesson (moved)", "2026-10-15T17:00:00-04:00"),  # moved from Wed Oct 14
        ("Piano lesson", "2026-10-21T16:00:00-04:00"),
    ]  # Oct 7 was excluded
    assert len({e.id for e in lessons}) == 3  # each occurrence has its own id
    assert {e.ical_uid for e in lessons} == {"piano@example.com"}


def test_utc_event_fields():
    (vet,) = parse(VET)
    assert vet.start == "2026-10-02T14:00:00+00:00"
    assert describe_time(vet, TZ) == "Fri 2026-10-02, 10:00am-10:30am"
    assert vet.attendees == ["front@vet.example"] and vet.organizer == "alex@example.com"
    assert "vaccination records" in vet.description


def test_all_day_multi_day_event():
    (beach,) = parse(VACATION)
    assert beach.all_day and (beach.start, beach.end) == ("2026-11-01", "2026-11-08")
    assert describe_time(beach, TZ) == "Sun 2026-11-01 to Sat 2026-11-07, all day"


def test_floating_time_is_taken_as_local():
    (yoga,) = parse(FLOATING)
    assert yoga.start == "2026-10-03T08:00:00-04:00"


def test_cancelled_is_marked_and_outside_window_is_dropped():
    events = parse(CANCELLED, OUTSIDE)
    assert [(e.summary, e.status) for e in events] == [("Charity gala", "cancelled")]


# --- fetching -----------------------------------------------------------------


class FakeObject:
    def __init__(self, data):
        self.data = data


class FakeCalendar:
    def __init__(self, name, *objects):
        self.name, self.url = name, f"https://caldav.example/{name}/"
        self.objects = [FakeObject(o) for o in objects]
        self.searches = []

    def search(self, start, end, event):
        self.searches.append((start, end, event))
        return self.objects


def test_fetch_reads_every_calendar_except_skipped():
    home = FakeCalendar("Home", ics(VET), ics(PIANO))
    birthdays = FakeCalendar("Birthdays", ics(VACATION))
    events = fetch_events([home, birthdays], "icloud", NOW, 30, 90, TZ, skip=["birthdays"])
    assert {e.summary for e in events} == {"Vet appointment", "Piano lesson", "Piano lesson (moved)"}
    assert birthdays.searches == []
    start, end, event_only = home.searches[0]
    assert event_only and start.date() == date(2026, 8, 28) and end.date() == date(2026, 12, 26)
    assert {e.calendar_id for e in events} == {"https://caldav.example/Home/"}


def test_sync_stores_icloud_calendar_only():
    store = Store(":memory:")
    source = ICloudSource("icloud", [FakeCalendar("Home", ics(VET))], TZ)
    config = {"sync": {"email_days": 365, "max_emails": 100, "calendar_days_back": 30, "calendar_days_ahead": 90}}
    failed = sync_accounts({"icloud": {"provider": "icloud"}}, lambda label, cfg: source, store, None, None, config,
                           log=lambda _: None)
    assert failed == []
    assert store.count("events", "icloud") == 1 and store.count("emails") == 0


# --- merging with other providers ------------------------------------------------


def test_same_event_from_google_and_icloud_is_shown_once():
    (vet,) = parse(VET)  # iCloud writes it in UTC
    google_copy = replace(vet, account="gmail", id="g-123", calendar_id="primary",
                          start="2026-10-02T10:00:00-04:00", end="2026-10-02T10:30:00-04:00")
    assert len(merge_duplicates([vet, google_copy])) == 1
    result = lookup(Route(["calendar"], date(2026, 10, 2), date(2026, 10, 2)), [vet, google_copy], NOW.date(), TZ)
    assert len(result.events) == 1


def test_recurring_occurrences_are_not_merged_together():
    assert len(merge_duplicates(parse(PIANO))) == 3


# --- secrets ----------------------------------------------------------------------


def test_secret_read_from_container_path(tmp_path, monkeypatch):
    from services.common import secrets

    (tmp_path / "icloud_apple_id").write_text("someone@example.com\n")
    monkeypatch.setattr(secrets, "SECRETS_DIR", tmp_path)
    assert secrets.get_secret("icloud_apple_id") == "someone@example.com"


def test_missing_secret_says_how_to_set_it(tmp_path, monkeypatch):
    import keyring

    from services.common import secrets

    monkeypatch.setattr(secrets, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(keyring, "get_password", lambda service, name: None)
    with pytest.raises(secrets.MissingSecret, match="secrets_cli.py set icloud_app_password"):
        secrets.get_secret("icloud_app_password")
