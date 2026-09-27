import json
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from services.agent.api import MODEL_ID, create_app
from services.agent.assistant import SOURCES_MARKER, Assistant, history_text, is_task_request, message_text
from services.embedding.chunker import email_chunks
from services.embedding.index import EmailIndex
from services.ingestion.calendar_source import parse_event
from services.ingestion.gmail_source import parse_message

from .conftest import FakeEmbedder

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)


class FakeChat:
    """Router gets a fixed route; condense gets a fixed rewrite; answers stream in two pieces."""

    def __init__(self, route=None, rewrite=None):
        self.route = route or {"sources": ["calendar"], "start_date": "2026-10-02", "end_date": "2026-10-02",
                               "calendar_keywords": []}
        self.rewrite = rewrite
        self.condense_calls = []
        self.answer_prompts = []
        self.passthrough = None

    def complete(self, system, user, schema=None, temperature=None):
        if schema is not None:
            return json.dumps(self.route)
        self.condense_calls.append(user)
        return self.rewrite

    def stream(self, system, user):
        self.answer_prompts.append(user)
        yield "On Friday "
        yield "you have a call [E1]."

    def stream_messages(self, messages):
        self.passthrough = messages
        yield "Weekend plans"


@pytest.fixture
def assistant(gmail_messages, event_fixtures):
    embedder = FakeEmbedder()
    index = EmailIndex(QdrantClient(":memory:"), "emails")
    for message in gmail_messages:
        email = parse_message(message, "gmail-test")
        chunks = email_chunks(email, 1500, 200)
        index.upsert_email(email, chunks, embedder.embed_documents(chunks))
    events = [parse_event(e, "primary", "gmail-test") for e in event_fixtures]

    def make(chat):
        return Assistant(chat, embedder, index, lambda: events, TZ, top_k=3, today=TODAY)

    return make


def user(text):
    return {"role": "user", "content": text}


# --- assistant --------------------------------------------------------------


def test_single_question_is_not_rewritten(assistant):
    chat = FakeChat()
    reply = "".join(assistant(chat).respond([user("What's on Friday the 2nd?")]))
    assert chat.condense_calls == []
    assert reply.startswith("On Friday you have a call [E1].")
    answer, sources = reply.split(SOURCES_MARKER)
    assert "Searched: calendar 2026-10-02 to 2026-10-02" in sources
    assert "Call with Sam about kitchen" in sources
    assert "Understood as" not in sources


def test_follow_up_is_rewritten_with_history(assistant):
    chat = FakeChat(rewrite="What's on my calendar on Friday, October 2?")
    messages = [
        user("What's on my calendar this week?"),
        {"role": "assistant", "content": "Nothing today." + SOURCES_MARKER + "Searched: calendar"},
        user("what about friday the 2nd"),
    ]
    reply = "".join(assistant(chat).respond(messages))
    history = chat.condense_calls[0]
    assert "User: What's on my calendar this week?" in history
    assert "Assistant: Nothing today." in history and "Searched" not in history  # footer stripped
    assert history.endswith("Latest message: what about friday the 2nd")
    assert chat.answer_prompts[0].endswith("Question: What's on my calendar on Friday, October 2?")
    assert "Understood as: What's on my calendar on Friday, October 2?" in reply


def test_failed_rewrite_keeps_the_original_question(assistant):
    class BrokenCondense(FakeChat):
        def complete(self, system, user, schema=None, temperature=None):
            if schema is None:
                raise RuntimeError("down")
            return super().complete(system, user, schema, temperature)

    chat = BrokenCondense()
    messages = [user("hi"), {"role": "assistant", "content": "hello"}, user("what's on friday?")]
    "".join(assistant(chat).respond(messages))
    assert chat.answer_prompts[0].endswith("Question: what's on friday?")


def test_open_webui_task_requests_skip_email_and_calendar(assistant):
    chat = FakeChat()
    messages = [user("### Task:\nGenerate a concise title for this chat.")]
    assert "".join(assistant(chat).respond(messages)) == "Weekend plans"
    assert chat.passthrough == [{"role": "user", "content": messages[0]["content"]}]
    assert chat.answer_prompts == []


def test_message_text_handles_content_parts():
    assert message_text({"content": [{"type": "text", "text": "a"}, {"type": "image_url"}]}) == "a"
    assert message_text({"content": None}) == ""
    assert is_task_request("  ### Task: title") and not is_task_request("what's my task list?")


def test_history_keeps_recent_turns_only():
    messages = [user(f"q{i}") for i in range(10)]
    assert history_text(messages).splitlines() == [f"User: q{i}" for i in range(4, 10)]


# --- API --------------------------------------------------------------------


def test_models_lists_home_agent(assistant):
    client = TestClient(create_app(assistant(FakeChat())))
    assert client.get("/v1/models").json()["data"][0]["id"] == MODEL_ID


def test_non_streaming_completion(assistant):
    client = TestClient(create_app(assistant(FakeChat())))
    body = client.post("/v1/chat/completions", json={"model": MODEL_ID, "messages": [user("friday?")]}).json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"].startswith("On Friday you have a call")


def test_streaming_completion_is_server_sent_events(assistant):
    client = TestClient(create_app(assistant(FakeChat())))
    response = client.post(
        "/v1/chat/completions", json={"model": MODEL_ID, "stream": True, "messages": [user("friday?")]}
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text.startswith("On Friday you have a call [E1].")


def test_failure_mid_answer_is_reported_not_crashed(assistant):
    class FailingChat(FakeChat):
        def stream(self, system, user):
            yield "Partial "
            raise RuntimeError("ollama stopped")

    client = TestClient(create_app(assistant(FailingChat())))
    content = client.post("/v1/chat/completions", json={"messages": [user("friday?")]}).json()
    text = content["choices"][0]["message"]["content"]
    assert text.startswith("Partial ") and "Something went wrong: RuntimeError: ollama stopped" in text
