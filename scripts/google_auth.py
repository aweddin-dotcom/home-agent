"""Grant one tool access to a Google account, or check that access works.

Each tool gets its own token with only the permissions it needs
(TOOLS in services/common/google_creds.py). Granting opens a browser: sign
in to the account the tool should use and approve. The token is saved to
data/tokens/TOOL/.

  python scripts/google_auth.py grant ingestion
  python scripts/google_auth.py check ingestion

Run these yourself, in your own terminal.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import keyring  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from services.common.google_creds import (  # noqa: E402
    CALENDAR_EVENTS,
    CALENDAR_READONLY,
    GMAIL_MODIFY,
    GMAIL_READONLY,
    GMAIL_SEND,
    TOOLS,
    load_credentials,
    save_private,
    token_path,
)

TOKENS_DIR = ROOT / "data" / "tokens"
SERVICE = "home-agent"


def load_client_config():
    value = keyring.get_password(SERVICE, "google_client")
    if value is None:
        sys.exit("google_client is not set. Run: python scripts/secrets_cli.py set google_client --from-file PATH")
    return json.loads(value)


def missing_scopes(requested, granted):
    return sorted(set(requested) - set(granted or []))


def cmd_grant(tool):
    scopes = TOOLS[tool]
    # Let a partial approval through so missing_scopes() can explain it,
    # instead of oauthlib failing with "Scope has changed".
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    flow = InstalledAppFlow.from_client_config(load_client_config(), scopes)
    print(f"Opening a browser to grant '{tool}' access. Sign in to the account it should use.")
    creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)
    missing = missing_scopes(scopes, creds.granted_scopes)
    if missing:
        sys.exit(
            "Not all permissions were approved, so no token was saved. Missing:\n  "
            + "\n  ".join(missing)
            + "\nRun grant again and leave every checkbox ticked."
        )
    path = token_path(TOKENS_DIR, tool)
    save_private(path, creds.to_json())
    print(f"Saved token for '{tool}' to {path.relative_to(ROOT)}.")


def cmd_check(tool):
    creds = load_credentials(TOKENS_DIR, tool)
    print(f"Token for '{tool}' is valid.")
    scopes = set(TOOLS[tool])
    if scopes & {GMAIL_READONLY, GMAIL_MODIFY}:
        profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
        print(f"Gmail account: {profile['emailAddress']} ({profile['messagesTotal']} messages)")
    if scopes & {CALENDAR_READONLY, CALENDAR_EVENTS}:
        calendars = build("calendar", "v3", credentials=creds).calendarList().list().execute()
        print(f"Calendars visible: {len(calendars.get('items', []))}")
    if GMAIL_SEND in scopes:
        print("Send permission granted (not exercised by check).")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grant or check a tool's Google access.")
    parser.add_argument("command", choices=["grant", "check"])
    parser.add_argument("tool", choices=sorted(TOOLS))
    args = parser.parse_args(argv)
    {"grant": cmd_grant, "check": cmd_check}[args.command](args.tool)


if __name__ == "__main__":
    main()
