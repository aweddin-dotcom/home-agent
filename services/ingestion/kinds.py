"""What kind of email this is, from its content: an order (something the user
bought: confirmation, shipping, delivery, receipt, invoice), marketing, or
other. Mail providers don't say (Outlook has no Promotions tab), and search
by topic alone ranks a store's newsletter about an activity above the order
for the gear it's about.
"""

import re

ORDER_SIGNALS = [
    r"\border\s*(#|no\.?|number|confirmation|id)\b",
    r"\border\s*#?\s*[A-Z0-9][A-Z0-9-]{4,}",
    r"\b(has|have|was|were)\s+(been\s+)?(shipped|dispatched|delivered)\b",
    r"\bout for delivery\b",
    r"\btracking\s*(number|#|no\.?|id)?\s*:?\s*[A-Z0-9]{8,}",
    r"\bestimated (delivery|arrival)\b",
    r"\b(receipt|invoice)\b",
    r"\bpayment (received|confirmation|confirmed)\b",
    r"\bthanks? (you )?for your (order|purchase)\b",
    r"\b(return|refund) (request|label|processed|issued)\b",
]
MARKETING_SIGNALS = [
    r"\bunsubscribe\b",
    r"\b\d{1,2}\s?% off\b",
    r"\bshop (now|the)\b",
    r"\bnew arrivals\b",
    r"\blimited time\b",
    r"\b(sale|deals?) (ends|starts|event)\b",
    r"\bfree shipping on\b",
    r"\bview (this email )?in (your )?browser\b",
]
_ORDER = [re.compile(p, re.IGNORECASE) for p in ORDER_SIGNALS]
_MARKETING = [re.compile(p, re.IGNORECASE) for p in MARKETING_SIGNALS]
MARKETING_FOLDERS = {"promotions", "social"}


def email_kind(subject, body, folders=()):
    """"order", "marketing", or "other". An order stays an order even with
    the footer every store email has ("unsubscribe")."""
    text = f"{subject}\n{body or ''}"
    if any(p.search(text) for p in _ORDER):
        return "order"
    if {f.lower() for f in folders} & MARKETING_FOLDERS:
        return "marketing"
    if sum(1 for p in _MARKETING if p.search(text)) >= 2:
        return "marketing"
    return "other"
