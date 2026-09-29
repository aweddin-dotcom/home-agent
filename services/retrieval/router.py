"""Decide which sources a question needs (email, calendar, or both) and which
dates it's about.

The chat model does the interpretation, given a table of nearby dates so it
doesn't have to do calendar arithmetic. Its reply is then validated, so a
bad reply degrades to "search both" instead of failing.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

SOURCES = ("email", "calendar", "portfolio", "profile", "disc_golf", "pong", "general")
# What to search when the model's reply is unusable: the user's own data.
DEFAULT_SOURCES = ("email", "calendar")
MAX_RANGE_DAYS = 400

SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)}},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "calendar_keywords": {"type": "array", "items": {"type": "string"}},
        "mail_folder": {"type": ["string", "null"]},
        "about_purchases": {"type": "boolean"},
        "portfolio_news": {"type": "boolean"},
    },
    "required": ["sources", "start_date", "end_date", "calendar_keywords", "mail_folder", "about_purchases",
                 "portfolio_news"],
}

SYSTEM_PROMPT = """You route questions about the user's email and calendar. Reply with JSON only.

sources: which to search.
- "calendar" for schedules, meetings, appointments, events, availability,
  conflicts, "what's on", "am I free".
- "email" for anything someone wrote or sent: requests, confirmations,
  receipts, quotes, plans discussed in messages.
- Both when the question could involve either, or you're unsure. Visits
  and appointments arranged by message ("when is the plumber coming?")
  may only be in email, so include email for those. Trips, travel,
  reservations, bookings, and orders are usually confirmed by email:
  include email for those too.
- "portfolio" for the user's investments: accounts, balances, holdings,
  allocation, stocks or funds they own or watch, retirement plans, and
  market news about them ("how is my IRA doing?", "any news on my
  stocks?", "is anything on my watchlist near my buy price?").
- "profile" for questions about the user themselves, from notes they wrote:
  who they are, family and other people in their life, work, routines,
  priorities, goals, preferences ("what do you know about me?", "who is
  my sister?", "what are my goals this year?").
- "disc_golf" for the user's own disc golf rounds, from their UDisc
  scorecards: courses and layouts played, how often, scores, ratings,
  holes ("how many times have I played Maple Hill?", "what's my best
  score on the red layout?", "how did I play last month?").
- "pong" for the family's Browser Pong league: standings, records, who's
  best, head-to-head, recent matches ("who has the best Pong record?").
- "general" alone, only for questions that clearly aren't about the user's
  own messages, schedule, plans, purchases, people, accounts, or games: facts,
  definitions, how-to, conversions, arithmetic ("how many ounces in a
  cup?"). If it could be about the user's life, use email and calendar.

start_date, end_date: the dates the question is about, as YYYY-MM-DD,
inclusive. Copy them from the named ranges and date table; don't calculate.
A weekday name on its own ("Thursday", "Saturday morning") means the one
marked "coming" in the table. A single day has start_date equal to
end_date. For an email question, dates mean when the email arrived ("the
email from Sept 24th", "what did I get yesterday?"). For those, a date
without a year is the most recent one not after today. The same goes for
disc golf: dates mean when rounds were played. For anything else
(plans, trips, appointments), a month or date without a year is the one in
the Months list, which runs forward from today. Named periods without dates
mean their usual month: spring break is March, Thanksgiving week is late
November, winter break is late December. Only fill in dates when the question mentions or implies a time.
"When is X?" has no dates: use null for both and put X in
calendar_keywords.

calendar_keywords: names of specific events the question mentions (for
example "dentist", "lake trip"). Empty when none.

mail_folder: when the question is about a mail folder or label by name
("what's in my travel folder?", "anything new under Receipts?"), that
name, without the word "folder". Otherwise null.

about_purchases: true when the question is about something the user bought
or paid for: an order, delivery, shipment, package, return, refund,
receipt, or charge ("when will my new boots arrive?"). Otherwise false.

portfolio_news: true when the question asks for news or headlines about
the user's investments or the market. Otherwise false."""


@dataclass
class Route:
    sources: list = field(default_factory=lambda: list(DEFAULT_SOURCES))
    start: date = None
    end: date = None
    keywords: list = field(default_factory=list)
    folder: str = None
    purchases: bool = False  # about something the user bought: orders, deliveries, receipts
    news: bool = False  # wants news about their investments


def named_ranges(today):
    """Common phrases resolved to dates in code, so the model doesn't do
    calendar arithmetic. Weeks run Monday to Sunday, except that on a
    weekend "this week" covers the week ahead."""
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    # On a weekend, "this week" means the week ahead, not the day or two left.
    this_week_end = week_end + timedelta(days=7) if today.weekday() >= 5 else week_end
    if today.weekday() == 6:  # Sunday: only today is left of this weekend
        weekend = (today, today)
    else:
        saturday = today + timedelta(days=5 - today.weekday())
        weekend = (max(saturday, today), saturday + timedelta(days=1))
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    return {
        "today": (today, today),
        "tomorrow": (today + timedelta(days=1),) * 2,
        "yesterday": (today - timedelta(days=1),) * 2,
        "this week": (today, this_week_end),
        "next week": (week_start + timedelta(days=7), week_end + timedelta(days=7)),
        "last week": (week_start - timedelta(days=7), week_end - timedelta(days=7)),
        "this weekend": weekend,
        "next 7 days": (today, today + timedelta(days=7)),
        "this month": (month_start, next_month - timedelta(days=1)),
        "last month": (last_month_start, month_start - timedelta(days=1)),
        "this year": (date(today.year, 1, 1), date(today.year, 12, 31)),
        "last year": (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
    }


def month_table(today, count=12):
    """This month and the next ones, with their years and date ranges."""
    lines = []
    year, month = today.year, today.month
    for _ in range(count):
        first = date(year, month, 1)
        following = date(year + (month == 12), month % 12 + 1, 1)
        lines.append(f"{first:%B %Y}: {first} to {following - timedelta(days=1)}")
        year, month = following.year, following.month
    return lines


def date_table(today, days_back=7, days_ahead=21):
    ranges = [f"{name}: {start} to {end}" for name, (start, end) in named_ranges(today).items()]
    lines = ["Named ranges:", *ranges, "",
             "Months (a month or date named without a year means the one listed here):",
             *month_table(today), "", "Dates:"]
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
    folder = data.get("mail_folder")
    folder = folder.strip() if isinstance(folder, str) and folder.strip() else None
    if folder and "email" not in sources:
        sources.append("email")
    if "general" in sources and len(sources) > 1:
        sources.remove("general")  # anything personal is answered from the user's data
    news = data.get("portfolio_news") is True
    if news and "portfolio" not in sources:
        sources = [s for s in sources if s != "general"] + ["portfolio"]
    purchases = data.get("about_purchases") is True
    if purchases:
        # Purchases are the user's own data, confirmed by email.
        sources = [s for s in sources if s != "general"]
        if "email" not in sources:
            sources.append("email")
    return Route(sources=sources or list(DEFAULT_SOURCES), start=start, end=end, keywords=keywords, folder=folder,
                 purchases=purchases, news=news)


def route(question, chat, today):
    user = f"Date table:\n{date_table(today)}\n\nQuestion: {question}"
    try:
        raw = chat.complete(SYSTEM_PROMPT, user, schema=SCHEMA, temperature=0)
    except Exception:
        return Route()
    return validate(raw)
