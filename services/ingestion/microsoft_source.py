"""Read Outlook/Hotmail mail and calendar from Microsoft Graph (read-only)
into the same Email and Event records as Gmail."""

import re
from datetime import datetime, timedelta, timezone
from email.utils import formataddr

from .calendar_source import Event
from .cleaners import clean_body, html_to_text
from .gmail_source import Email
from .graph_api import GraphError

MESSAGE_FIELDS = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,"
    "body,bodyPreview,parentFolderId,isDraft,categories"
)
EVENT_FIELDS = "id,subject,start,end,isAllDay,location,body,attendees,organizer,isCancelled,iCalUId"
# Folders whose mail is never synced.
SKIPPED_FOLDERS = ("deleteditems", "junkemail", "drafts")

FORWARD_SUBJECT = re.compile(r"^\s*(fw|fwd)\s*:", re.IGNORECASE)
# Outlook's quoted-reply header: an optional line of underscores, then
# "From: ..." and "Sent: ..." lines.
OUTLOOK_QUOTE = re.compile(r"^(?:_{10,}\s*\n\s*)?From: .*\n\s*(?:Sent|Date): ", re.MULTILINE)


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _address(recipient):
    email_address = (recipient or {}).get("emailAddress") or {}
    address, name = email_address.get("address", ""), email_address.get("name", "")
    return formataddr((name, address)) if address else name


def parse_graph_message(message, account):
    body = message.get("body") or {}
    text = body.get("content") or ""
    if body.get("contentType") == "html":
        text = html_to_text(text)
    subject = message.get("subject") or ""
    if not FORWARD_SUBJECT.match(subject):
        # In a forward, the quoted message is the content; in a reply it's history.
        match = OUTLOOK_QUOTE.search(text)
        if match:
            text = text[: match.start()]
    received = message.get("receivedDateTime")
    return Email(
        account=account,
        id=message["id"],
        thread_id=message.get("conversationId") or message["id"],
        sender=_address(message.get("from")),
        to=[_address(r) for r in message.get("toRecipients") or []],
        cc=[_address(r) for r in message.get("ccRecipients") or []],
        date=_utc(received).isoformat() if received else "",
        subject=subject,
        labels=message.get("categories") or [],
        body=clean_body(text),
        snippet=message.get("bodyPreview") or "",
    )


def skipped_folder_ids(client):
    ids = set()
    for name in SKIPPED_FOLDERS:
        try:
            ids.add(client.get(f"/me/mailFolders/{name}", params={"$select": "id"})["id"])
        except GraphError as error:
            if error.status != 404:
                raise
    return ids


def fetch_messages(client, account, since, max_messages, skip_ids=frozenset()):
    """Yield parsed emails received since a time, newest first, skipping known
    ids and deleted, junk, and draft mail."""
    skip_folders = skipped_folder_ids(client)
    params = {
        "$filter": f"receivedDateTime ge {since.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
        "$orderby": "receivedDateTime desc",
        "$top": "100",
        "$select": MESSAGE_FIELDS,
    }
    headers = {"Prefer": 'outlook.body-content-type="text"'}
    seen = 0
    for message in client.paged("/me/messages", params, headers):
        seen += 1
        if seen > max_messages:
            return
        if message["id"] in skip_ids or message.get("isDraft") or message.get("parentFolderId") in skip_folders:
            continue
        yield parse_graph_message(message, account)


def _event_time(value):
    """Graph gives times without an offset, in the zone asked for (UTC here)."""
    return value.split(".")[0] + "+00:00"


def parse_graph_event(event, calendar_id, account):
    all_day = bool(event.get("isAllDay"))
    start, end = event["start"]["dateTime"], event["end"]["dateTime"]
    body = event.get("body") or {}
    description = body.get("content") or ""
    if body.get("contentType") == "html":
        description = html_to_text(description)
    return Event(
        account=account,
        id=event["id"],
        calendar_id=calendar_id,
        summary=event.get("subject") or "",
        start=start[:10] if all_day else _event_time(start),
        end=end[:10] if all_day else _event_time(end),
        all_day=all_day,
        location=(event.get("location") or {}).get("displayName") or "",
        description=clean_body(description),
        attendees=[
            a["emailAddress"]["address"]
            for a in event.get("attendees") or []
            if (a.get("emailAddress") or {}).get("address")
        ],
        organizer=((event.get("organizer") or {}).get("emailAddress") or {}).get("address") or "",
        status="cancelled" if event.get("isCancelled") else "confirmed",
        ical_uid=event.get("iCalUId") or "",
    )


def fetch_events(client, account, now, days_back, days_ahead):
    """All events, on every calendar the account has, within a window around now.
    calendarView expands recurring events into their individual occurrences."""
    params = {
        "startDateTime": f"{(now - timedelta(days=days_back)).astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
        "endDateTime": f"{(now + timedelta(days=days_ahead)).astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
        "$top": "100",
        "$select": EVENT_FIELDS,
    }
    headers = {"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'}
    events = []
    for calendar in client.paged("/me/calendars", {"$select": "id"}):
        for event in client.paged(f"/me/calendars/{calendar['id']}/calendarView", params, headers):
            events.append(parse_graph_event(event, calendar["id"], account))
    return events
