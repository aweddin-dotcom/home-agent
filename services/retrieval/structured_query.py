"""Calendar lookups from the local store: events by date range or by name,
formatted for the chat model, with overlaps worked out in code rather than
left to the model."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

DEFAULT_DAYS_AHEAD = 14
MAX_KEYWORD_SPAN_EVENTS = 5


@dataclass
class CalendarResult:
    events: list  # Event records, in start order
    start: date
    end: date
    note: str = ""
    overlaps: list = field(default_factory=list)  # (index_a, index_b) into events


def interval(event, tz):
    """An event's [start, end) in local time. All-day end dates are exclusive."""
    if event.all_day:
        start = datetime.combine(date.fromisoformat(event.start), time.min, tz)
        end = datetime.combine(date.fromisoformat(event.end or event.start), time.min, tz)
        return start, max(end, start + timedelta(days=1))
    start = datetime.fromisoformat(event.start).astimezone(tz)
    end = datetime.fromisoformat(event.end).astimezone(tz) if event.end else start
    return start, max(end, start)


def in_range(events, start, end, tz):
    window_start = datetime.combine(start, time.min, tz)
    window_end = datetime.combine(end + timedelta(days=1), time.min, tz)
    selected = []
    for event in events:
        event_start, event_end = interval(event, tz)
        if event_start < window_end and (event_end > window_start or event_start >= window_start):
            selected.append(event)
    return selected


def matches(event, keyword):
    text = f"{event.summary} {event.location} {event.description}".lower()
    return all(word in text for word in keyword.lower().split())


def find_overlaps(events, tz):
    spans = [interval(e, tz) for e in events]
    return [
        (i, j)
        for i in range(len(events))
        for j in range(i + 1, len(events))
        if spans[i][0] < spans[j][1] and spans[j][0] < spans[i][1]
    ]


def lookup(route, events, today, tz):
    events = sorted((e for e in events if e.status != "cancelled"), key=lambda e: interval(e, tz)[0])
    note = ""
    # Keywords that name no event at all (e.g. "Thursday") are ignored.
    route_keywords = [k for k in route.keywords if any(matches(e, k) for e in events)] if route.start else route.keywords
    dated = in_range(events, route.start, route.end, tz) if route.start else None
    if dated is not None and route_keywords and not any(matches(e, k) for e in dated for k in route_keywords):
        # The model named a real event but gave dates that don't contain it.
        # Trust the name over the dates.
        dated = None
    if dated is not None:
        start, end = route.start, route.end
        selected = dated
    elif route.keywords:
        matched = [e for e in events if any(matches(e, k) for k in route.keywords)]
        names = ", ".join(f'"{k}"' for k in route.keywords)
        if not matched:
            start = end = today
            selected = []
            note = f"No calendar events match {names}."
        elif len(matched) <= MAX_KEYWORD_SPAN_EVENTS:
            # Show everything during the matched events, so conflicts are visible.
            spans = [interval(e, tz) for e in matched]
            start = min(s for s, _ in spans).date()
            end = max(e - timedelta(microseconds=1) for _, e in spans).date()
            selected = in_range(events, start, end, tz)
            note = f"Events matching {names}, and everything else during them."
        else:
            start = interval(matched[0], tz)[0].date()
            end = interval(matched[-1], tz)[0].date()
            selected = matched
            note = f"Events matching {names}."
    else:
        start, end = today, today + timedelta(days=DEFAULT_DAYS_AHEAD)
        selected = in_range(events, start, end, tz)
        note = "No dates were given, so this shows the next two weeks."
    return CalendarResult(selected, start, end, note, find_overlaps(selected, tz))


def _clock(moment):
    return f"{moment:%I:%M%p}".lstrip("0").lower()


def _day(moment):
    return f"{moment:%a} {moment:%Y-%m-%d}"


def describe_time(event, tz):
    start, end = interval(event, tz)
    if event.all_day:
        last = end - timedelta(days=1)
        span = _day(start)
        if last.date() != start.date():
            span += f" to {_day(last)}"
        return span + ", all day"
    if start.date() == end.date():
        return f"{_day(start)}, {_clock(start)}-{_clock(end)}"
    return f"{_day(start)} {_clock(start)} to {_day(end)} {_clock(end)}"


def format_calendar(result, tz):
    if result.start == result.end:
        dates = f"{result.start:%A} {result.start}"
    else:
        dates = f"{result.start:%A} {result.start} to {result.end:%A} {result.end}"
    header = (
        f"Calendar for {dates}. This list is complete for those dates: "
        "anything not listed is not on the calendar."
    )
    lines = [header]
    if result.note:
        lines.append(result.note)
    if not result.events:
        lines.append("No events.")
    for n, event in enumerate(result.events, 1):
        line = f"[E{n}] {describe_time(event, tz)}  {event.summary or '(no title)'}"
        if event.location:
            line += f"  at {event.location}"
        if event.attendees:
            line += f"  with {', '.join(event.attendees)}"
        if event.description:
            line += f"\n     Notes: {event.description[:300]}"
        lines.append(line)
    if result.overlaps:
        lines.append("Overlapping events:")
        lines.extend(f"- [E{i + 1}] overlaps [E{j + 1}]" for i, j in result.overlaps)
    return "\n".join(lines)
