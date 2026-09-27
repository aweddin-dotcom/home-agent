"""Plans that only exist in email: dates emails mention, and the calendar's
sync horizon. All data is invented."""

import json
import sqlite3
from datetime import date

import pytest

from services.ingestion.dates import excerpt_around, mentioned_dates
from services.ingestion.store import SCHEMA_VERSION, Store
from services.retrieval.ask import format_sources, gather
from services.retrieval.router import Route
from services.retrieval.structured_query import format_calendar, lookup

from .test_email_dates import TODAY, TZ, route
from .test_folders import RouteChat, synced_store

AUG_30 = date(2026, 8, 30)


@pytest.mark.parametrize("text, dates", [
    ("Arrive Monday, March 22; depart Saturday, March 27.", ["2027-03-22", "2027-03-27"]),  # next March
    ("Your order from Aug 3 was delivered", ["2026-08-03"]),  # recent past stays this year
    ("Check-in: Tuesday, October 20, 2026", ["2026-10-20"]),
    ("the 23rd of March", ["2027-03-23"]),
    ("due 10/2/2026, renew 3/23/27, on 2027-03-24", ["2026-10-02", "2027-03-23", "2027-03-24"]),
    ("half 1/2 cup; May I ask; Feb 30", []),
])
def test_mentioned_dates(text, dates):
    assert mentioned_dates(text, AUG_30) == dates


def test_excerpt_is_around_the_matching_date():
    text = "Intro text. " * 50 + "Arrive: Monday, March 22 (check-in from 4pm)" + " Footer." * 50
    excerpt = excerpt_around(text, AUG_30, date(2027, 3, 1), date(2027, 3, 31), width=40)
    assert "Arrive: Monday, March 22 (check-in from 4pm)" in excerpt and excerpt.startswith("...")


def test_emails_mentioning_a_range(gmail_messages, event_fixtures):
    store, _ = synced_store(gmail_messages, event_fixtures)
    total, rows = store.emails_mentioning(date(2027, 3, 1), date(2027, 3, 31))
    assert total == 1 and rows[0]["email_id"] == "m-spring"
    assert rows[0]["mentions"] == ["2027-03-22", "2027-03-27"]
    _, rows = store.emails_mentioning(date(2026, 10, 20), date(2026, 10, 20))
    assert [r["email_id"] for r in rows] == ["m-hotel"]


def test_a_day_inside_a_stay_matches_the_span(gmail_messages, event_fixtures):
    """The email says arrive the 22nd, depart the 27th: the 24th is part of the stay."""
    store, _ = synced_store(gmail_messages, event_fixtures)
    _, rows = store.emails_mentioning(date(2027, 3, 24), date(2027, 3, 24))
    assert [r["email_id"] for r in rows] == ["m-spring"]
    assert rows[0]["mentions"] == ["2027-03-22 to 2027-03-27"]
    # Dates far apart in one email (a quote now, work starting months later) aren't a span.
    _, rows = store.emails_mentioning(date(2027, 1, 15), date(2027, 1, 15))
    assert rows == []


def test_future_question_lists_emails_that_mention_the_dates(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({**route(["calendar"], "2027-03-01", "2027-03-31"), "calendar_keywords": []})
    context = gather("what travel do I have for spring break?", FakeEmbedderFor(), index, chat, 3, [], TODAY, TZ, store)
    mentions = next(s for s in context.sections if s.startswith("Emails that mention dates"))
    assert "[M1] Received 2026-08-30" in mentions and "Mentions: 2027-03-22, 2027-03-27" in mentions
    assert "Arrive: Monday, March 22" in mentions
    assert "Emails mentioning those dates:" in format_sources(context, TZ)


def test_questions_about_when_mail_arrived_dont_list_mentions(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat(route(["email"], "2026-09-24"))  # "the email from Sept 24th"
    context = gather("the email from sept 24", FakeEmbedderFor(), index, chat, 3, [], TODAY, TZ, store)
    assert context.mention_rows == []


def FakeEmbedderFor():
    from .conftest import FakeEmbedder

    return FakeEmbedder()


def test_calendar_beyond_the_sync_horizon_isnt_claimed_complete(event_fixtures):
    from services.ingestion.calendar_source import parse_event

    events = [parse_event(e, "primary", "gmail") for e in event_fixtures]
    spring = Route(["calendar"], date(2027, 3, 1), date(2027, 3, 31))
    beyond = format_calendar(lookup(spring, events, TODAY, TZ, horizon=date(2026, 12, 26)), TZ)
    assert "only synced through Saturday 2026-12-26" in beyond and "complete" not in beyond
    within = format_calendar(lookup(spring, events, TODAY, TZ, horizon=date(2027, 9, 27)), TZ)
    assert "This list is complete" in within


def test_version_5_store_gets_mentioned_dates_backfilled(tmp_path):
    path = tmp_path / "structured.db"
    store = Store(path)
    store.db.execute("insert into emails (account, id, subject, body, date) values "
                     "('gmail', 'e1', 'Booking', 'Arrive Monday, March 22', '2026-08-30T16:10:00-04:00')")
    store.db.execute("alter table emails drop column mentioned_dates")
    store.db.execute("pragma user_version = 5")
    store.db.commit()
    store.db.close()

    upgraded = Store(path)
    assert not upgraded.rebuilt
    assert upgraded.db.execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION
    stored = upgraded.db.execute("select mentioned_dates from emails where id = 'e1'").fetchone()[0]
    assert json.loads(stored) == ["2027-03-22"]
