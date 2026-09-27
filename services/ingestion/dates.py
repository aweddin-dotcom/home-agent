"""Find the calendar dates an email mentions ("check-in March 23",
"due 10/2/2026", "Tuesday, Oct 20"), so questions about a date range can
find reservations, deadlines, and plans that only exist in email.

Dates written without a year get the year that puts them nearest after the
email arrived: a confirmation received in August that mentions "March 23"
means next March. (Anything up to 60 days before arrival stays in the same
year, for "your order from Sep 3".)
"""

import re
from datetime import date, datetime, timedelta

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(20\d\d))?"

PATTERNS = [
    # March 23 / Mar. 23, 2027 / March 23rd
    (re.compile(rf"\b{_MONTH}\.?\s+{_DAY}\b{_YEAR}", re.IGNORECASE), ("month", "day", "year")),
    # 23 March / 23rd of March 2027
    (re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}\b\.?{_YEAR}", re.IGNORECASE), ("day", "month", "year")),
    # 2027-03-23
    (re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b"), ("year", "month_num", "day")),
    # 3/23/2027 or 3/23/27 (US order; a year is required, to avoid fractions)
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d|\d\d)\b"), ("month_num", "day", "year")),
]

MAX_DATES = 50
PAST_ALLOWANCE = timedelta(days=60)


def _infer_year(month, day, received):
    try:
        candidate = date(received.year, month, day)
    except ValueError:
        return None
    if candidate < received - PAST_ALLOWANCE:
        try:
            candidate = date(received.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def find_mentions(text, received):
    """[(date, start, end)] for every date mentioned in text, in order.
    `received` is the date (or datetime) the email arrived."""
    if isinstance(received, datetime):
        received = received.date()
    found = []
    for pattern, fields in PATTERNS:
        for match in pattern.finditer(text or ""):
            parts = dict(zip(fields, match.groups()))
            month = MONTHS[parts["month"][:3].lower()] if parts.get("month") else int(parts["month_num"])
            day = int(parts["day"])
            year = parts.get("year")
            if year:
                year = int(year) + (2000 if len(year) == 2 else 0)
                try:
                    when = date(year, month, day)
                except ValueError:
                    continue
            else:
                when = _infer_year(month, day, received)
            if when:
                found.append((when, match.start(), match.end()))
    found.sort(key=lambda m: m[1])
    return found


def mentioned_dates(text, received):
    """Sorted unique ISO dates mentioned in text."""
    return sorted({m[0].isoformat() for m in find_mentions(text, received)})[:MAX_DATES]


def excerpt_around(text, received, start, end, width=250):
    """Text around the first mention of a date in [start, end]."""
    for when, first, last in find_mentions(text, received):
        if start <= when <= end:
            lo, hi = max(0, first - width), min(len(text), last + width)
            snippet = " ".join(text[lo:hi].split())
            return ("..." if lo else "") + snippet + ("..." if hi < len(text) else "")
    return ""
