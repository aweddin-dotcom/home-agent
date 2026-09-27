"""Grant one tool access to a Google account, or check that access works.

Each tool gets its own token with only the permissions it needs
(docs/secrets.md). Granting opens a browser: sign in to the account the
tool should use and approve. The token is saved to data/tokens/TOOL/.

  python scripts/google_auth.py grant ingestion
  python scripts/google_auth.py check ingestion

Run these yourself, in your own terminal.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
TOKENS_DIR = ROOT / "data" / "tokens"
SERVICE = "home-agent"

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"

# One token per tool, each with only the permissions that tool needs.
TOOLS = {
    "ingestion": [GMAIL_READONLY, CALENDAR_READONLY],  # email and calendar sync
    "mail": [GMAIL_MODIFY],  # filing into folders, drafts
    "send": [GMAIL_SEND],  # only after the user approves
    "calendar": [CALENDAR_EVENTS],  # private events; invites need approval
}


def token_path(tool):
    return TOKENS_DIR / tool / "token.json"


def load_client_config():
    value = keyring.get_password(SERVICE, "google_client")
    if value is None:
        sys.exit("google_client is not set. Run: python scripts/secrets_cli.py set google_client --from-file PATH")
    return json.loads(value)


def save_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


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
    save_private(token_path(tool), creds.to_json())
    print(f"Saved token for '{tool}' to {token_path(tool).relative_to(ROOT)}.")


def load_credentials(tool):
    path = token_path(tool)
    if not path.exists():
        sys.exit(f"No token for '{tool}'. Run: python scripts/google_auth.py grant {tool}")
    creds = Credentials.from_authorized_user_file(str(path), TOOLS[tool])
    if not creds.valid:
        creds.refresh(Request())
        save_private(path, creds.to_json())
    return creds


def cmd_check(tool):
    creds = load_credentials(tool)
    print(f"Token for '{tool}' is valid.")
    scopes = set(TOOLS[tool])
    if scopes & {GMAIL_READONLY, GMAIL_MODIFY}:
        profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
        print(f"Gmail account: {profile['emailAddress']} ({profile['messagesTotal']} messages)")
    if scopes & {CALENDAR_READONLY, CALENDAR_EVENTS}:
        calendars = build("calendar", "v3", credentials=creds).calendarList().list().execute()
        print(f"Calendars visible: {len(calendars.get('items', []))}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grant or check a tool's Google access.")
    parser.add_argument("command", choices=["grant", "check"])
    parser.add_argument("tool", choices=sorted(TOOLS))
    args = parser.parse_args(argv)
    {"grant": cmd_grant, "check": cmd_check}[args.command](args.tool)


if __name__ == "__main__":
    main()
