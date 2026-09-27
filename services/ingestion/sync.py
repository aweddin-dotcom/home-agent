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


def sync_accounts(accounts, connect, store, embedder, index, config, log=print):
    """Sync each account; one failing doesn't stop the rest. Returns the labels that failed."""
    failed = []
    for label, account_config in accounts.items():
        log(f"{label}:")
        try:
            source = connect(label, account_config)
            if source.has_email:
                sync_emails(source, store, embedder, index, config, log)
                sync_folders(source, store, config, log)
            if source.has_calendar:
                sync_calendar(source, store, config, log=log)
        except Exception as error:  # noqa: BLE001 - reported, and other accounts continue
            log(f"  Failed: {type(error).__name__}: {error}")
            failed.append(label)
    return failed


def remove_account(label, store, index, log=print):
    store.remove_account(label)
    index.remove_account(label)
    log(f"Removed the local copy of '{label}'. Nothing at the provider was changed.")


def main(argv=None):
    from qdrant_client import QdrantClient

    from services.common.ollama import OllamaEmbedder
    from services.embedding.index import EmailIndex

    from .sources import connect
    from .store import Store

    parser = argparse.ArgumentParser(description="Sync email and calendar into the local store.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--account", help="sync only this account")
    group.add_argument("--remove", metavar="ACCOUNT", help="delete this account's local copy and stop")
    group.add_argument("--rebuild", action="store_true", help="clear the local copy of everything, then sync")
    args = parser.parse_args(argv)

    config = settings.retrieval()
    model = settings.models()
    store = Store(settings.STRUCTURED_DB)
    index = EmailIndex(QdrantClient(url=settings.QDRANT_URL), config["search"]["collection"])

    if args.remove:
        remove_account(args.remove, store, index)
        return
    if args.rebuild or store.rebuilt:
        if store.rebuilt:
            print("The local copy was from an older version; rebuilding it from the providers.")
        index.reset()
        for label in store.accounts_present():
            store.remove_account(label)

    accounts = settings.accounts()
    if args.account:
        all_accounts = settings.accounts(include_disabled=True)
        if args.account not in all_accounts:
            raise SystemExit(f"No account '{args.account}' in config/accounts.yaml.")
        accounts = {args.account: all_accounts[args.account]}
    if not accounts:
        raise SystemExit("No enabled accounts in config/accounts.yaml.")

    stale = set(store.accounts_present()) - set(settings.accounts(include_disabled=True))
    if stale:
        print(f"Note: local copies exist for accounts no longer configured: {', '.join(sorted(stale))}. "
              "Remove with --remove LABEL.")

    embedder = OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"])
    failed = sync_accounts(
        accounts, lambda label, cfg: connect(label, cfg, settings.TOKENS_DIR), store, embedder, index, config
    )
    if failed:
        raise SystemExit(f"Sync failed for: {', '.join(failed)}")


if __name__ == "__main__":
    main()
