"""Read iCloud calendars over CalDAV (read-only in code; Apple's app-specific
passwords can't be limited to reading) into the same Event records as the
other providers.

Recurring events are expanded here, from each event's iCalendar data,
rather than relying on the server: exceptions (a moved or cancelled
occurrence) and exclusions come out right.
"""

from datetime import datetime, time, timedelta

from .calendar_source import Event
from .cleaners import clean_body, html_to_text

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"


def _address(value):
    return str(value).removeprefix("mailto:").removeprefix("MAILTO:")


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _when(value, tz):
    """(iso string, is_all_day) for a DTSTART/DTEND value. Floating times
    (no zone) are taken to be in the user's time zone."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.isoformat(), False
    return value.isoformat(), True


def events_from_ics(ics, calendar_id, account, start, end, tz):
    """Every occurrence between start and end (datetimes) in one iCalendar object."""
    import icalendar
    import recurring_ical_events

    calendar = icalendar.Calendar.from_ical(ics)
    events = []
    for component in recurring_ical_events.of(calendar).between(start, end):
        begins = component.decoded("DTSTART")
        if "DTEND" in component:
            finishes = component.decoded("DTEND")
        elif "DURATION" in component:
            finishes = begins + component.decoded("DURATION")
        else:
            finishes = begins + (timedelta(days=1) if not isinstance(begins, datetime) else timedelta())
        start_text, all_day = _when(begins, tz)
        end_text, _ = _when(finishes, tz)
        uid = str(component.get("UID", ""))
        description = str(component.get("DESCRIPTION", ""))
        if "<" in description:
            description = html_to_text(description)
        organizer = component.get("ORGANIZER")
        events.append(
            Event(
                account=account,
                id=f"{uid}/{start_text}",  # one id per occurrence
                calendar_id=calendar_id,
                summary=str(component.get("SUMMARY", "")),
                start=start_text,
                end=end_text,
                all_day=all_day,
                location=str(component.get("LOCATION", "")),
                description=clean_body(description),
                attendees=[_address(a) for a in _as_list(component.get("ATTENDEE"))],
                organizer=_address(organizer) if organizer else "",
                status="cancelled" if str(component.get("STATUS", "")).upper() == "CANCELLED" else "confirmed",
                ical_uid=uid,
            )
        )
    return events


def fetch_events(calendars, account, now, days_back, days_ahead, tz, skip=()):
    """Events from every calendar (except those named in `skip`) in a window
    around now. `calendars` are caldav Calendar objects, or anything with
    name, url, and search(start=, end=, event=True) returning objects with .data."""
    start = datetime.combine((now - timedelta(days=days_back)).date(), time.min, tz)
    end = datetime.combine((now + timedelta(days=days_ahead)).date(), time.min, tz)
    skipped = {name.lower() for name in skip}
    events = []
    for calendar in calendars:
        name = calendar.name or ""
        if name.lower() in skipped:
            continue
        for obj in calendar.search(start=start, end=end, event=True):
            events.extend(events_from_ics(obj.data, str(calendar.url or name), account, start, end, tz))
    return events


def connect_calendars(username, password, url=ICLOUD_CALDAV_URL):
    """Sign in and list the account's calendars. Raises a clear error if
    Apple rejects the credentials."""
    import caldav
    from caldav.lib.error import AuthorizationError

    client = caldav.DAVClient(url=url, username=username, password=password)
    try:
        return client.principal().calendars()
    except AuthorizationError as error:
        raise PermissionError(
            "iCloud rejected the sign-in. Check the Apple ID and app-specific password "
            "(python scripts/secrets_cli.py set icloud_apple_id / icloud_app_password)."
        ) from error
