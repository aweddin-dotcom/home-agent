"""Folder and label names: synced, refreshed when mail moves, and usable in answers.
All data is invented."""

import json
import sqlite3
from datetime import date
from zoneinfo import ZoneInfo

from qdrant_client import QdrantClient

from services.embedding.chunker import email_chunks
from services.embedding.index import EmailIndex
from services.ingestion.gmail_source import fetch_label_map, parse_message
from services.ingestion.microsoft_source import fetch_folder_map, folder_paths
from services.ingestion.sources import GoogleSource
from services.ingestion.store import SCHEMA_VERSION, Store
from services.ingestion.sync import sync_accounts
from services.retrieval.ask import format_folder, gather
from services.retrieval.router import validate

from .conftest import FakeCalendar, FakeEmbedder, FakeGmail
from .test_microsoft import NOW, client

CONFIG = {
    "sync": {"email_days": 365, "max_emails": 100, "calendar_days_back": 30, "calendar_days_ahead": 400},
    "chunking": {"size_chars": 1500, "overlap_chars": 200},
}
TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)
quiet = lambda _: None  # noqa: E731


# --- providers ------------------------------------------------------------------


def test_gmail_label_map_uses_names_and_skips_non_folder_labels(gmail_messages):
    folders = fetch_label_map(FakeGmail(gmail_messages), "q", 100, log=quiet)
    assert folders["m-forward"] == ["Travel stuff"]
    assert sorted(folders["m-receipt"]) == ["Inbox", "Receipts"]
    assert sorted(folders["m-newsletter"]) == ["Inbox", "Promotions"]  # UNREAD isn't a folder
    assert set(folders) == {m["id"] for m in gmail_messages}  # every email in the window, labeled or not


def test_outlook_folder_paths_include_nested_folders():
    graph, _ = client()
    assert folder_paths(graph) == {
        "inbox": "Inbox", "travel": "Inbox/Travel stuff", "junk": "Junk Email", "deleted": "Deleted Items",
    }


def test_outlook_folder_map():
    graph, _ = client()
    folders = fetch_folder_map(graph, NOW.replace(year=2025), 100)
    assert folders["o-3"] == ["Inbox/Travel stuff"]
    assert folders["o-1"] == ["Inbox"]


# --- store ------------------------------------------------------------------------


def synced_store(gmail_messages, event_fixtures, gmail=None):
    """A store and index after syncing the fixture mailbox as account 'gmail'."""
    store = Store(":memory:")
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    gmail = gmail or FakeGmail(gmail_messages)
    connect = lambda label, cfg: GoogleSource(label, gmail, FakeCalendar(event_fixtures), log=quiet)  # noqa: E731
    assert sync_accounts({"gmail": {"provider": "google"}}, connect, store, FakeEmbedder(), index, CONFIG, quiet) == []
    return store, index


def test_sync_records_folders(gmail_messages, event_fixtures):
    store, _ = synced_store(gmail_messages, event_fixtures)
    assert store.folders_for([("gmail", "m-forward"), ("gmail", "m-plumber")]) == {
        ("gmail", "m-forward"): ["Travel stuff"],
        ("gmail", "m-plumber"): ["Inbox"],
    }
    assert store.folder_names() == ["Inbox", "Promotions", "Receipts", "Travel stuff"]


