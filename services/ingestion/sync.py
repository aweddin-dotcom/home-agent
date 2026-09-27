"""Sync email and calendar from Google into the local store and search index.

  python -m services.ingestion.sync

Uses the read-only 'ingestion' token. Emails already synced are skipped, so
running it again only fetches new mail. Prints counts only, never content.
"""

from datetime import datetime, timezone

from services.common import settings
from services.embedding.chunker import email_chunks

from .calendar_source import fetch_events
from .gmail_source import fetch_messages


def sync_emails(gmail, store, embedder, index, config, log=print):
    known = store.known_email_ids()
    query = f"newer_than:{config['sync']['email_days']}d -in:drafts"
    chunking = config["chunking"]
    new = 0
    for email in fetch_messages(gmail, query, config["sync"]["max_emails"], known):
        chunks = email_chunks(email, chunking["size_chars"], chunking["overlap_chars"])
        index.upsert_email(email, chunks, embedder.embed_documents(chunks))
        # Save only after indexing succeeds, so a failed email is retried next run.
        store.save_email(email)
        new += 1
        if new % 25 == 0:
            log(f"  {new} new emails so far...")
    log(f"Emails: {new} new, {len(known)} already synced.")
    return new


def sync_calendar(calendar, store, config, now=None, log=print):
    sync = config["sync"]
    events = fetch_events(
        calendar, now or datetime.now(timezone.utc), sync["calendar_days_back"], sync["calendar_days_ahead"]
    )
    store.save_events(events)
    log(f"Calendar: {len(events)} events in the sync window.")
    return len(events)


def main():
    from googleapiclient.discovery import build
    from qdrant_client import QdrantClient

    from services.common.google_creds import load_credentials
    from services.common.ollama import OllamaEmbedder
    from services.embedding.index import EmailIndex

    from .store import Store

    config = settings.retrieval()
    model = settings.models()
    creds = load_credentials(settings.TOKENS_DIR, "ingestion")
    store = Store(settings.STRUCTURED_DB)
    embedder = OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"])
    index = EmailIndex(QdrantClient(url=settings.QDRANT_URL), config["search"]["collection"])

    sync_emails(build("gmail", "v1", credentials=creds), store, embedder, index, config)
    sync_calendar(build("calendar", "v3", credentials=creds), store, config)


if __name__ == "__main__":
    main()
