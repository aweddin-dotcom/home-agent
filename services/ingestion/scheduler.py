"""Run the sync on a schedule. This is the sync-worker container's command.

  python -m services.ingestion.scheduler

Every sync.interval_minutes it syncs all enabled accounts (re-reading
config/accounts.yaml each time, so changes apply without a restart), and
refreshes folder names every sync.folders_every_minutes. Each account's
result is recorded for the chat to show. On first start it copies in an
existing database (HOME_AGENT_SEED_DB) so nothing needs re-downloading.
"""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from services.common import settings


def log(message):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def seed_database(target, seed):
    """Copy the seed database into place if there's no database yet. Returns
    True if it copied. Uses SQLite's backup API, so a copy taken while
    something else has the seed open is still consistent."""
    if target.exists() or not seed.is_file() or seed.stat().st_size == 0:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"{seed.resolve().as_uri()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    with destination:
        source.backup(destination)
    source.close()
    destination.close()
    return True


def run_forever(run_once, interval_minutes, folders_every_minutes, sleep=time.sleep, clock=time.monotonic,
                cycles=None):
    """Call run_once(refresh_folders=...) every interval. `cycles` limits the
    number of runs (for tests); None runs forever."""
    last_folders = None
    count = 0
    while cycles is None or count < cycles:
        started = clock()
        refresh = last_folders is None or started - last_folders >= folders_every_minutes * 60
        try:
            failed = run_once(refresh_folders=refresh)
            if refresh:
                last_folders = started
            log(f"Sync finished{'; failed: ' + ', '.join(failed) if failed else ''}.")
        except Exception as error:  # noqa: BLE001 - keep the schedule alive; details are logged
            log(f"Sync run failed: {type(error).__name__}: {error}")
        count += 1
        if cycles is None or count < cycles:
            sleep(max(0.0, interval_minutes * 60 - (clock() - started)))


def main():
    from .sync import run_once

    seed = os.environ.get("HOME_AGENT_SEED_DB")
    if seed and seed_database(settings.STRUCTURED_DB, Path(seed)):
        log(f"Copied the existing database from {seed}; only new mail will be fetched.")
    schedule = settings.retrieval()["sync"]
    log(f"Syncing every {schedule['interval_minutes']} minutes; "
        f"folder names every {schedule['folders_every_minutes']} minutes.")
    run_forever(
        lambda refresh_folders: run_once(refresh_folders=refresh_folders, log=log),
        schedule["interval_minutes"],
        schedule["folders_every_minutes"],
    )


if __name__ == "__main__":
    main()
