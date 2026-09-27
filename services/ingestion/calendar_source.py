"""Read events from the Google Calendar API (read-only) into Event records."""

from dataclasses import dataclass
from datetime import timedelta

from .cleaners import clean_body, html_to_text


@dataclass
class Event:
    account: str  # label from config/accounts.yaml
    id: str
    calendar_id: str
    summary: str
    start: str  # ISO date-time, or a date for all-day events
    end: str
    all_day: bool
    location: str
    description: str
    attendees: list
    organizer: str
    status: str
    ical_uid: str = ""  # same across providers and accounts; used to merge copies


def parse_event(event, calendar_id, account):
    start, end = event.get("start", {}), event.get("end", {})
    description = event.get("description", "")
    if "<" in description:
        description = html_to_text(description)
    return Event(
        account=account,
        id=event["id"],
        calendar_id=calendar_id,
        summary=event.get("summary", ""),
        start=start.get("dateTime") or start.get("date", ""),
        end=end.get("dateTime") or end.get("date", ""),
        all_day="date" in start,
        location=event.get("location", ""),
        description=clean_body(description),
        attendees=[a["email"] for a in event.get("attendees", []) if "email" in a],
        organizer=event.get("organizer", {}).get("email", ""),
        status=event.get("status", ""),
        ical_uid=event.get("iCalUID", ""),
    )


def fetch_events(service, account, now, days_back, days_ahead):
    """All events, on every calendar the account can see, within a window around now."""
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()
    events = []
    for calendar in service.calendarList().list().execute().get("items", []):
        api = service.events()
        request = api.list(
            calendarId=calendar["id"],
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        while request is not None:
            response = request.execute()
            events.extend(parse_event(e, calendar["id"], account) for e in response.get("items", []))
            request = api.list_next(request, response)
    return events
