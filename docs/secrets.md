# Secrets Plan

How credentials are stored and used so the agent can act for the user while
the credentials stay protected.

## Principles

1. **The model never sees a credential.** Credentials live only in the tool
   services that use them (mail tool, calendar tool, notifier). The agent
   asks a tool to act and gets a result back, never a token. The agent
   service itself is given no secrets.
2. **One credential per permission level.** The sync service can read mail
   but not send it; only the send tool can send. See the inventory below.
3. **Confirmation is enforced in code.** Tools that need approval
   (rules4models.txt, section 2) refuse to run without a one-time approval
   signed by the confirmation service. The model can request approval but
   cannot create one.
4. **No payment credentials.** For purchases, the agent prepares the order
   and the user completes it.
5. **Every use is logged; every secret is replaceable.** Nothing is backed
   up. Each secret below has a documented way to regenerate it, so losing
   the Mac means working through a checklist, not recovering a vault.
6. **Personal data stays local.** The assistant uses local models only.
   Claude Code (a cloud service) is used to build and maintain the project
   but is blocked from reading data/, logs/, and secrets (.claude/settings.json).

## Storage

| Kind | Where it lives | Why |
|---|---|---|
| Fixed secrets (client secret, notifier token, signing key) | The OS credential store: macOS login Keychain on the Mac Studio, Windows Credential Manager on the laptop | Encrypted, built in, readable by scripts through Python's `keyring` library on both |
| Runtime copies of fixed secrets | `secrets/` (generated at startup, owner-only permissions, never committed) | Mounted into containers as Docker secrets at `/run/secrets/...`, not environment variables |
| OAuth tokens (Google) | `data/tokens/<account>/<tool>/`, mounted only into the container that uses it | Google refreshes tokens, so the tool must be able to write them |
| Everything on disk | FileVault-encrypted disk | Protects data if the Mac is stolen |

Edge's password manager is for website logins only; nothing here uses it.

### Managing secrets

The names of all secrets are listed in `config/secrets.yaml` (never the
values). The user manages values with `scripts/secrets_cli.py`, in their own
terminal:

| Command | What it does |
|---|---|
| `python scripts/secrets_cli.py status` | Shows which secrets are set or missing. Never shows values. |
| `python scripts/secrets_cli.py set NAME` | Prompts twice for the value, hidden, and stores it. |
| `python scripts/secrets_cli.py set NAME --from-file PATH` | Stores the contents of a file (e.g. Google's client JSON). Delete the file afterwards. |
| `python scripts/secrets_cli.py remove NAME` | Deletes it from the store and from `secrets/`. |
| `python scripts/secrets_cli.py export` | Writes every set secret to `secrets/NAME` for Docker. |

The tool deliberately has no way to pass a value as a command argument, so
values never land in shell history, logs, or a Claude Code transcript.
Claude Code never runs `set`; the user does.

### Startup sequence

`python scripts/up.py` exports the secrets and runs `docker compose up -d`.
Each container receives only the secrets listed for it in
docker-compose.yml, as files at `/run/secrets/NAME`. Service code reads
secrets only from there, so it works the same on either machine.

On the Mac:

1. The Mac boots; the user unlocks FileVault (at the Mac or over SSH on the
   local network), which logs in and unlocks the login Keychain.
2. A login item runs `scripts/up.py`.

## Inventory

| Secret | Used by | Stored in | How to regenerate |
|---|---|---|---|
| Google OAuth client ID + secret | All Google tools (to request tokens) | Credential store | Google Cloud Console → APIs & Services → Credentials → add a new client secret, delete the old one |
| Sync token (`gmail.readonly`, `calendar.readonly`) | Email and calendar sync | `data/tokens/<account>/ingestion/` | `python scripts/google_auth.py grant ACCOUNT ingestion` |
| Gmail organize token (`gmail.modify`) | Mail tool (labels, folders, drafts) | `data/tokens/<account>/mail/` | `python scripts/google_auth.py grant ACCOUNT mail` |
| Gmail send token (`gmail.send`) | Send tool (after approval only) | `data/tokens/<account>/send/` | `python scripts/google_auth.py grant ACCOUNT send` |
| Calendar token (`calendar.events`) | Calendar tool | `data/tokens/<account>/calendar/` | `python scripts/google_auth.py grant ACCOUNT calendar` |
| Microsoft app (client) ID | Microsoft tools (to request approvals); not a secret, but kept with them | Credential store | Microsoft Entra admin center → App registrations → Home Agent → Overview |
| Outlook sync approval (`Mail.Read`, `Calendars.Read`) | Email and calendar sync | `data/tokens/<account>/ingestion/` | `python scripts/microsoft_auth.py grant ACCOUNT ingestion` |
| Outlook organize / send / calendar approvals (`Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`) | Mail, send, and calendar tools (not built yet) | `data/tokens/<account>/<tool>/` | `python scripts/microsoft_auth.py grant ACCOUNT TOOL` |
| Notification token (ntfy or Pushover) | Delivery service | Credential store | Regenerate in the notification service's settings |
| Approval signing key | Confirmation service; tools that verify approvals | Credential store | Generate a new random key; pending approvals become invalid |

To revoke all Microsoft access at once: account.microsoft.com → Privacy →
"Apps and services" → remove Home Agent.

To revoke all Google access at once: Google Account → Security →
"Third-party apps with account access" → remove the app.

Not project secrets (managed by their own tools): Claude Code's login, git's
GitHub credentials (OS credential manager), and the Claude API key used by a
separate app. That key is not used here. If a cloud model is ever added,
create a new key just for this project, with a spending limit.

Qdrant and Ollama need no credentials as long as they are reachable only on
the Mac itself or the internal Docker network, never from the LAN.

## Google setup notes

- Grant and test each tool's access with `scripts/google_auth.py`
  (`grant ACCOUNT TOOL`, `check ACCOUNT TOOL`), run by the user.
  ACCOUNT is a label from config/accounts.yaml. The tool-to-permission map
  is `TOOLS` in that script.

- Set the OAuth app's publishing status to **In production** and skip
  verification. Google exempts personal-use apps from verification; the
  user sees an "unverified app" warning when granting access, and the app
  is limited to 100 users. Leave the logo, homepage, and privacy-policy
  fields empty: a logo triggers brand verification, which asks for them.
- The alternative is staying in **Testing** with each account added as a
  test user. Approvals then expire after 7 days; sync reports this and
  says which `grant` command to run.
- Request each token in its own auth flow with only its scope. Do not use
  incremental authorization (`include_granted_scopes`), which merges scopes
  into one token.
- `gmail.modify` also allows moving mail to Trash. The mail tool must refuse
  this in code, because the rules forbid deleting email.

## Microsoft setup notes

- The app is registered in the Microsoft Entra admin center for
  **personal Microsoft accounts only**, as a public client ("Allow public
  client flows" on). It has no client secret; only its app (client) ID is
  stored.
- Approvals use the device-code sign-in: `microsoft_auth.py grant` prints a
  code to enter at microsoft.com/devicelogin. This also works over SSH.
- Approvals renew as they're used and lapse after about 90 days without
  use; sync reports which `grant` command to run.
- Same permission split as Google. `Mail.ReadWrite` also allows deleting,
  so the mail tool must refuse deletes in code.

## Before the Mac arrives

- Development uses the invented data in tests/fixtures/ and needs no
  credentials.
- For testing against the real Google APIs, use a separate throwaway Gmail
  account. The user's real Google account is first connected on the Mac.
