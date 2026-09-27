"""Read email from the Gmail API (read-only) into Email records."""

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formataddr, getaddresses, parsedate_to_datetime

from .cleaners import clean_body, html_to_text
from .google_api import execute


@dataclass
class Email:
    account: str  # label from config/accounts.yaml
    id: str  # the provider's id, unique within the account
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


def parse_message(msg, account):
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        body = clean_body(plain)
    else:
        html = _find_part(payload, "text/html")
        body = clean_body(html_to_text(html)) if html is not None else ""
    return Email(
        account=account,
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


def fetch_messages(service, account, query, max_messages, skip_ids=frozenset(), log=print):
    """Yield parsed emails matching a Gmail search, newest first, skipping known ids."""
    messages = service.users().messages()
    request = messages.list(userId="me", q=query, maxResults=min(500, max_messages))
    seen = 0
    while request is not None:
        response = execute(request, log)
        for ref in response.get("messages", []):
            seen += 1
            if seen > max_messages:
                return
            if ref["id"] in skip_ids:
                continue
            message = execute(messages.get(userId="me", id=ref["id"], format="full"), log)
            yield parse_message(message, account)
        request = messages.list_next(request, response)


# Gmail system labels worth knowing about, by display name. Other system
# labels (UNREAD, CHAT, ...) aren't folders to the user.
SYSTEM_LABELS = {
    "INBOX": "Inbox",
    "SENT": "Sent",
    "STARRED": "Starred",
    "IMPORTANT": "Important",
    "CATEGORY_PROMOTIONS": "Promotions",
    "CATEGORY_SOCIAL": "Social",
    "CATEGORY_UPDATES": "Updates",
    "CATEGORY_FORUMS": "Forums",
}


def _list_ids(service, query, max_messages, log, label_id=None):
    messages = service.users().messages()
    kwargs = {"userId": "me", "q": query, "maxResults": min(500, max_messages)}
    if label_id:
        kwargs["labelIds"] = [label_id]
    request = messages.list(**kwargs)
    ids = []
    while request is not None and len(ids) < max_messages:
        response = execute(request, log)
        ids.extend(ref["id"] for ref in response.get("messages", []))
        request = messages.list_next(request, response)
    return ids[:max_messages]


def fetch_label_map(service, query, max_messages, log=print):
    """{message id: [label names]} for every email in the sync window, from
    one cheap id-only listing per label rather than fetching each email."""
    labels = execute(service.users().labels().list(userId="me"), log).get("labels", [])
    names = {
        label["id"]: SYSTEM_LABELS.get(label["id"], label["name"])
        for label in labels
        if label.get("type") == "user" or label["id"] in SYSTEM_LABELS
    }
    folders = {message_id: [] for message_id in _list_ids(service, query, max_messages, log)}
    for label_id, name in names.items():
        for message_id in _list_ids(service, query, max_messages, log, label_id):
            if message_id in folders:
                folders[message_id].append(name)
    return folders
