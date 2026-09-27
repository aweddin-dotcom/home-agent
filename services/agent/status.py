"""Short notes about sync health, shown under each chat answer."""

from datetime import datetime, timezone


def ago(when, now):
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def _time(value):
    return datetime.fromisoformat(value) if value else None


def sync_notes(statuses, enabled_accounts, now=None, stale_after_minutes=60):
    """Lines like "Last sync: 4 min ago." plus one per account that's failing,
    stale, or not synced yet."""
    now = now or datetime.now(timezone.utc)
    lines = []
    successes = [_time(statuses[a]["last_success"]) for a in enabled_accounts
                 if a in statuses and statuses[a]["last_success"]]
    if successes:
        lines.append(f"Last sync: {ago(max(successes), now)}.")
    for account in enabled_accounts:
        status = statuses.get(account)
        if status is None:
            lines.append(f"Not synced yet: {account}.")
        elif not status["ok"]:
            lines.append(f"Sync problem ({account}): {status['detail']}")
        else:
            last = _time(status["last_success"])
            if last and (now - last).total_seconds() > stale_after_minutes * 60:
                lines.append(f"{account} last synced {ago(last, now)}.")
    return lines
