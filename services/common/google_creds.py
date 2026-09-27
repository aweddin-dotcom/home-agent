"""Google permissions per tool, and loading of saved tokens.

Each tool gets its own token with only the permissions it needs
(docs/secrets.md). Tokens are granted by scripts/google_auth.py.
"""

import os
import sys

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"

TOOLS = {
    "ingestion": [GMAIL_READONLY, CALENDAR_READONLY],  # email and calendar sync
    "mail": [GMAIL_MODIFY],  # filing into folders, drafts
    "send": [GMAIL_SEND],  # only after the user approves
    "calendar": [CALENDAR_EVENTS],  # private events; invites need approval
}


def token_path(tokens_dir, tool):
    return tokens_dir / tool / "token.json"


def save_private(path, text):
    """Write a file readable only by its owner (on macOS and Linux)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def load_credentials(tokens_dir, tool):
    """Load a tool's token, refreshing and re-saving it if it has expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(tokens_dir, tool)
    if not path.exists():
        sys.exit(f"No token for '{tool}'. Run: python scripts/google_auth.py grant {tool}")
    creds = Credentials.from_authorized_user_file(str(path), TOOLS[tool])
    if not creds.valid:
        creds.refresh(Request())
        save_private(path, creds.to_json())
    return creds
