from datetime import datetime, timezone

from qdrant_client import QdrantClient

from services.embedding.index import EmailIndex
from services.ingestion.calendar_source import fetch_events
from services.ingestion.cleaners import clean_body, html_to_text
from services.ingestion.gmail_source import fetch_messages, parse_message
from services.ingestion.store import Store
from services.ingestion.sync import sync_calendar, sync_emails

from .conftest import FakeCalendar, FakeEmbedder, FakeGmail

CONFIG = {
    "sync": {"email_days": 365, "max_emails": 100, "calendar_days_back": 30, "calendar_days_ahead": 400},
    "chunking": {"size_chars": 1500, "overlap_chars": 200},
    "search": {"collection": "emails", "top_k": 5},
}


def parsed(gmail_messages, message_id):
    return parse_message(next(m for m in gmail_messages if m["id"] == message_id))


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
    assert parse_message(message).date.startswith("2026-")


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
    assert parse_message(message).body == "body"


def test_fetch_pages_through_and_skips_known(gmail_messages):
    gmail = FakeGmail(gmail_messages, page_size=2)
    emails = list(fetch_messages(gmail, "q", max_messages=100, skip_ids={"m-plumber"}))
    assert len(emails) == len(gmail_messages) - 1
    assert "m-plumber" not in gmail.fetched


def test_fetch_respects_max(gmail_messages):
    assert len(list(fetch_messages(FakeGmail(gmail_messages), "q", max_messages=4))) == 4


# --- calendar ---------------------------------------------------------------


def test_calendar_events_parsed(event_fixtures):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    events = {e.id: e for e in fetch_events(FakeCalendar(event_fixtures), now, 30, 400)}
    standup, trip = events["ev-standup"], events["ev-trip"]
    assert standup.start == "2026-09-29T09:00:00-07:00" and not standup.all_day
    assert standup.attendees == ["alex@example.com", "priya@example.com"]
    assert trip.all_day and trip.start == "2027-06-12"
    assert trip.description == "Bring the\nkayak" or "kayak" in trip.description
    assert "<" not in trip.description


# --- sync -------------------------------------------------------------------


def make_sync_parts():
    store = Store(":memory:")
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    return store, FakeEmbedder(), index


def test_sync_stores_and_indexes_then_skips_on_rerun(gmail_messages):
    store, embedder, index = make_sync_parts()
    logs = []
    assert sync_emails(FakeGmail(gmail_messages), store, embedder, index, CONFIG, log=logs.append) == len(
        gmail_messages
    )
    assert store.count("emails") == len(gmail_messages)
    assert index.client.count("emails").count == len(gmail_messages)  # one chunk each

    second = FakeGmail(gmail_messages)
    assert sync_emails(second, store, embedder, index, CONFIG, log=logs.append) == 0
    assert second.fetched == []


def test_failed_indexing_leaves_email_for_next_run(gmail_messages):
    store, embedder, index = make_sync_parts()

    class BrokenEmbedder:
        def embed_documents(self, texts):
            raise RuntimeError("ollama down")

    try:
        sync_emails(FakeGmail(gmail_messages), store, BrokenEmbedder(), index, CONFIG, log=lambda _: None)
    except RuntimeError:
        pass
    assert store.count("emails") == 0
    assert sync_emails(FakeGmail(gmail_messages), store, embedder, index, CONFIG, log=lambda _: None) == len(
        gmail_messages
    )


def test_sync_calendar_upserts(event_fixtures):
    store, _, _ = make_sync_parts()
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    for _ in range(2):
        sync_calendar(FakeCalendar(event_fixtures), store, CONFIG, now=now, log=lambda _: None)
    assert store.count("events") == 2


def test_sync_logs_counts_not_content(gmail_messages):
    store, embedder, index = make_sync_parts()
    logs = []
    sync_emails(FakeGmail(gmail_messages), store, embedder, index, CONFIG, log=logs.append)
    output = " ".join(logs)
    assert "Thanksgiving" not in output and "@" not in output
