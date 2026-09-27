import json
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from qdrant_client import QdrantClient

from services.embedding.chunker import email_chunks
from services.embedding.index import EmailIndex
from services.ingestion.calendar_source import parse_event
from services.ingestion.gmail_source import parse_message
from services.retrieval.ask import ask
from services.retrieval.router import Route, date_table, named_ranges, route, validate
from services.retrieval.structured_query import describe_time, format_calendar, in_range, lookup

from .conftest import FakeEmbedder

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)  # a Sunday


@pytest.fixture
def events(event_fixtures):
    return [parse_event(e, "primary", "gmail-test") for e in event_fixtures]


def ids(events):
    return [e.id for e in events]


# --- router -----------------------------------------------------------------


def test_date_table_marks_today_and_coming_weekdays():
    table = date_table(TODAY)
    assert "Sun 2026-09-27 (today)" in table
    assert "Mon 2026-09-28 (tomorrow, coming Monday)" in table
    assert "Sat 2026-10-03 (coming Saturday)" in table
    assert "Sat 2026-10-10\n" in table  # the one after is not "coming"
    assert "next week: 2026-09-28 to 2026-10-04" in table


def d(text):
    return date.fromisoformat(text)


def test_named_ranges_on_a_sunday():
    r = named_ranges(TODAY)
    assert r["this week"] == (d("2026-09-27"), d("2026-10-04"))  # the week ahead, not just today
    assert r["next week"] == (d("2026-09-28"), d("2026-10-04"))
    assert r["last week"] == (d("2026-09-14"), d("2026-09-20"))
    assert r["this weekend"] == (d("2026-09-27"), d("2026-09-27"))


def test_named_ranges_on_a_wednesday():
    r = named_ranges(d("2026-09-30"))
    assert r["this week"] == (d("2026-09-30"), d("2026-10-04"))
    assert r["next week"] == (d("2026-10-05"), d("2026-10-11"))
    assert r["this weekend"] == (d("2026-10-03"), d("2026-10-04"))


def test_named_ranges_on_a_saturday():
    r = named_ranges(d("2026-10-03"))
    assert r["this week"] == (d("2026-10-03"), d("2026-10-11"))
    assert r["this weekend"] == (d("2026-10-03"), d("2026-10-04"))


def test_validate_good_reply():
    r = validate('{"sources": ["calendar"], "start_date": "2026-10-01", "end_date": "2026-10-01", '
                 '"calendar_keywords": ["dentist"]}')
    assert r.sources == ["calendar"]
    assert r.start == r.end == date(2026, 10, 1)
    assert r.keywords == ["dentist"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("not json", Route()),
        ('{"sources": []}', Route()),
        ('{"sources": ["weather"]}', Route()),
        ('{"sources": ["email"], "start_date": "tomorrow"}', Route(sources=["email"])),
    ],
)
def test_validate_repairs_bad_replies(raw, expected):
    assert validate(raw) == expected


def test_validate_fills_missing_end_and_swaps_reversed_dates():
    assert validate('{"sources": ["calendar"], "start_date": "2026-10-01"}').end == date(2026, 10, 1)
    r = validate('{"sources": ["calendar"], "start_date": "2026-10-04", "end_date": "2026-09-28"}')
    assert (r.start, r.end) == (date(2026, 9, 28), date(2026, 10, 4))


def test_route_falls_back_to_both_sources_when_the_model_fails():
    class BrokenChat:
        def complete(self, *args, **kwargs):
            raise RuntimeError("ollama down")

    assert route("anything", BrokenChat(), TODAY) == Route()


# --- calendar lookup --------------------------------------------------------


def test_single_day_with_overlap(events):
    result = lookup(Route(["calendar"], date(2026, 10, 1), date(2026, 10, 1)), events, TODAY, TZ)
    assert ids(result.events) == ["ev-budget", "ev-dentist"]
    assert result.overlaps == [(0, 1)]


def test_no_dates_shows_next_two_weeks_without_cancelled(events):
    result = lookup(Route(["calendar"]), events, TODAY, TZ)
    assert ids(result.events) == ["ev-standup", "ev-budget", "ev-dentist", "ev-call", "ev-soccer", "ev-market"]
    assert "next two weeks" in result.note


def test_keyword_shows_everything_during_the_matched_event(events):
    result = lookup(Route(["calendar"], keywords=["dentist"]), events, TODAY, TZ)
    assert ids(result.events) == ["ev-budget", "ev-dentist"]
    assert result.overlaps == [(0, 1)]


