"""Sources: one per account, each producing Email and Event records.

Everything after sync (store, search, calendar lookup, answers) works the
same whichever provider a record came from. To add a provider, add a class
with the same attributes and methods, and register it in PROVIDERS.
"""

from datetime import datetime, timedelta, timezone

from .calendar_source import fetch_events
from .gmail_source import fetch_messages


class GoogleSource:
    """Gmail and Google Calendar, read-only, via the account's 'ingestion' token."""

    has_email = True
    has_calendar = True

    def __init__(self, account, gmail, calendar, log=print):
        self.account = account
        self.log = log
        self.gmail = gmail
        self.calendar = calendar

    @classmethod
    def connect(cls, account, config, tokens_dir):
        from googleapiclient.discovery import build

        from services.common.google_creds import load_credentials

        creds = load_credentials(tokens_dir, account, "ingestion")
        return cls(
            account,
            build("gmail", "v1", credentials=creds, cache_discovery=False),
            build("calendar", "v3", credentials=creds, cache_discovery=False),
        )

    def emails(self, days, max_emails, skip_ids):
        query = f"newer_than:{days}d -in:drafts"
        return fetch_messages(self.gmail, self.account, query, max_emails, skip_ids, self.log)

    def events(self, now, days_back, days_ahead):
        return fetch_events(self.calendar, self.account, now, days_back, days_ahead, self.log)

    def folders(self, days, max_emails):
        from .gmail_source import fetch_label_map

        return fetch_label_map(self.gmail, f"newer_than:{days}d -in:drafts", max_emails, self.log)


class MicrosoftSource:
    """Outlook/Hotmail mail and calendar, read-only, via Microsoft Graph and
    the account's 'ingestion' approval."""

    has_email = True
    has_calendar = True

    def __init__(self, account, client):
        self.account = account
        self.client = client

    @classmethod
    def connect(cls, account, config, tokens_dir, log=print):
        from services.common.microsoft_creds import TokenProvider

        from .graph_api import GraphClient

        get_token = TokenProvider(tokens_dir, account, "ingestion")
        get_token()  # fail now, with a clear message, if the approval has lapsed
        return cls(account, GraphClient(get_token, log))

    def emails(self, days, max_emails, skip_ids):
        from .microsoft_source import fetch_messages

        since = datetime.now(timezone.utc) - timedelta(days=days)
        return fetch_messages(self.client, self.account, since, max_emails, skip_ids)

    def events(self, now, days_back, days_ahead):
        from .microsoft_source import fetch_events

        return fetch_events(self.client, self.account, now, days_back, days_ahead)

    def folders(self, days, max_emails):
        from .microsoft_source import fetch_folder_map

        return fetch_folder_map(self.client, datetime.now(timezone.utc) - timedelta(days=days), max_emails)


PROVIDERS = {"google": GoogleSource, "microsoft": MicrosoftSource}


def connect(account, config, tokens_dir):
    provider = config.get("provider")
    if provider not in PROVIDERS:
        raise ValueError(f"Account '{account}': unknown provider '{provider}' (known: {', '.join(PROVIDERS)})")
    return PROVIDERS[provider].connect(account, config, tokens_dir)
