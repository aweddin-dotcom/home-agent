"""Decide which sources a question needs (email, calendar, or both) and which
dates it's about.

The chat model does the interpretation, given a table of nearby dates so it
doesn't have to do calendar arithmetic. Its reply is then validated, so a
bad reply degrades to "search both" instead of failing.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

SOURCES = ("email", "calendar")
MAX_RANGE_DAYS = 400

SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)}},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "calendar_keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sources", "start_date", "end_date", "calendar_keywords"],
}

SYSTEM_PROMPT = """You route questions about the user's email and calendar. Reply with JSON only.

sources: which to search.
- "calendar" for schedules, meetings, appointments, events, availability,
  conflicts, "what's on", "am I free".
- "email" for anything someone wrote or sent: requests, confirmations,
  receipts, quotes, plans discussed in messages.
- Both when the question could involve either, or you're unsure. Visits
  and appointments arranged by message ("when is the plumber coming?")
  may only be in email, so include email for those.

start_date, end_date: the dates the question is about, as YYYY-MM-DD,
inclusive. Copy them from the named ranges and date table; don't calculate.
A weekday name on its own ("Thursday", "Saturday morning") means the one
marked "coming" in the table. A single day has start_date equal to
end_date. Only fill in dates when the question mentions or implies a time.
"When is X?" has no dates: use null for both and put X in
calendar_keywords.

calendar_keywords: names of specific events the question mentions (for
example "dentist", "lake trip"). Empty when none."""


@dataclass
class Route:
    sources: list = field(default_factory=lambda: list(SOURCES))
    start: date = None
    end: date = None
    keywords: list = field(default_factory=list)


def named_ranges(today):
    """Common phrases resolved to dates in code, so the model doesn't do
    calendar arithmetic. Weeks run Monday to Sunday."""
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    if today.weekday() == 6:  # Sunday: only today is left of this weekend
        weekend = (today, today)
    else:
        saturday = today + timedelta(days=5 - today.weekday())
        weekend = (max(saturday, today), saturday + timedelta(days=1))
    return {
        "today": (today, today),
        "tomorrow": (today + timedelta(days=1),) * 2,
        "yesterday": (today - timedelta(days=1),) * 2,
        "this week (rest of it)": (today, week_end),
        "next week": (week_start + timedelta(days=7), week_end + timedelta(days=7)),
        "last week": (week_start - timedelta(days=7), week_end - timedelta(days=7)),
        "this weekend": weekend,
        "next 7 days": (today, today + timedelta(days=7)),
    }


def date_table(today, days_back=7, days_ahead=21):
    ranges = [f"{name}: {start} to {end}" for name, (start, end) in named_ranges(today).items()]
    lines = ["Named ranges:", *ranges, "", "Dates:"]
    for offset in range(-days_back, days_ahead + 1):
        day = today + timedelta(days=offset)
        labels = {0: ["today"], 1: ["tomorrow"], -1: ["yesterday"]}.get(offset, [])
        if 1 <= offset <= 7:
            labels.append(f"coming {day:%A}")
        suffix = f" ({', '.join(labels)})" if labels else ""
        lines.append(f"{day:%a} {day.isoformat()}{suffix}")
    return "\n".join(lines)


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def validate(raw):
    """Turn the model's reply into a Route, repairing anything unusable."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return Route()
    if not isinstance(data, dict):
        return Route()

    sources = [s for s in data.get("sources") or [] if s in SOURCES]
    start, end = _parse_date(data.get("start_date")), _parse_date(data.get("end_date"))
    if start and not end:
        end = start
    if end and not start:
        start = end
    if start and end and start > end:
        start, end = end, start
    if start and (end - start).days > MAX_RANGE_DAYS:
        start = end = None
    keywords = [k.strip() for k in data.get("calendar_keywords") or [] if isinstance(k, str) and k.strip()]
    return Route(sources=sources or list(SOURCES), start=start, end=end, keywords=keywords)


def route(question, chat, today):
    user = f"Date table:\n{date_table(today)}\n\nQuestion: {question}"
    try:
        raw = chat.complete(SYSTEM_PROMPT, user, schema=SCHEMA, temperature=0)
    except Exception:
        return Route()
    return validate(raw)
