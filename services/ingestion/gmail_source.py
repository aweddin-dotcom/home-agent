"""Read email from the Gmail API (read-only) into Email records."""

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formataddr, getaddresses, parsedate_to_datetime

from .cleaners import clean_body, html_to_text


@dataclass
class Email:
    id: str
    thread_id: str
    sender: str
    to: list
    cc: list
    date: str  # ISO 8601
    subject: str
    labels: list
    body: str
    snippet: str


def _decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_part(payload, mime_type):
    """Depth-first search for the first body part of a type, skipping attachments."""
    if payload.get("filename"):
        return None
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = _find_part(part, mime_type)
        if found is not None:
            return found
    return None


def _addresses(header):
    return [formataddr(pair) for pair in getaddresses([header]) if pair[1]] if header else []


def _date(header, internal_date_ms):
    if header:
        try:
            return parsedate_to_datetime(header).isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.fromtimestamp(int(internal_date_ms or 0) / 1000, tz=timezone.utc).isoformat()


def parse_message(msg):
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        body = clean_body(plain)
    else:
        html = _find_part(payload, "text/html")
        body = clean_body(html_to_text(html)) if html is not None else ""
    return Email(
        id=msg["id"],
        thread_id=msg.get("threadId", msg["id"]),
        sender=headers.get("from", ""),
        to=_addresses(headers.get("to")),
        cc=_addresses(headers.get("cc")),
        date=_date(headers.get("date"), msg.get("internalDate")),
        subject=headers.get("subject", ""),
        labels=msg.get("labelIds", []),
        body=body,
        snippet=msg.get("snippet", ""),
    )


def fetch_messages(service, query, max_messages, skip_ids=frozenset()):
    """Yield parsed emails matching a Gmail search, newest first, skipping known ids."""
    messages = service.users().messages()
    request = messages.list(userId="me", q=query, maxResults=min(500, max_messages))
    seen = 0
    while request is not None:
        response = request.execute()
        for ref in response.get("messages", []):
            seen += 1
            if seen > max_messages:
                return
            if ref["id"] in skip_ids:
                continue
            yield parse_message(messages.get(userId="me", id=ref["id"], format="full").execute())
        request = messages.list_next(request, response)
