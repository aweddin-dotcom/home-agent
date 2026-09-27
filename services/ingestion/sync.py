"""Sync email and calendar from every enabled account into the local store
and search index.

  python -m services.ingestion.sync                   # all enabled accounts
  python -m services.ingestion.sync --account LABEL   # just one
  python -m services.ingestion.sync --remove LABEL    # delete one account's local copy
  python -m services.ingestion.sync --rebuild         # clear everything, then sync

Accounts are listed in config/accounts.yaml. Emails already synced are
skipped, so running it again only fetches new mail. A failing account is
reported and the others still sync. Prints counts only, never content.
Nothing here changes anything at the providers.
"""

import argparse
from datetime import datetime, timezone

from services.common import settings
from services.embedding.chunker import email_chunks


def sync_emails(source, store, embedder, index, config, log=print):
    known = store.known_email_ids(source.account)
    sync, chunking = config["sync"], config["chunking"]
    new = 0
    for email in source.emails(sync["email_days"], sync["max_emails"], known):
        chunks = email_chunks(email, chunking["size_chars"], chunking["overlap_chars"])
        index.upsert_email(email, chunks, embedder.embed_documents(chunks))
        # Save only after indexing succeeds, so a failed email is retried next run.
        store.save_email(email)
        new += 1
        if new % 25 == 0:
            log(f"  {new} new emails so far...")
    log(f"  Emails: {new} new, {len(known)} already synced.")
    return new


def sync_calendar(source, store, config, now=None, log=print):
    sync = config["sync"]
    events = source.events(now or datetime.now(timezone.utc), sync["calendar_days_back"], sync["calendar_days_ahead"])
    store.replace_events(source.account, events)
    log(f"  Calendar: {len(events)} events in the sync window.")
    return len(events)


def sync_folders(source, store, config, log=print):
    """Refresh folder/label names for every synced email, so moves are picked up."""
    folders = source.folders(config["sync"]["email_days"], config["sync"]["max_emails"])
    store.update_folders(source.account, folders)
    log(f"  Folders: refreshed for {len(folders)} emails.")
    return len(folders)


def sync_accounts(accounts, connect, store, embedder, index, config, log=print, refresh_folders=True):
    """Sync each account; one failing doesn't stop the rest. Each account's
    result is recorded in the store for the chat to report. Returns the
    labels that failed."""
    failed = []
    for label, account_config in accounts.items():
        log(f"{label}:")
        counts = []
        try:
            source = connect(label, account_config)
            if source.has_email:
                counts.append(f"{sync_emails(source, store, embedder, index, config, log)} new emails")
                if refresh_folders:
                    sync_folders(source, store, config, log)
            if source.has_calendar:
                counts.append(f"{sync_calendar(source, store, config, log=log)} events")
            store.record_sync(label, True, ", ".join(counts))
        except Exception as error:  # noqa: BLE001 - reported, and other accounts continue
            message = f"{type(error).__name__}: {error}"
            log(f"  Failed: {message}")
            store.record_sync(label, False, message[:500])
            failed.append(label)
    return failed


def remove_account(label, store, index, log=print):
    store.remove_account(label)
    index.remove_account(label)
    log(f"Removed the local copy of '{label}'. Nothing at the provider was changed.")


def open_store_and_index(config):
    from qdrant_client import QdrantClient

    from services.embedding.index import EmailIndex

    from .store import Store

    store = Store(settings.STRUCTURED_DB)
    index = EmailIndex(QdrantClient(url=settings.QDRANT_URL), config["search"]["collection"])
    if store.rebuilt:
        print("The local copy was from an older version; rebuilding it from the providers.")
        index.reset()
    return store, index


def run_once(only_account=None, refresh_folders=True, log=print):
    """One sync of every enabled account (or just one). Returns the labels that failed."""
    from services.common.ollama import OllamaEmbedder

    from .sources import connect

    config = settings.retrieval()
    model = settings.models()
    store, index = open_store_and_index(config)

    accounts = settings.accounts()
    if only_account:
        all_accounts = settings.accounts(include_disabled=True)
        if only_account not in all_accounts:
            raise SystemExit(f"No account '{only_account}' in config/accounts.yaml.")
        accounts = {only_account: all_accounts[only_account]}
    if not accounts:
        log("No enabled accounts in config/accounts.yaml.")
        return []

    stale = set(store.accounts_present()) - set(settings.accounts(include_disabled=True))
    if stale:
        log(f"Note: local copies exist for accounts no longer configured: {', '.join(sorted(stale))}. "
            "Remove with --remove LABEL.")

    embedder = OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"])
    return sync_accounts(
        accounts, lambda label, cfg: connect(label, cfg, settings.TOKENS_DIR), store, embedder, index, config,
        log, refresh_folders,
    )


def print_stats(day=None):
    """Counts only, for checking what's stored without looking at content."""
    from datetime import date

    from .store import Store

    store = Store(settings.STRUCTURED_DB, readonly=True)
    day = date.fromisoformat(day) if day else None
    statuses = store.sync_statuses()
    for account, s in sorted(store.stats(settings.TIMEZONE, day).items()):
        line = f"{account}: {s['emails']} emails"
        if s["emails"]:
            line += f" received {s['oldest']} to {s['newest']}"
        line += f", {s.get('events', 0)} events"
        if day:
            line += f"; {s['on_day']} emails received on {day}"
        status = statuses.get(account)
        if status:
            line += f". Last sync {'ok' if status['ok'] else 'FAILED'} at {status['last_attempt'][:16]}"
        print(line)


def main(argv=None):
    from services.common.containers import delegate_to_container

    # On the laptop/Mac itself, run inside the sync-worker container, which
    # owns the database (a Docker volume).
    delegate_to_container("sync-worker", "services.ingestion.sync", argv)

    parser = argparse.ArgumentParser(description="Sync email and calendar into the local store.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--account", help="sync only this account")
    group.add_argument("--remove", metavar="ACCOUNT", help="delete this account's local copy and stop")
    group.add_argument("--rebuild", action="store_true", help="clear the local copy of everything, then sync")
    group.add_argument("--stats", action="store_true", help="show counts per account (no content) and stop")
    parser.add_argument("--date", help="with --stats: also count emails received on this day (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    if args.stats:
        print_stats(args.date)
        return
    if args.remove or args.rebuild:
        store, index = open_store_and_index(settings.retrieval())
        if args.remove:
            remove_account(args.remove, store, index)
            return
        index.reset()
        for label in store.accounts_present():
            store.remove_account(label)

    failed = run_once(args.account)
    if failed:
        raise SystemExit(f"Sync failed for: {', '.join(failed)}")


if __name__ == "__main__":
    main()
