"""Automatic sync: the schedule, first-start seeding, status recording, and
the sync notes shown in chat. All data is invented."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.status import ago, sync_notes
from services.ingestion.scheduler import run_forever, seed_database
from services.ingestion.store import SCHEMA_VERSION, Store
from services.ingestion.sync import sync_accounts

from .test_agent_api import assistant  # noqa: F401 (fixture)
from .test_folders import synced_store

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


# --- schedule -----------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_runs_every_interval_and_refreshes_folders_hourly():
    clock = FakeClock()
    clock.sleeps = []
    calls = []

    def run_once(refresh_folders):
        calls.append(refresh_folders)
        clock.now += 30  # each sync takes 30 seconds
        return []

    run_forever(run_once, interval_minutes=15, folders_every_minutes=60, sleep=clock.sleep, clock=clock, cycles=6)
    assert calls == [True, False, False, False, True, False]  # folders at 0 and 60 minutes
    assert clock.sleeps == [15 * 60 - 30] * 5  # the sync's own time counts toward the interval


def test_a_failing_run_does_not_stop_the_schedule():
    clock = FakeClock()
    clock.sleeps = []
    calls = []

    def run_once(refresh_folders):
        calls.append(refresh_folders)
        if len(calls) == 1:
            raise RuntimeError("qdrant down")
        return []

    run_forever(run_once, 15, 60, sleep=clock.sleep, clock=clock, cycles=3)
    assert len(calls) == 3
    assert calls[1] is True  # the folder refresh is retried after a failed run


# --- first start ----------------------------------------------------------------


def test_seed_copies_existing_database_once(tmp_path):
    seed, target = tmp_path / "seed.db", tmp_path / "volume" / "structured.db"
    Store(seed).record_sync("gmail", True, "2 new emails")
    assert seed_database(target, seed)
    assert Store(target).sync_statuses()["gmail"]["detail"] == "2 new emails"
    assert not seed_database(target, seed)  # already there: left alone


def test_seed_skipped_when_there_is_nothing_to_copy(tmp_path):
    empty = tmp_path / "empty.db"
    empty.touch()
    assert not seed_database(tmp_path / "a.db", empty)
    assert not seed_database(tmp_path / "b.db", tmp_path / "missing.db")


def test_version_3_store_gains_sync_status_in_place(tmp_path):
    path = tmp_path / "structured.db"
    store = Store(path)
    store.db.execute("drop table sync_status")
    store.db.execute("pragma user_version = 3")
    store.db.commit()
    store.db.close()
    upgraded = Store(path)
    assert not upgraded.rebuilt
    assert upgraded.db.execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION == 4
    assert upgraded.sync_statuses() == {}


def test_readonly_store_before_the_database_exists(tmp_path):
    store = Store(tmp_path / "not-yet.db", readonly=True)
    assert store.all_events() == [] and store.sync_statuses() == {}


# --- status recording -------------------------------------------------------------


def test_sync_records_success_and_failure(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    status = store.sync_statuses()["gmail"]
    assert status["ok"] and status["detail"] == f"{len(gmail_messages)} new emails, {len(event_fixtures)} events"
    first_success = status["last_success"]

    def broken(label, config):
        raise RuntimeError("approval expired; run grant")

    sync_accounts({"gmail": {}}, broken, store, None, index, {}, log=lambda _: None)
    status = store.sync_statuses()["gmail"]
    assert not status["ok"] and "approval expired" in status["detail"]
    assert status["last_success"] == first_success  # a failure keeps the last good time


# --- chat notes --------------------------------------------------------------------


def status(ok=True, minutes_ago=5, detail=""):
    when = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"last_attempt": when, "last_success": when if ok else None, "ok": ok, "detail": detail}


def test_notes_when_all_is_well():
    statuses = {"gmail": status(minutes_ago=4), "icloud": status(minutes_ago=6)}
    assert sync_notes(statuses, ["gmail", "icloud"], NOW) == ["Last sync: 4 min ago."]


def test_notes_flag_failures_staleness_and_missing_accounts():
    statuses = {
        "gmail": {**status(minutes_ago=3), "ok": False, "detail": "ExpiredToken: run grant gmail ingestion"},
        "outlook": status(minutes_ago=180),
        "icloud": status(minutes_ago=2),
        "old": {**status(), "ok": False, "detail": "disabled account, ignored"},
    }
    notes = sync_notes(statuses, ["gmail", "outlook", "icloud", "new"], NOW, stale_after_minutes=60)
    assert notes == [
        "Last sync: 2 min ago.",
        "Sync problem (gmail): ExpiredToken: run grant gmail ingestion",
        "outlook last synced 3 h ago.",
        "Not synced yet: new.",
    ]


@pytest.mark.parametrize("minutes, text", [(0, "just now"), (45, "45 min ago"), (130, "2 h ago"), (3000, "2 days ago")])
def test_ago(minutes, text):
    assert ago(NOW - timedelta(minutes=minutes), NOW) == text


def test_chat_footer_includes_sync_notes(assistant):
    from .test_agent_api import FakeChat, user

    bot = assistant(FakeChat())
    bot.status_notes = lambda: ["Last sync: 4 min ago.", "Sync problem (gmail): expired"]
    reply = "".join(bot.respond([user("friday?")]))
    assert reply.endswith("Last sync: 4 min ago.\nSync problem (gmail): expired")

    bot.status_notes = lambda: 1 / 0  # a broken status check must not hide the answer
    assert "On Friday" in "".join(bot.respond([user("friday?")]))


# --- running on the host ------------------------------------------------------------


def test_commands_hand_off_to_containers_on_the_host(monkeypatch):
    from services.common import containers

    calls = []
    monkeypatch.setattr(containers, "in_container", lambda: False)
    monkeypatch.setattr(containers.subprocess, "run", lambda cmd, cwd: calls.append(cmd) or type("R", (), {"returncode": 0}))
    with pytest.raises(SystemExit) as done:
        containers.delegate_to_container("sync-worker", "services.ingestion.sync", ["--account", "gmail"])
    assert done.value.code == 0
    assert calls == [["docker", "compose", "exec", "sync-worker", "python", "-m", "services.ingestion.sync",
                      "--account", "gmail"]]

    monkeypatch.setattr(containers, "in_container", lambda: True)
    assert containers.delegate_to_container("sync-worker", "x", []) is None  # inside: carry on


def test_database_sqlite_file_is_a_real_copy(tmp_path):
    seed, target = tmp_path / "seed.db", tmp_path / "copy.db"
    Store(seed)
    seed_database(target, seed)
    assert sqlite3.connect(target).execute("pragma integrity_check").fetchone()[0] == "ok"
