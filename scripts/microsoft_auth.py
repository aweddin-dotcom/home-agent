"""Grant one tool access to a Microsoft (Outlook/Hotmail) account, or check
that access works.

Each tool gets its own approval with only the permissions it needs
(TOOLS in services/common/microsoft_creds.py). ACCOUNT is a label from
config/accounts.yaml. Granting prints a short code: open the address it
shows in any browser, enter the code, sign in to the Microsoft account that
label should mean, and approve. The approval is saved to
data/tokens/ACCOUNT/TOOL/.

  python scripts/microsoft_auth.py grant outlook ingestion
  python scripts/microsoft_auth.py check outlook ingestion

Run these yourself, in your own terminal.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import keyring  # noqa: E402

from services.common import settings  # noqa: E402
from services.common.microsoft_creds import (  # noqa: E402
    AUTHORITY,
    TOOLS,
    TokenProvider,
    missing_scopes,
    save_grant,
    token_path,
)
from services.common.tokens import MissingToken  # noqa: E402

TOKENS_DIR = ROOT / "data" / "tokens"
SERVICE = "home-agent"


def load_client_id():
    value = keyring.get_password(SERVICE, "microsoft_client_id")
    if value is None:
        sys.exit("microsoft_client_id is not set. Run: python scripts/secrets_cli.py set microsoft_client_id")
    return value.strip()


def require_microsoft_account(account, accounts):
    config = accounts.get(account)
    if config is None:
        sys.exit(f"No account '{account}' in config/accounts.yaml. Add it there first.")
    if config.get("provider") != "microsoft":
        sys.exit(f"Account '{account}' is a '{config.get('provider')}' account, not microsoft.")


def cmd_grant(account, tool):
    import msal

    client_id = load_client_id()
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(client_id, authority=AUTHORITY, token_cache=cache)
    flow = app.initiate_device_flow(scopes=TOOLS[tool])
    if "user_code" not in flow:
        sys.exit(f"Couldn't start sign-in: {flow.get('error_description', flow)}")
    print(f"Granting '{tool}' access for '{account}'. Sign in to the Microsoft account it should use.")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        sys.exit(f"Sign-in didn't complete: {result.get('error_description', result.get('error'))}")
    missing = missing_scopes(TOOLS[tool], result.get("scope", "").split())
    if missing:
        sys.exit(
            "Not all permissions were approved, so nothing was saved. Missing:\n  "
            + "\n  ".join(missing)
            + "\nRun grant again and approve every permission."
        )
    path = token_path(TOKENS_DIR, account, tool)
    save_grant(path, client_id, cache)
    print(f"Saved approval for '{account}' / '{tool}' to {path.relative_to(ROOT)}.")


def cmd_check(account, tool):
    from services.ingestion.graph_api import GraphClient

    try:
        provider = TokenProvider(TOKENS_DIR, account, tool)
        provider()
    except MissingToken as error:
        sys.exit(str(error))
    client = GraphClient(provider)
    print(f"Approval for '{account}' / '{tool}' is valid.")
    # /me would need the User.Read permission, which no tool asks for.
    print(f"Microsoft account: {provider.username}")
    if "Mail" in " ".join(TOOLS[tool]):
        inbox = client.get("/me/mailFolders/inbox", params={"$select": "totalItemCount"})
        print(f"Inbox: {inbox.get('totalItemCount')} messages")
    if "Calendars" in " ".join(TOOLS[tool]):
        calendars = list(client.paged("/me/calendars", {"$select": "id"}))
        print(f"Calendars visible: {len(calendars)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grant or check a tool's access to a Microsoft account.")
    parser.add_argument("command", choices=["grant", "check"])
    parser.add_argument("account", help="label from config/accounts.yaml")
    parser.add_argument("tool", choices=sorted(TOOLS))
    args = parser.parse_args(argv)
    require_microsoft_account(args.account, settings.accounts(include_disabled=True))
    {"grant": cmd_grant, "check": cmd_check}[args.command](args.account, args.tool)


if __name__ == "__main__":
    main()
