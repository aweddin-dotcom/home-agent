import base64
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))


def _b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def to_gmail_message(fixture):
    """Build a Gmail API 'full' format message from a fixture entry."""
    headers = [
        {"name": name, "value": fixture[key]}
        for name, key in (("From", "from"), ("To", "to"), ("Date", "date"), ("Subject", "subject"))
        if key in fixture
    ]
    parts = [
        {"mimeType": mime, "filename": "", "body": {"data": _b64(fixture[key])}}
        for mime, key in (("text/plain", "text"), ("text/html", "html"))
        if key in fixture
    ]
    if len(parts) == 1:
        payload = {**parts[0], "headers": headers}
    else:
        payload = {"mimeType": "multipart/alternative", "filename": "", "headers": headers, "parts": parts}
    body_text = fixture.get("text", "")
    return {
        "id": fixture["id"],
        "threadId": "t-" + fixture["id"],
        "labelIds": ["INBOX"],
        "snippet": body_text[:80],
        "internalDate": "1790000000000",
        "payload": payload,
    }


@pytest.fixture
def email_fixtures():
    return yaml.safe_load((FIXTURES / "emails.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def gmail_messages(email_fixtures):
    return [to_gmail_message(f) for f in email_fixtures]


@pytest.fixture
def event_fixtures():
    return yaml.safe_load((FIXTURES / "events.yaml").read_text(encoding="utf-8"))


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeGmail:
    """Just enough of the Gmail API client for fetch_messages()."""

    def __init__(self, messages, page_size=3):
        self.by_id = {m["id"]: m for m in messages}
        self.ids = [m["id"] for m in messages]
        self.page_size = page_size
        self.fetched = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        return self._page(0)

    def _page(self, start):
        page = self.ids[start : start + self.page_size]
        call = _Call({"messages": [{"id": i} for i in page]})
        call.next_start = start + self.page_size if start + self.page_size < len(self.ids) else None
        return call

    def list_next(self, request, response):
        return None if request.next_start is None else self._page(request.next_start)

    def get(self, userId, id, format):
        self.fetched.append(id)
        return _Call(self.by_id[id])


class FakeCalendar:
    """Just enough of the Calendar API client for fetch_events()."""

    def __init__(self, events):
        self.events_list = events

    def calendarList(self):
        return self

    def events(self):
        return self

    def list(self, **kwargs):
        if "calendarId" in kwargs:
            return _Call({"items": self.events_list})
        return _Call({"items": [{"id": "primary"}]})

    def list_next(self, request, response):
        return None


class FakeEmbedder:
    """Deterministic bag-of-words vectors, so search works without Ollama."""

    DIMENSIONS = 256

    def _vector(self, text):
        vector = [0.0] * self.DIMENSIONS
        for word in text.lower().split():
            word = "".join(ch for ch in word if ch.isalnum())
            if word:
                vector[hash(word) % self.DIMENSIONS] += 1.0
        return vector if any(vector) else [1.0] + [0.0] * (self.DIMENSIONS - 1)

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, query):
        return self._vector(query)
