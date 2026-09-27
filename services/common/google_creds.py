"""Google permissions per tool, and loading of saved tokens.

Each tool gets its own token with only the permissions it needs
(docs/secrets.md). Tokens are granted by scripts/google_auth.py.
"""

from .tokens import ExpiredToken, MissingToken, save_private  # noqa: F401 (re-exported)

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


def token_path(tokens_dir, account, tool):
    return tokens_dir / account / tool / "token.json"


def load_credentials(tokens_dir, account, tool):
    """Load an account's token for a tool, refreshing and re-saving it if expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(tokens_dir, account, tool)
    if not path.exists():
        raise MissingToken(
            f"No '{tool}' token for account '{account}'. "
            f"Run: python scripts/google_auth.py grant {account} {tool}"
        )
    creds = Credentials.from_authorized_user_file(str(path), TOOLS[tool])
    if not creds.valid:
        from google.auth.exceptions import RefreshError

        try:
            creds.refresh(Request())
        except RefreshError as error:
            # Typical causes: the Google app is in Testing mode (approvals
            # expire after 7 days), or access was revoked in the Google account.
            raise ExpiredToken(
                f"The '{tool}' approval for account '{account}' has expired or been revoked. "
                f"Run: python scripts/google_auth.py grant {account} {tool}"
            ) from error
        save_private(path, creds.to_json())
    return creds
