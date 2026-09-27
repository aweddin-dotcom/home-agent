import pytest
from datetime import datetime, timezone

from qdrant_client import QdrantClient

from services.embedding.index import EmailIndex
from services.ingestion.calendar_source import fetch_events
from services.ingestion.cleaners import clean_body, html_to_text
from services.ingestion.gmail_source import fetch_messages, parse_message
from services.ingestion.sources import GoogleSource
from services.ingestion.store import SCHEMA_VERSION, Store
from services.ingestion.sync import remove_account, sync_accounts, sync_calendar, sync_emails

from .conftest import FakeCalendar, FakeEmbedder, FakeGmail

CONFIG = {
    "sync": {"email_days": 365, "max_emails": 100, "calendar_days_back": 30, "calendar_days_ahead": 400},
    "chunking": {"size_chars": 1500, "overlap_chars": 200},
    "search": {"collection": "emails", "top_k": 5},
}


def parsed(gmail_messages, message_id):
    return parse_message(next(m for m in gmail_messages if m["id"] == message_id), "gmail-test")


# --- cleaning ---------------------------------------------------------------


def test_strips_quoted_reply_wrapped_over_two_lines_and_signature(gmail_messages):
    body = parsed(gmail_messages, "m-plumber").body
    assert "Tuesday at 9am" in body
    assert "wrote:" not in body
    assert "Can someone come look" not in body
    assert "Licensed & insured" not in body


def test_html_only_email_becomes_text(gmail_messages):
    body = parsed(gmail_messages, "m-school").body
    assert "due Friday, October 2" in body
    assert "<" not in body
    assert "color" not in body  # <style> removed


def test_prefers_plain_part_and_drops_sent_from(gmail_messages):
    body = parsed(gmail_messages, "m-sister").body
    assert body.startswith("Hey! Are we doing Thanksgiving")
    assert "Sent from my iPhone" not in body
    assert "<div>" not in body


def test_forwarded_content_is_kept(gmail_messages):
    body = parsed(gmail_messages, "m-forward").body
    assert "June 12-15" in body
    assert "Forwarded message" in body


def test_html_blockquote_removed(gmail_messages):
    body = parsed(gmail_messages, "m-newsletter").body
    assert "garlic" in body
    assert "tomatoes" not in body


def test_clean_body_collapses_blank_lines():
    assert clean_body("a\n\n\n\n\nb  \t c") == "a\n\nb c"


def test_html_to_text_keeps_line_breaks():
    assert html_to_text("one<br>two").split() == ["one", "two"]


# --- parsing ----------------------------------------------------------------


def test_headers_parsed(gmail_messages):
    email = parsed(gmail_messages, "m-contractor")
    assert email.sender == "Sam Ortiz <sam@ortizbuilds.example>"
    assert email.to == ["Alex Morgan <alex@example.com>"]
    assert email.subject == "Kitchen remodel quote and timeline"
    assert email.date == "2026-09-22T16:05:00-07:00"
    assert email.thread_id == "t-m-contractor"


def test_missing_date_header_falls_back_to_internal_date(gmail_messages):
    message = next(m for m in gmail_messages if m["id"] == "m-dentist")
    message["payload"]["headers"] = [h for h in message["payload"]["headers"] if h["name"] != "Date"]
    assert parse_message(message, "gmail-test").date.startswith("2026-")


def test_attachments_are_not_used_as_body():
    message = {
        "id": "m1",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [],
            "parts": [
                {"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": "YXR0YWNobWVudA"}},
                {"mimeType": "text/plain", "filename": "", "body": {"data": "Ym9keQ"}},
            ],
        },
    }
    assert parse_message(message, "gmail-test").body == "body"


def test_fetch_pages_through_and_skips_known(gmail_messages):
    gmail = FakeGmail(gmail_messages, page_size=2)
    emails = list(fetch_messages(gmail, "gmail-test", "q", max_messages=100, skip_ids={"m-plumber"}))
    assert len(emails) == len(gmail_messages) - 1
    assert "m-plumber" not in gmail.fetched


