"""Finding emails by the date they arrived. All data is invented."""

import json
from dataclasses import replace
from datetime import date
from zoneinfo import ZoneInfo

from services.ingestion.gmail_source import parse_message
from services.retrieval.ask import format_sources, gather, received_dates_apply
from services.retrieval.router import Route

from .conftest import FakeEmbedder
from .test_folders import RouteChat, synced_store

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)


def route(sources, start, end=None):
    return {"sources": sources, "start_date": start, "end_date": end or start,
            "calendar_keywords": [], "mail_folder": None}


def test_emails_between_uses_local_dates(gmail_messages, event_fixtures):
    store, _ = synced_store(gmail_messages, event_fixtures)
    total, rows = store.emails_between(date(2026, 9, 24), date(2026, 9, 24), TZ)
    # 20:41 Pacific on the 24th is 23:41 in New York: still the 24th.
    assert total == 2 and [r["subject"] for r in rows] == ["Thanksgiving?", "Your Northwind Goods order has shipped"]

    late = replace(parse_message(gmail_messages[0], "outlook"), id="late", subject="Late night",
                   date="2026-09-25T03:50:00+00:00")  # 11:50pm on the 24th in New York
    store.save_email(late)
    total, rows = store.emails_between(date(2026, 9, 24), date(2026, 9, 24), TZ)
    assert [r["subject"] for r in rows][:2] == ["Late night", "Thanksgiving?"]  # newest first


def test_date_question_lists_what_arrived(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat(route(["email"], "2026-09-24"))
    context = gather("the email from sept 24th", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    listing = context.sections[0]
    assert listing.startswith("Emails received on Thursday 2026-09-24 (newest first; 2 of 2):")
    assert "[D1] Received 2026-09-24" in listing and "Thanksgiving?" in listing
    assert "Mom says she can bring pies" in listing  # excerpt, not just the subject
    assert "Received in those dates:" in format_sources(context, TZ)


def test_a_range_ending_in_the_future_is_cut_at_today(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat(route(["email"], "2026-09-26", "2026-10-02"))
    context = gather("what came in this week", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    assert context.sections[0].startswith("Emails received from Saturday 2026-09-26 to Sunday 2026-09-27")


def test_dates_about_events_are_not_treated_as_arrival_dates():
    past = date(2026, 9, 24)
    assert received_dates_apply(Route(["email"], past, past), TODAY)
    assert not received_dates_apply(Route(["email", "calendar"], past, past), TODAY)  # the calendar's dates
    future = date(2026, 10, 5)
    assert not received_dates_apply(Route(["email"], future, future), TODAY)  # "emails about next week's trip"
    assert not received_dates_apply(Route(["email"]), TODAY)  # no dates at all


def test_stats_are_counts_only(gmail_messages, event_fixtures):
    store, _ = synced_store(gmail_messages, event_fixtures)
    stats = store.stats(TZ, day=date(2026, 9, 24))
    assert stats == {"gmail": {"emails": len(gmail_messages), "oldest": date(2026, 8, 30),
                               "newest": date(2026, 9, 27), "on_day": 2, "events": len(event_fixtures)}}
    assert "Thanksgiving" not in json.dumps(stats, default=str)


def test_date_question_excludes_similar_mail_from_other_days(gmail_messages, event_fixtures):
    """An older order from the same shop must not be offered for a question
    about the Sep 24 order, and the Sep 24 order's item list (in its second
    chunk, after the boilerplate) must be included."""
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat(route(["email"], "2026-09-24"))
    context = gather("northwind order items shipped", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    ids = [h["email_id"] for h in context.hits]
    assert "m-order-new" in ids and "m-order-old" not in ids
    assert all(h["date"].startswith("2026-09-24") for h in context.hits)
    order = next(h for h in context.hits if h["email_id"] == "m-order-new")
    assert "Brass desk lamp" in order["text"] and order["text"].startswith("Subject: Your Northwind Goods order")


def test_undated_question_still_searches_everything(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None,
                      "calendar_keywords": [], "mail_folder": None})
    context = gather("northwind order delivered kettle mugs", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    assert "m-order-old" in [h["email_id"] for h in context.hits]
