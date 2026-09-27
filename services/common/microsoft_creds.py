"""Microsoft (Outlook/Hotmail) permissions per tool, and loading of saved approvals.

Same one-approval-per-tool design as Google (docs/secrets.md). The app is a
public client registered for personal Microsoft accounts, so there is no
client secret; the app (client) ID is stored alongside each approval.
Approvals are granted by scripts/microsoft_auth.py.
"""

import json

from .tokens import ExpiredToken, MissingToken, save_private

AUTHORITY = "https://login.microsoftonline.com/consumers"  # personal accounts
GRAPH = "https://graph.microsoft.com/"

TOOLS = {
    "ingestion": [GRAPH + "Mail.Read", GRAPH + "Calendars.Read"],  # email and calendar sync
    "mail": [GRAPH + "Mail.ReadWrite"],  # filing into folders, drafts
    "send": [GRAPH + "Mail.Send"],  # only after the user approves
    "calendar": [GRAPH + "Calendars.ReadWrite"],  # private events; invites need approval
}


def token_path(tokens_dir, account, tool):
    return tokens_dir / account / tool / "msal.json"


def save_grant(path, client_id, cache):
    save_private(path, json.dumps({"client_id": client_id, "authority": AUTHORITY, "cache": cache.serialize()}))


def missing_scopes(requested, granted):
    """Microsoft may report granted scopes with or without the Graph prefix."""
    granted = {s.lower().removeprefix(GRAPH.lower()) for s in granted}
    return [s for s in requested if s.lower().removeprefix(GRAPH.lower()) not in granted]


class TokenProvider:
    """Hands out a current access token for one account and tool, refreshing
    it from the saved approval as needed. Call it before each request."""

    def __init__(self, tokens_dir, account, tool):
        import msal

        self.path = token_path(tokens_dir, account, tool)
        self.account, self.tool = account, tool
        if not self.path.exists():
            raise MissingToken(
                f"No '{tool}' approval for account '{account}'. "
                f"Run: python scripts/microsoft_auth.py grant {account} {tool}"
            )
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.client_id = saved["client_id"]
        self.cache = msal.SerializableTokenCache()
        self.cache.deserialize(saved["cache"])
        self.app = msal.PublicClientApplication(self.client_id, authority=saved["authority"], token_cache=self.cache)
        self.scopes = TOOLS[tool]

    def __call__(self):
        accounts = self.app.get_accounts()
        result = self.app.acquire_token_silent(self.scopes, account=accounts[0]) if accounts else None
        if not result or "access_token" not in result:
            raise ExpiredToken(
                f"The '{self.tool}' approval for account '{self.account}' has expired or been revoked. "
                f"Run: python scripts/microsoft_auth.py grant {self.account} {self.tool}"
            )
        if self.cache.has_state_changed:
            save_grant(self.path, self.client_id, self.cache)
        return result["access_token"]