def test_fetch_respects_max(gmail_messages):
    assert len(list(fetch_messages(FakeGmail(gmail_messages), "gmail-test", "q", max_messages=4))) == 4


# --- calendar ---------------------------------------------------------------


def test_calendar_events_parsed(event_fixtures):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    events = {e.id: e for e in fetch_events(FakeCalendar(event_fixtures), "gmail-test", now, 30, 400)}
    standup, trip = events["ev-standup"], events["ev-trip"]
    assert standup.start == "2026-09-29T09:00:00-04:00" and not standup.all_day
    assert standup.attendees == ["alex@example.com", "priya@example.com"]
    assert trip.all_day and trip.start == "2027-06-12"
    assert "kayak" in trip.description and "<" not in trip.description
    assert events["ev-bookclub"].status == "cancelled"


# --- sync -------------------------------------------------------------------


def chunk_count(gmail_messages):
    """Search-index points for the fixture mailbox (long emails have several)."""
    from services.embedding.chunker import email_chunks

    return sum(len(email_chunks(parse_message(m, "x"), 1500, 200)) for m in gmail_messages)

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
quiet = lambda _: None  # noqa: E731


def make_sync_parts():
    store = Store(":memory:")
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    return store, FakeEmbedder(), index


def source(account="gmail-test", gmail=None, calendar=None):
    return GoogleSource(account, gmail, calendar)


def test_sync_stores_and_indexes_then_skips_on_rerun(gmail_messages):
    store, embedder, index = make_sync_parts()
    assert sync_emails(source(gmail=FakeGmail(gmail_messages)), store, embedder, index, CONFIG, quiet) == len(
        gmail_messages
    )
    assert store.count("emails") == len(gmail_messages)
    assert index.client.count("emails").count == chunk_count(gmail_messages)

    second = FakeGmail(gmail_messages)
    assert sync_emails(source(gmail=second), store, embedder, index, CONFIG, quiet) == 0
    assert second.fetched == []


def test_failed_indexing_leaves_email_for_next_run(gmail_messages):
    store, embedder, index = make_sync_parts()

    class BrokenEmbedder:
        def embed_documents(self, texts):
            raise RuntimeError("ollama down")

    try:
        sync_emails(source(gmail=FakeGmail(gmail_messages)), store, BrokenEmbedder(), index, CONFIG, quiet)
    except RuntimeError:
        pass
    assert store.count("emails") == 0
    assert sync_emails(source(gmail=FakeGmail(gmail_messages)), store, embedder, index, CONFIG, quiet) == len(
        gmail_messages
    )


def test_sync_calendar_replaces_previous_sync(event_fixtures):
    store, _, _ = make_sync_parts()
    sync_calendar(source(calendar=FakeCalendar(event_fixtures)), store, CONFIG, now=NOW, log=quiet)
    assert store.count("events") == len(event_fixtures)
    # An event deleted at the provider disappears locally on the next sync.
    sync_calendar(source(calendar=FakeCalendar(event_fixtures[1:])), store, CONFIG, now=NOW, log=quiet)
    assert {e.id for e in store.all_events()} == {e["id"] for e in event_fixtures[1:]}


def test_sync_logs_counts_not_content(gmail_messages):
    store, embedder, index = make_sync_parts()
    logs = []
    sync_emails(source(gmail=FakeGmail(gmail_messages)), store, embedder, index, CONFIG, logs.append)
    output = " ".join(logs)
    assert "Thanksgiving" not in output and "@" not in output


# --- multiple accounts -------------------------------------------------------


