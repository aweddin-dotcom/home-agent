"""Check the iCloud sign-in and list the calendars it can see, with how many
events each has in the sync window. Useful for choosing skip_calendars in
config/accounts.yaml.

  python scripts/icloud_check.py [ACCOUNT]     # ACCOUNT defaults to "icloud"

Run it yourself, in your own terminal.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.common import settings  # noqa: E402
from services.common.secrets import MissingSecret, get_secret  # noqa: E402
from services.ingestion.icloud_source import ICLOUD_CALDAV_URL, connect_calendars, fetch_events  # noqa: E402


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "icloud"
    config = settings.accounts(include_disabled=True).get(account)
    if not config or config.get("provider") != "icloud":
        sys.exit(f"No icloud account '{account}' in config/accounts.yaml.")
    try:
        calendars = connect_calendars(
            get_secret(config.get("apple_id_secret", "icloud_apple_id")),
            get_secret(config.get("password_secret", "icloud_app_password")),
            config.get("url", ICLOUD_CALDAV_URL),
        )
    except (MissingSecret, PermissionError) as error:
        sys.exit(str(error))

    sync = settings.retrieval()["sync"]
    skipped = {n.lower() for n in config.get("skip_calendars") or []}
    now = datetime.now(timezone.utc)
    print(f"Signed in. {len(calendars)} calendars "
          f"(events from {sync['calendar_days_back']} days back to {sync['calendar_days_ahead']} ahead):")
    for calendar in calendars:
        events = fetch_events([calendar], account, now, sync["calendar_days_back"], sync["calendar_days_ahead"],
                              settings.TIMEZONE)
        note = "  (skipped by skip_calendars)" if (calendar.name or "").lower() in skipped else ""
        print(f"  {calendar.name or '(unnamed)'}: {len(events)} events{note}")


if __name__ == "__main__":
    main()
