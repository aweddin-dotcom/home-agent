"""Ask questions against the invented test emails and calendar, with the
real models, as if today were Sunday 2026-09-27 in New York. Useful for
checking answer quality after a change, or comparing models, without
touching real data.

  python scripts/try_fixtures.py                    # the default questions
  python scripts/try_fixtures.py "your question"    # your own
"""

import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import yaml  # noqa: E402
from conftest import to_gmail_message  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402

from services.common import settings  # noqa: E402
from services.common.ollama import OllamaChat, OllamaEmbedder  # noqa: E402
from services.embedding.chunker import email_chunks  # noqa: E402
from services.embedding.index import EmailIndex  # noqa: E402
from services.ingestion.calendar_source import parse_event  # noqa: E402
from services.ingestion.gmail_source import parse_message  # noqa: E402
from services.retrieval.ask import ask  # noqa: E402

TODAY = date(2026, 9, 27)
TZ = ZoneInfo("America/New_York")
COLLECTION = "fixture_demo_emails"
QUESTIONS = [
    "What do I have on Thursday?",
    "Does my dentist appointment conflict with anything?",
    "Am I free Saturday morning?",
    "What's on my calendar next week?",
    "When is the plumber coming?",
    "How much will the kitchen remodel cost and when will it be done?",
    "What is my sister's phone number?",
    "Are there any account notices I should act on?",
]


def load(name):
    return yaml.safe_load((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


def main():
    model = settings.models()
    embedder = OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"])
    chat = OllamaChat(settings.OLLAMA_BASE_URL, model["chat"], num_ctx=model["chat_context_tokens"])
    client = QdrantClient(url=settings.QDRANT_URL)
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    index = EmailIndex(client, COLLECTION)
    try:
        for fixture in load("emails.yaml"):
            email = parse_message(to_gmail_message(fixture))
            chunks = email_chunks(email, 1500, 200)
            index.upsert_email(email, chunks, embedder.embed_documents(chunks))
        events = [parse_event(e, "primary") for e in load("events.yaml")]

        print(f"Model: {model['chat']}. Today is {TODAY:%A} {TODAY}.\n")
        for question in sys.argv[1:] or QUESTIONS:
            answer = ask(question, embedder, index, chat, 3, events, TODAY, TZ)
            r = answer.route
            dates = f" {r.start} to {r.end}" if r.start else ""
            print(f"Q: {question}\n   (searched {', '.join(r.sources)}{dates})\nA: {answer.text}\n")
    finally:
        client.delete_collection(COLLECTION)


if __name__ == "__main__":
    main()