def test_same_ids_in_two_accounts_are_kept_apart(gmail_messages, event_fixtures):
    store, embedder, index = make_sync_parts()
    for account in ("gmail-a", "gmail-b"):
        s = source(account, FakeGmail(gmail_messages), FakeCalendar(event_fixtures))
        sync_emails(s, store, embedder, index, CONFIG, quiet)
        sync_calendar(s, store, CONFIG, now=NOW, log=quiet)
    assert store.count("emails") == 2 * len(gmail_messages)
    assert index.client.count("emails").count == 2 * chunk_count(gmail_messages)
    assert store.count("events", "gmail-a") == store.count("events", "gmail-b") == len(event_fixtures)
    # Re-syncing one account's calendar leaves the other's alone.
    sync_calendar(source("gmail-a", calendar=FakeCalendar([])), store, CONFIG, now=NOW, log=quiet)
    assert store.count("events", "gmail-a") == 0
    assert store.count("events", "gmail-b") == len(event_fixtures)


def test_remove_account_deletes_only_that_accounts_copy(gmail_messages):
    store, embedder, index = make_sync_parts()
    for account in ("gmail-a", "gmail-b"):
        sync_emails(source(account, FakeGmail(gmail_messages)), store, embedder, index, CONFIG, quiet)
    remove_account("gmail-a", store, index, log=quiet)
    assert store.accounts_present() == ["gmail-b"]
    assert index.client.count("emails").count == chunk_count(gmail_messages)


def test_one_failing_account_does_not_stop_the_others(gmail_messages, event_fixtures):
    store, embedder, index = make_sync_parts()

    def connect(label, config):
        if label == "broken":
            raise RuntimeError("token expired")
        return source(label, FakeGmail(gmail_messages), FakeCalendar(event_fixtures))

    logs = []
    accounts = {"broken": {"provider": "google"}, "gmail-b": {"provider": "google"}}
    failed = sync_accounts(accounts, connect, store, embedder, index, CONFIG, logs.append)
    assert failed == ["broken"]
    assert store.accounts_present() == ["gmail-b"]
    assert any("token expired" in line for line in logs)


def test_older_store_layout_is_rebuilt(tmp_path):
    import sqlite3

    path = tmp_path / "structured.db"
    old = sqlite3.connect(path)
    old.execute("create table emails (id text primary key, body text)")
    old.execute("insert into emails values ('x', 'old')")
    old.commit()
    old.close()

    store = Store(path)
    assert store.rebuilt
    assert store.count("emails") == 0
    assert store.db.execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION
    assert not Store(path).rebuilt  # current layout is left alone


def test_readonly_store_before_first_sync(tmp_path):
    path = tmp_path / "structured.db"
    path.touch()
    assert Store(path, readonly=True).all_events() == []


# --- Google rate limits -------------------------------------------------------


def http_error(status, content):
    import httplib2
    from googleapiclient.errors import HttpError

    return HttpError(httplib2.Response({"status": str(status)}), content)


RATE_LIMITED = http_error(403, b'{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}')


class FlakyRequest:
    def __init__(self, failures):
        self.failures = list(failures)

    def execute(self):
        if self.failures:
            raise self.failures.pop(0)
        return {"ok": True}


def test_rate_limit_waits_and_retries():
    from services.ingestion.google_api import execute

    waits, logs = [], []
    request = FlakyRequest([RATE_LIMITED, http_error(429, b"")])
    assert execute(request, log=logs.append, sleep=waits.append) == {"ok": True}
    assert waits == [15, 30]
    assert "waiting 15s" in logs[0]


def test_other_errors_are_not_retried():
    from googleapiclient.errors import HttpError

    from services.ingestion.google_api import execute

    waits = []
    forbidden = http_error(403, b'{"error": {"errors": [{"reason": "insufficientPermissions"}]}}')
    with pytest.raises(HttpError):
        execute(FlakyRequest([forbidden]), log=quiet, sleep=waits.append)
    assert waits == []


def test_gives_up_after_the_last_wait():
    from googleapiclient.errors import HttpError

    from services.ingestion.google_api import execute

    waits = []
    with pytest.raises(HttpError):
        execute(FlakyRequest([RATE_LIMITED] * 3), log=quiet, sleep=waits.append, waits=(1, 2))
    assert waits == [1, 2]