def test_moving_an_email_is_picked_up_on_the_next_sync(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    plumber = next(m for m in gmail_messages if m["id"] == "m-plumber")
    plumber["labelIds"] = ["Label_travel"]  # the user moved it
    connect = lambda label, cfg: GoogleSource(label, FakeGmail(gmail_messages), FakeCalendar([]), log=quiet)  # noqa: E731
    sync_accounts({"gmail": {"provider": "google"}}, connect, store, FakeEmbedder(), index, CONFIG, quiet)
    assert store.folders_for([("gmail", "m-plumber")])[("gmail", "m-plumber")] == ["Travel stuff"]


def test_mail_marked_as_spam_later_is_marked_and_left_out(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    plumber = next(m for m in gmail_messages if m["id"] == "m-plumber")
    plumber["labelIds"] = ["SPAM"]  # Gmail (or the user) filed it as spam after it was synced
    connect = lambda label, cfg: GoogleSource(label, FakeGmail(gmail_messages), FakeCalendar([]), log=quiet)  # noqa: E731
    sync_accounts({"gmail": {"provider": "google"}}, connect, store, FakeEmbedder(), index, CONFIG, quiet)
    assert store.folders_for([("gmail", "m-plumber")])[("gmail", "m-plumber")] == ["Spam"]

    # Gone from listings by date and from search answers...
    _, rows = store.emails_between(date(2026, 9, 21), date(2026, 9, 21), TZ)
    assert "m-plumber" not in [r["email_id"] for r in rows]
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None,
                      "calendar_keywords": [], "mail_folder": None})
    context = gather("leaking kitchen faucet plumber", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    assert "m-plumber" not in [h["email_id"] for h in context.hits]
    # ...but there when the spam folder is asked about by name.
    assert [r["email_id"] for r in store.emails_in_folder("spam")[2]] == ["m-plumber"]


def test_emails_in_folder_matches_part_of_the_name(gmail_messages, event_fixtures):
    store, _ = synced_store(gmail_messages, event_fixtures)
    matched, total, rows = store.emails_in_folder("travel")
    assert matched == ["Travel stuff"] and total == 2
    assert [r["email_id"] for r in rows] == ["m-forward", "m-hotel"]  # newest first
    assert store.emails_in_folder("vacation") == ([], 0, [])


def test_version_2_store_is_upgraded_in_place(tmp_path):
    path = tmp_path / "structured.db"
    old = sqlite3.connect(path)
    old.executescript(
        "create table emails (account text, id text, thread_id text, sender text, recipients text, cc text,"
        " date text, subject text, labels text, body text, snippet text, synced_at text,"
        " primary key (account, id));"
        "create table events (account text, calendar_id text, id text, summary text, start text, end text,"
        " all_day integer, location text, description text, attendees text, organizer text, status text,"
        " ical_uid text, synced_at text, primary key (account, calendar_id, id));"
        "insert into emails (account, id, subject) values ('gmail', 'kept', 'Still here');"
        "pragma user_version = 2;"
    )
    old.commit()
    old.close()

    store = Store(path)
    assert not store.rebuilt  # upgraded, not cleared: no re-sync needed
    assert store.db.execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.known_email_ids("gmail") == {"kept"}
    assert store.folders_for([("gmail", "kept")]) == {("gmail", "kept"): []}


def test_readonly_store_without_folders_column_degrades_gracefully(tmp_path):
    path = tmp_path / "structured.db"
    path.touch()
    store = Store(path, readonly=True)
    assert store.folder_names() == []
    assert store.emails_in_folder("travel") == ([], 0, [])


# --- answers ----------------------------------------------------------------------


def test_router_accepts_a_folder_and_adds_email():
    route = validate(json.dumps({"sources": ["calendar"], "start_date": None, "end_date": None,
                                 "calendar_keywords": [], "mail_folder": " travel stuff "}))
    assert route.folder == "travel stuff"
    assert "email" in route.sources
    assert validate('{"sources": ["email"], "mail_folder": ""}').folder is None


class RouteChat:
    def __init__(self, route):
        self.route = route

    def complete(self, system, user, schema=None, temperature=None):
        return json.dumps(self.route)


def test_folder_question_lists_the_folder(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None,
                      "calendar_keywords": [], "mail_folder": "travel"})
    context = gather("what's in my travel folder?", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    folder_section = context.sections[0]
    assert folder_section.startswith("Emails in Travel stuff (newest first; 2 most recent of 2):")
    assert "[F1] Received 2026-09-26" in folder_section and "Lake house dates" in folder_section
    # The excerpt reaches details beyond the one-line preview.
    assert "[F2] Received 2026-09-10" in folder_section and "Check-in: Tuesday, October 20, 2026" in folder_section
    assert [r["email_id"] for r in context.folder_rows] == ["m-forward", "m-hotel"]


def test_searched_emails_show_their_folder(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None,
                      "calendar_keywords": [], "mail_folder": None})
    context = gather("lake house booked kayak", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    assert "m-forward" in [h["email_id"] for h in context.hits]
    assert "\nFolder: Travel stuff\nSubject: Fwd: Lake house dates" in context.sections[-1]
    dates = [h["date"] for h in context.hits]
    assert dates == sorted(dates, reverse=True)  # newest first


def test_naming_a_folder_limits_search_to_it(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None,
                      "calendar_keywords": [], "mail_folder": "travel"})
    context = gather("reservation confirmation dates", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    assert context.hits and {h["email_id"] for h in context.hits} <= {"m-forward", "m-hotel"}


def test_unknown_folder_lists_the_real_ones():
    text = format_folder("vacation", [], 0, [], ["Inbox", "Receipts", "Travel stuff"])
    assert text == 'No mail folder or label matches "vacation". Folders and labels: Inbox, Receipts, Travel stuff.'