def test_all_day_event_overlaps_timed_event_that_day(events):
    result = lookup(Route(["calendar"], keywords=["soccer"]), events, TODAY, TZ)
    assert ids(result.events) == ["ev-soccer", "ev-market"]
    assert result.overlaps == [(0, 1)]


def test_named_event_wins_over_dates_that_miss_it(events):
    route = Route(["calendar"], d("2026-09-22"), d("2026-09-22"), ["dentist"])
    assert "ev-dentist" in ids(lookup(route, events, TODAY, TZ).events)


def test_keywords_that_name_no_event_do_not_discard_dates(events):
    route = Route(["calendar"], d("2026-10-01"), d("2026-10-01"), ["Thursday"])
    assert ids(lookup(route, events, TODAY, TZ).events) == ["ev-budget", "ev-dentist"]


def test_dates_kept_when_they_contain_the_named_event(events):
    route = Route(["calendar"], d("2026-10-01"), d("2026-10-02"), ["dentist"])
    assert ids(lookup(route, events, TODAY, TZ).events) == ["ev-budget", "ev-dentist", "ev-call"]


def test_same_event_in_two_accounts_is_shown_once(events):
    from dataclasses import replace

    copies = [replace(e, account="icloud") for e in events if e.id == "ev-dentist"]
    for event in events + copies:
        event.ical_uid = event.ical_uid or f"uid-{event.id}"
    result = lookup(Route(["calendar"], d("2026-10-01"), d("2026-10-01")), events + copies, TODAY, TZ)
    assert ids(result.events) == ["ev-budget", "ev-dentist"]


def test_recurring_instances_sharing_a_uid_are_all_kept(events):
    from dataclasses import replace

    standup = next(e for e in events if e.id == "ev-standup")
    weekly = [replace(standup, id=f"ev-standup-{i}", ical_uid="uid-standup",
                      start=f"2026-09-{29 + i}T09:00:00-04:00", end=f"2026-09-{29 + i}T09:15:00-04:00")
              for i in range(2)]
    result = lookup(Route(["calendar"], d("2026-09-29"), d("2026-09-30")), weekly, TODAY, TZ)
    assert len(result.events) == 2


def test_multi_word_keyword_needs_every_word(events):
    assert ids(lookup(Route(["calendar"], keywords=["lake trip"]), events, TODAY, TZ).events) == ["ev-trip"]
    result = lookup(Route(["calendar"], keywords=["lake party"]), events, TODAY, TZ)
    assert result.events == [] and "No calendar events match" in result.note


def test_all_day_end_date_is_exclusive(events):
    soccer = [e for e in events if e.id == "ev-soccer"]
    assert in_range(soccer, date(2026, 10, 3), date(2026, 10, 3), TZ) == soccer
    assert in_range(soccer, date(2026, 10, 4), date(2026, 10, 4), TZ) == []


def test_describe_time(events):
    by_id = {e.id: e for e in events}
    assert describe_time(by_id["ev-dentist"], TZ) == "Thu 2026-10-01, 3:00pm-4:00pm"
    assert describe_time(by_id["ev-call"], TZ) == "Fri 2026-10-02, 9:00am-9:30am"  # converted from UTC
    assert describe_time(by_id["ev-soccer"], TZ) == "Sat 2026-10-03, all day"
    assert describe_time(by_id["ev-trip"], TZ) == "Sat 2027-06-12 to Tue 2027-06-15, all day"


def test_format_calendar(events):
    text = format_calendar(lookup(Route(["calendar"], keywords=["dentist"]), events, TODAY, TZ), TZ)
    assert text.startswith("Calendar for Thursday 2026-10-01. ")
    assert "anything not listed is not on the calendar" in text
    assert "[E2] Thu 2026-10-01, 3:00pm-4:00pm  Dentist cleaning  at Bright Smile Dental" in text
    assert "- [E1] overlaps [E2]" in text
    empty = format_calendar(lookup(Route(["calendar"], date(2026, 9, 28), date(2026, 9, 28)), events, TODAY, TZ), TZ)
    assert "No events.\nThe next event after these dates: Tue 2026-09-29, 9:00am-9:15am  Team standup" in empty


def test_next_event_only_when_range_is_empty(events):
    assert lookup(Route(["calendar"], d("2026-10-01"), d("2026-10-01")), events, TODAY, TZ).next_event is None
    far = lookup(Route(["calendar"], d("2027-07-01"), d("2027-07-01")), events, TODAY, TZ)
    assert far.events == [] and far.next_event is None


# --- ask --------------------------------------------------------------------


