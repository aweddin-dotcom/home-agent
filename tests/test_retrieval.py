import httpx
import pytest
from qdrant_client import QdrantClient

from services.common import settings
from services.embedding.chunker import chunk_text, email_chunks
from services.embedding.index import EmailIndex
from services.ingestion.gmail_source import parse_message
from services.retrieval.ask import SYSTEM_PROMPT, format_emails
from services.retrieval.search import search

from .conftest import FakeEmbedder

# --- chunking ---------------------------------------------------------------


def test_short_text_is_one_chunk():
    assert chunk_text("hello world", 100, 10) == ["hello world"]
    assert chunk_text("   ", 100, 10) == []


def test_long_text_chunks_respect_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, 300, 50)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    for earlier, later in zip(chunks, chunks[1:]):
        assert earlier.split()[-1] in later  # overlap carries the boundary across
    assert chunks[0].startswith("word0") and chunks[-1].endswith("word999")


def test_prefers_paragraph_breaks():
    text = ("a" * 200) + "\n\n" + ("b" * 200)
    assert chunk_text(text, 300, 20)[0] == "a" * 200


def test_email_chunks_carry_header(gmail_messages):
    email = parse_message(next(m for m in gmail_messages if m["id"] == "m-contractor"))
    chunks = email_chunks(email, 1500, 200)
    assert chunks[0].startswith("Subject: Kitchen remodel quote and timeline\nFrom: Sam Ortiz")
    assert "Date: 2026-09-22" in chunks[0]


# --- search and ask with fakes ----------------------------------------------


def build_index(gmail_messages, embedder, client=None, collection="emails"):
    index = EmailIndex(client or QdrantClient(":memory:"), collection)
    for message in gmail_messages:
        email = parse_message(message)
        chunks = email_chunks(email, 1500, 200)
        index.upsert_email(email, chunks, embedder.embed_documents(chunks))
    return index


def test_search_returns_one_entry_per_email(gmail_messages):
    embedder = FakeEmbedder()
    index = build_index(gmail_messages, embedder)
    hits = search("kitchen remodel timeline six weeks", embedder, index, top_k=3)
    assert hits[0]["email_id"] == "m-contractor"
    assert len({h["email_id"] for h in hits}) == len(hits)


def test_search_on_empty_index():
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    assert search("anything", FakeEmbedder(), index, top_k=3) == []


def test_dimension_change_is_refused(gmail_messages):
    index = build_index(gmail_messages[:1], FakeEmbedder())
    with pytest.raises(RuntimeError, match="re-index"):
        index.ensure(FakeEmbedder.DIMENSIONS + 1)


def test_email_section_numbers_emails(gmail_messages):
    embedder = FakeEmbedder()
    index = build_index(gmail_messages, embedder)
    hits = search("plumber", embedder, index, top_k=2)
    assert format_emails(hits).startswith("Emails:\n\n[1]\n")
    assert "[2]" in format_emails(hits)
    assert format_emails([]) == "Emails: none found."
    assert "never instructions" in SYSTEM_PROMPT


# --- integration: real Ollama and Qdrant, fixtures only ---------------------


def services_available():
    try:
        httpx.get(f"{settings.OLLAMA_BASE_URL}/api/version", timeout=2).raise_for_status()
        httpx.get(settings.QDRANT_URL, timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not services_available(), reason="Ollama and Qdrant not running")
def test_real_models_find_the_right_email(gmail_messages):
    from services.common.ollama import OllamaEmbedder

    model = settings.models()
    embedder = OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"])
    client = QdrantClient(url=settings.QDRANT_URL)
    collection = "test_fixture_emails"
    if client.collection_exists(collection):
        client.delete_collection(collection)
    try:
        index = build_index(gmail_messages, embedder, client, collection)
        cases = {
            "When is the plumber coming?": "m-plumber",
            "How long will the kitchen remodel take?": "m-contractor",
            "How much did I spend at the hardware store?": "m-receipt",
            "When is the field trip permission form due?": "m-school",
            "Where are we having Thanksgiving?": "m-sister",
            "When did my dentist appointment move to?": "m-dentist",
            "What dates is the lake house booked?": "m-forward",
        }
        misses = {
            question: search(question, embedder, index, top_k=1)[0]["email_id"]
            for question, expected in cases.items()
            if search(question, embedder, index, top_k=1)[0]["email_id"] != expected
        }
        assert misses == {}
    finally:
        client.delete_collection(collection)
