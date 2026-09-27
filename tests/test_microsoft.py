"""Outlook/Hotmail via a fake Microsoft Graph. All data is invented."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from services.common.microsoft_creds import GRAPH, missing_scopes
from services.ingestion.graph_api import GraphClient, GraphError
from services.ingestion.microsoft_source import fetch_events, fetch_messages, parse_graph_event, parse_graph_message
from services.ingestion.sources import MicrosoftSource
from services.ingestion.store import Store
from services.ingestion.sync import sync_calendar, sync_emails
from services.retrieval.router import Route
from services.retrieval.structured_query import describe_time, lookup

from .conftest import FakeEmbedder

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def message(id, subject="Hello", body="Hi there", folder="inbox", content_type="text", **extra):
    return {
        "id": id,
        "conversationId": f"conv-{id}",
        "subject": subject,
        "from": {"emailAddress": {"name": "Jordan Lee", "address": "jordan@example.net"}},
        "toRecipients": [{"emailAddress": {"name": "Alex Morgan", "address": "alex@example.com"}}],
        "ccRecipients": [],
        "receivedDateTime": "2026-09-25T14:30:00Z",
        "body": {"contentType": content_type, "content": body},
        "bodyPreview": body[:40],
        "parentFolderId": folder,
        "isDraft": False,
        **extra,
    }


MESSAGES = [
    message("o-1", "Soccer schedule", "Practice moves to Wednesdays at 5:30pm starting October 7."),
    message("o-2", "Re: Car service", "Tuesday works, see you at 8.\n\n________________________________\n"
                                      "From: Alex Morgan\nSent: Monday, September 21, 2026\nTo: Service\n"
                                      "Can I bring the car in Tuesday?"),
    message("o-3", "FW: Concert tickets", "Sharing these.\n\n________________________________\n"
                                           "From: Box Office\nSent: Friday\n\nTwo seats, row F, November 14."),
    message("o-4", "Win a prize", "Click here", folder="junk"),
    message("o-5", "Old newsletter", "Deleted", folder="deleted"),
    message("o-6", "Unsent", "Draft text", isDraft=True),
    message("o-7", "Invoice", "<p>Amount due: <b>$89.00</b></p>", content_type="html"),
]

EVENTS = {
    "cal-main": [
        {"id": "oe-1", "subject": "Parent-teacher conference", "isAllDay": False,
         "start": {"dateTime": "2026-10-06T22:00:00.0000000", "timeZone": "UTC"},
         "end": {"dateTime": "2026-10-06T22:30:00.0000000", "timeZone": "UTC"},
         "location": {"displayName": "Room 12"}, "body": {"contentType": "text", "content": "Bring report card"},
         "attendees": [{"emailAddress": {"address": "teacher@school.example"}}],
         "organizer": {"emailAddress": {"address": "teacher@school.example"}},
         "isCancelled": False, "iCalUId": "uid-conference"},
        {"id": "oe-2", "subject": "Cancelled lunch", "isAllDay": False,
         "start": {"dateTime": "2026-10-07T16:00:00.0000000"}, "end": {"dateTime": "2026-10-07T17:00:00.0000000"},
         "isCancelled": True, "iCalUId": "uid-lunch"},
    ],
    "cal-holidays": [
        {"id": "oe-3", "subject": "Columbus Day", "isAllDay": True,
         "start": {"dateTime": "2026-10-12T00:00:00.0000000"}, "end": {"dateTime": "2026-10-13T00:00:00.0000000"},
         "isCancelled": False, "iCalUId": "uid-holiday"},
    ],
}


class FakeGraph:
    """Serves the fake data, two messages per page, and can rate-limit the first call."""

    def __init__(self, rate_limit_first=False):
        self.rate_limit_first = rate_limit_first
        self.calls = []

    def handler(self, request):
        path, params = request.url.path.removeprefix("/v1.0"), dict(request.url.params)
        self.calls.append((path, params))
        assert request.headers["Authorization"] == "Bearer test-token"
        if self.rate_limit_first:
            self.rate_limit_first = False
            return httpx.Response(429, headers={"Retry-After": "7"})
        folders = {"/me/mailFolders/deleteditems": "deleted", "/me/mailFolders/junkemail": "junk"}
        if path in folders:
            return httpx.Response(200, json={"id": folders[path]})
        if path == "/me/mailFolders/drafts":
            return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
        if path == "/me/messages":
            assert request.headers["Prefer"] == 'outlook.body-content-type="text"'
            if "skip" not in params:  # first page; later pages come from nextLink
                assert params["$orderby"] == "receivedDateTime desc"
                assert params["$filter"].startswith("receivedDateTime ge ")
            start = int(params.get("skip", 0))
            page = {"value": MESSAGES[start : start + 2]}
            if start + 2 < len(MESSAGES):
                page["@odata.nextLink"] = f"https://graph.microsoft.com/v1.0/me/messages?skip={start + 2}"
            return httpx.Response(200, json=page)
        if path == "/me/calendars":
            return httpx.Response(200, json={"value": [{"id": cid} for cid in EVENTS]})
        if path.startswith("/me/calendars/") and path.endswith("/calendarView"):
            assert "outlook.timezone=\"UTC\"" in request.headers["Prefer"]
            return httpx.Response(200, json={"value": EVENTS[path.split("/")[3]]})
        return httpx.Response(404, text="not found")


def client(fake=None, waits=(15,)):
    fake = fake or FakeGraph()
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    sleeps = []
    return GraphClient(lambda: "test-token", log=lambda _: None, sleep=sleeps.append, http=http, waits=waits), sleeps


# --- Graph client -------------------------------------------------------------


def test_paging_follows_next_links():
    graph, _ = client()
    params = {"$filter": "receivedDateTime ge 2025-01-01T00:00:00Z", "$orderby": "receivedDateTime desc"}
    items = list(graph.paged("/me/messages", params, {"Prefer": 'outlook.body-content-type="text"'}))
    assert [m["id"] for m in items] == [m["id"] for m in MESSAGES]


def test_rate_limit_honors_retry_after():
    fake = FakeGraph(rate_limit_first=True)
    graph, sleeps = client(fake)
    assert graph.get("/me/mailFolders/junkemail") == {"id": "junk"}
    assert sleeps == [7]


def test_errors_are_raised():
    graph, _ = client()
    with pytest.raises(GraphError) as error:
        graph.get("/me/nothing")
    assert error.value.status == 404


# --- mail ---------------------------------------------------------------------


def fetched():
    graph, _ = client()
    return {e.id: e for e in fetch_messages(graph, "outlook", NOW.replace(year=2025), 100)}


def test_junk_deleted_and_drafts_are_skipped():
    assert sorted(fetched()) == ["o-1", "o-2", "o-3", "o-7"]


def test_message_fields():
    email = fetched()["o-1"]
    assert email.account == "outlook"
    assert email.sender == "Jordan Lee <jordan@example.net>"
    assert email.to == ["Alex Morgan <alex@example.com>"]
    assert email.date == "2026-09-25T14:30:00+00:00"
    assert email.thread_id == "conv-o-1"


def test_reply_history_is_cut_but_forwards_are_kept():
    emails = fetched()
    assert emails["o-2"].body == "Tuesday works, see you at 8."
    assert "row F, November 14" in emails["o-3"].body


def test_html_body_becomes_text():
    assert "Amount due: $89.00" in " ".join(fetched()["o-7"].body.split())


def test_known_ids_are_skipped_and_max_respected():
    graph, _ = client()
    assert [e.id for e in fetch_messages(graph, "outlook", NOW, 100, skip_ids={"o-1"})][0] == "o-2"
    graph, _ = client()
    assert len(list(fetch_messages(graph, "outlook", NOW, 2))) == 2


# --- calendar -----------------------------------------------------------------


def events():
    graph, _ = client()
    return {e.id: e for e in fetch_events(graph, "outlook", NOW, 30, 90)}


def test_events_from_every_calendar():
    assert sorted(events()) == ["oe-1", "oe-2", "oe-3"]


def test_timed_event_converts_to_local_time():
    event = events()["oe-1"]
    assert event.start == "2026-10-06T22:00:00+00:00"
    assert describe_time(event, ZoneInfo("America/New_York")) == "Tue 2026-10-06, 6:00pm-6:30pm"
    assert event.location == "Room 12" and event.attendees == ["teacher@school.example"]
    assert event.ical_uid == "uid-conference"


def test_all_day_and_cancelled():
    e = events()
    assert e["oe-3"].all_day and (e["oe-3"].start, e["oe-3"].end) == ("2026-10-12", "2026-10-13")
    assert e["oe-2"].status == "cancelled"
    result = lookup(Route(["calendar"], datetime(2026, 10, 7).date(), datetime(2026, 10, 7).date()),
                    list(e.values()), NOW.date(), ZoneInfo("America/New_York"))
    assert result.events == []  # the cancelled lunch is not shown


# --- sync end to end ------------------------------------------------------------


def test_sync_stores_outlook_alongside_other_accounts():
    from qdrant_client import QdrantClient

    from services.embedding.index import EmailIndex

    store = Store(":memory:")
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    graph, _ = client()
    source = MicrosoftSource("outlook", graph)
    config = {
        "sync": {"email_days": 365, "max_emails": 100, "calendar_days_back": 30, "calendar_days_ahead": 90},
        "chunking": {"size_chars": 1500, "overlap_chars": 200},
    }
    assert sync_emails(source, store, FakeEmbedder(), index, config, log=lambda _: None) == 4
    sync_calendar(source, store, config, now=NOW, log=lambda _: None)
    assert store.accounts_present() == ["outlook"]
    assert store.count("events", "outlook") == 3


# --- approvals --------------------------------------------------------------------


def test_missing_scopes_accepts_short_or_full_names():
    requested = [GRAPH + "Mail.Read", GRAPH + "Calendars.Read"]
    assert missing_scopes(requested, ["Mail.Read", "calendars.read", "openid"]) == []
    assert missing_scopes(requested, [GRAPH + "Mail.Read"]) == [GRAPH + "Calendars.Read"]


def test_missing_approval_says_how_to_grant(tmp_path):
    from services.common.microsoft_creds import TokenProvider
    from services.common.tokens import MissingToken

    with pytest.raises(MissingToken, match="microsoft_auth.py grant outlook ingestion"):
        TokenProvider(tmp_path, "outlook", "ingestion")
