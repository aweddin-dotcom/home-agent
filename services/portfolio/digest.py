"""The digest's Investments section, from Portfolio Analyzer's digest and news
endpoints plus the user's synced mail (for "your statement is ready" emails).

Numbers (targets, moves, dates) are laid out in code; the local model only
condenses news headlines into a few bullets.
"""

import re
from datetime import date, datetime

from .client import PortfolioUnavailable

STATEMENT_READY = re.compile(
    r"\b(e-?)?statements?\b|\bdocuments? (is |are )?(now )?(ready|available)\b|\b1099\b|\btax (form|document)s?\b",
    re.IGNORECASE,
)

NEWS_PROMPT = """Condense these headlines about the user's investments into at most 3 short
bullets, most important first. Each bullet names the ticker(s) in
parentheses first, like "(AAPL) ...". Use only what the headlines say; don't
speculate or give advice. Reply with the bullets only, each on its own line
starting with "- "."""


def _money(value):
    return f"${value:,.2f}"


def _day(iso):
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{d:%b} {d.day}"


def broker_name(key):
    return key.upper() if len(key) <= 3 else key.title()


def watchlist_lines(alerts, limit=5):
    """The alerts closest to their targets; a count for the rest."""
    closest = sorted(alerts, key=lambda a: abs(a["pct_from_target"]))
    lines = []
    for a in closest[:limit]:
        name = a["ticker"] + (f" ({a['label']})" if a.get("label") else "")
        pct = a["pct_from_target"]
        side = "above" if pct >= 0 else "below"
        kind = "buy" if a["zone"] == "buy" else "sell"
        lines.append(f"- Watchlist: {name} at {_money(a['price'])}, {abs(pct):.1f}% {side} your {kind} target "
                     f"({_money(a['target'])})")
    if len(closest) > limit:
        lines.append(f"- ...and {len(closest) - limit} more watchlist tickers near or past their targets")
    return lines


def mover_lines(movers, limit=3):
    """The biggest moves since each holding's statement price; a count for the rest."""
    lines = [f"- {m['ticker']} {'up' if m['pct'] > 0 else 'down'} {abs(m['pct']):.1f}% since your "
             f"{_day(m['statement_date'])} statement (now {_money(m['price'])})" for m in movers[:limit]]
    if len(movers) > limit:
        lines.append(f"- ...and {len(movers) - limit} more holdings moved 5% or more since their statements")
    return lines


def statement_lines(accounts, emails, today, config):
    """Reminders to upload statements: a newer "statement ready" email from the
    broker, or an account whose latest upload is old."""
    latest = {}
    for account in accounts:
        broker = (account.get("broker") or "").lower()
        if account.get("statement_date"):
            latest[broker] = max(latest.get(broker, ""), account["statement_date"])

    lines, reminded = [], set()
    for key, patterns in (config.get("brokers") or {}).items():
        mentions = [e for e in emails
                    if any(p.lower() in f"{e['sender']} {e['subject']}".lower() for p in patterns)
                    and STATEMENT_READY.search(e["subject"] or "")]
        if not mentions:
            continue
        newest = max(mentions, key=lambda e: e["date"])
        if newest["date"][:10] > latest.get(key, ""):
            last = f"your latest upload is from {_day(latest[key])}" if key in latest else "none uploaded yet"
            lines.append(f"- New {broker_name(key)} statement available (email {_day(newest['date'])}); "
                         f"{last}. Upload it to Portfolio Analyzer.")
            reminded.add(key)

    stale_days = config.get("stale_statement_days", 45)
    for broker, statement_date in sorted(latest.items()):
        age = (today - date.fromisoformat(statement_date[:10])).days
        if broker not in reminded and age > stale_days:
            lines.append(f"- Latest {broker_name(broker)} statement uploaded is from {_day(statement_date)} "
                         f"({age} days ago).")
    return lines


def news_lines(chat, articles, limit):
    """Up to 3 bullets condensing headlines about holdings (watchlist news if none)."""
    picked = [a for a in articles if a.get("about_holdings")] or articles
    picked = picked[:limit]
    if not picked:
        return []
    headlines = "\n".join(f"({', '.join(a.get('tickers') or [])}) {a.get('title', '')}: {a.get('summary', '')[:200]}"
                          for a in picked)
    try:
        reply = chat.complete(NEWS_PROMPT, headlines, temperature=0)
        bullets = [line.strip() for line in reply.splitlines() if line.strip().startswith("- ")][:3]
    except Exception:  # noqa: BLE001 - fall back to plain headlines
        bullets = []
    if not bullets:
        bullets = [f"- ({', '.join(a.get('tickers') or [])}) {a.get('title', '')}" for a in picked[:3]]
    return [f"- News: {b[2:]}" for b in bullets]


def investment_lines(client, chat, emails, today, config, log=print):
    """(lines for the Investments section, a note for "What I did" or None)."""
    if client is None:
        return [], None
    try:
        data = client.digest()
    except PortfolioUnavailable:
        return [], "Portfolio Analyzer wasn't running, so there's no Investments section."
    lines = watchlist_lines(data.get("watchlist_alerts") or [], config.get("max_watchlist_lines", 5))
    lines += mover_lines(data.get("movers") or [], config.get("max_mover_lines", 3))
    lines += statement_lines(data.get("accounts") or [], emails, today, config)
    try:
        articles = client.news(days=config.get("news_days", 2)).get("articles") or []
        lines += news_lines(chat, articles, config.get("news_articles_to_summarize", 10))
    except PortfolioUnavailable:
        log("Digest: Portfolio Analyzer news didn't answer; skipping news.")
    return lines, None