class ScriptedChat:
    """Answers the router with a fixed route and records the final prompt."""

    def __init__(self, route_reply):
        self.route_reply = route_reply

    def complete(self, system, user, schema=None, temperature=None):
        if schema is not None:
            return json.dumps(self.route_reply)
        self.system, self.user = system, user
        return "answer"


def email_index(gmail_messages):
    embedder = FakeEmbedder()
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    for message in gmail_messages:
        email = parse_message(message, "gmail-test")
        chunks = email_chunks(email, 1500, 200)
        index.upsert_email(email, chunks, embedder.embed_documents(chunks))
    return embedder, index


def test_calendar_question_skips_email_search(gmail_messages, events):
    embedder, index = email_index(gmail_messages)
    chat = ScriptedChat({"sources": ["calendar"], "start_date": "2026-10-01", "end_date": "2026-10-01",
                         "calendar_keywords": []})
    answer = ask("what's on thursday?", embedder, index, chat, 3, events, TODAY, TZ)
    assert answer.hits == []
    assert "Dentist cleaning" in chat.user and "Emails" not in chat.user
    assert "Today is Sunday, 2026-09-27" in chat.system


def test_empty_calendar_result_falls_back_to_email(gmail_messages, events):
    embedder, index = email_index(gmail_messages)
    chat = ScriptedChat({"sources": ["calendar"], "start_date": None, "end_date": None,
                         "calendar_keywords": ["plumber"]})
    answer = ask("technician kitchen faucet cartridge", embedder, index, chat, 3, events, TODAY, TZ)
    assert answer.route.sources == ["calendar", "email"]
    assert answer.hits[0]["email_id"] == "m-plumber"
    assert "No calendar events match" in chat.user and "Emails:" in chat.user


def test_mixed_question_gets_both_sections(gmail_messages, events):
    embedder, index = email_index(gmail_messages)
    chat = ScriptedChat({"sources": ["email", "calendar"], "start_date": None, "end_date": None,
                         "calendar_keywords": ["dentist"]})
    answer = ask("dentist appointment moved", embedder, index, chat, 3, events, TODAY, TZ)
    assert answer.hits and answer.calendar.events
    assert chat.user.index("Calendar for") < chat.user.index("Emails:")
    assert chat.user.endswith("Question: dentist appointment moved")


# --- integration: the real chat model routes sample questions -----------------


def ollama_available():
    import httpx

    from services.common import settings

    try:
        httpx.get(f"{settings.OLLAMA_BASE_URL}/api/version", timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


# (question, source it needs, expected dates, or the event lookup must find)
ROUTING_CASES = [
    ("What's on my calendar tomorrow?", "calendar", ("2026-09-28", "2026-09-28")),
    ("What do I have on Thursday?", "calendar", ("2026-10-01", "2026-10-01")),
    ("What's on my calendar next week?", "calendar", ("2026-09-28", "2026-10-04")),
    ("What's on my calendar this week?", "calendar", ("2026-09-27", "2026-10-04")),
    ("Am I free Saturday morning?", "calendar", ("2026-10-03", "2026-10-03")),
    ("What meetings did I have yesterday?", "calendar", ("2026-09-26", "2026-09-26")),
    ("When is my dentist appointment?", "calendar", "ev-dentist"),
    ("Does the dentist appointment conflict with anything?", "calendar", "ev-dentist"),
    ("How much did I spend at the hardware store?", "email", None),
    ("What did the contractor say about the timeline?", "email", None),
    ("What's in my travel stuff folder?", "email", "folder:travel"),
    ("Anything new under my Receipts label?", "email", "folder:receipts"),
]


@pytest.mark.skipif(not ollama_available(), reason="Ollama not running")
def test_real_model_routes_questions(events):
    from services.common import settings
    from services.common.ollama import OllamaChat

    model = settings.models()
    chat = OllamaChat(settings.OLLAMA_BASE_URL, model["chat"], num_ctx=model["chat_context_tokens"])
    wrong = {}
    for question, source, expected in ROUTING_CASES:
        r = route(question, chat, TODAY)
        if source not in r.sources:
            wrong[question] = f"sources {r.sources}"
        elif isinstance(expected, tuple) and (str(r.start), str(r.end)) != expected:
            wrong[question] = f"dates {r.start} to {r.end}"
        elif isinstance(expected, str) and expected.startswith("folder:"):
            if expected[7:] not in (r.folder or "").lower():
                wrong[question] = f"folder {r.folder!r}"
        elif isinstance(expected, str) and expected not in ids(lookup(r, events, TODAY, TZ).events):
            wrong[question] = f"lookup missed {expected} (route {r})"
    assert wrong == {}
