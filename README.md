# Home Agent

Self-hosted personal assistant: email and calendar ingestion, retrieval, an
agent that works within rules4models.txt, and a daily digest
(docs/digest.md). Built on a Windows laptop, deployed on a Mac Studio.

## Layout

| Part | Runs where | Why |
|---|---|---|
| Ollama (models) | Natively on the host | Needs the GPU; Docker on macOS can't use the Apple GPU |
| Qdrant, Open WebUI, assistant services | Docker (docker-compose.yml) | Same Linux containers on both machines |

Model names live in config/models.yaml, one profile per machine, chosen by
`MODEL_PROFILE` in .env.

## Running it

1. Install Ollama and pull the models for your profile, e.g. on the laptop:
   `ollama pull qwen3:8b` and `ollama pull qwen3-embedding:0.6b`
2. `cp .env.example .env` and set `MODEL_PROFILE` (`laptop` or `mac`).
3. Python tools: `python -m venv .venv`, then install with
   `.venv/Scripts/pip install -r requirements-dev.txt` (Windows) or
   `.venv/bin/pip install -r requirements-dev.txt` (Mac).
4. Secrets, as needed: `python scripts/secrets_cli.py status` and `set NAME`
   (see docs/secrets.md). Run with the venv's python.
5. Start everything: `python scripts/up.py` (exports secrets, then
   `docker compose up -d`). Tests: `python -m pytest`.
6. Open WebUI: http://127.0.0.1:3000 (the first account created is the admin).
   Qdrant: http://127.0.0.1:6333/dashboard

## Using it

Run from the project folder with the venv's python, while Ollama and the
containers are running:

| Command | What it does |
|---|---|
| `python scripts/google_auth.py grant ACCOUNT ingestion` | One-time per Google account: approve read-only Gmail and Calendar access in the browser. ACCOUNT is a label from `config/accounts.yaml`. |
| `python scripts/microsoft_auth.py grant ACCOUNT ingestion` | Same, for an Outlook/Hotmail account: prints a code to enter at microsoft.com/devicelogin. |
| `python scripts/icloud_check.py` | For iCloud: after storing the Apple ID and app-specific password with `secrets_cli.py`, check the sign-in and list your calendars. |
| `python -m services.ingestion.sync` | Sync now, instead of waiting for the next automatic run. `--account LABEL` syncs one; `--remove LABEL` deletes one account's local copy; `--rebuild` clears everything and re-syncs; `--stats [--date YYYY-MM-DD]` shows counts per account (no content). Runs inside the `sync-worker` container. |
| `python -m services.retrieval.search "question"` | List the emails that best match a question (runs in the `agent-api` container) |
| `python -m services.retrieval.ask "question"` | Answer a question from email and calendar, with sources |
| `python scripts/run_evals.py` | Ask your question set (`data/evals/questions.md`) through the chat and have the local model grade each answer. Report and a history of runs go to `data/evals/results/`. |
| `python scripts/try_fixtures.py` | Ask sample questions against the invented test data, with the real models. For checking quality after changes or comparing models. |

### Dates in email

Each email's mentioned dates ("check-in March 23", "due 10/2") are recorded
at sync, with the year inferred from when it arrived. Questions about a date
range also list emails that mention those dates, so bookings and deadlines
that never made it onto a calendar are found; two dates close together in
one email count as a span (a stay from arrival to departure). Calendars are
synced a year ahead, and answers say so when a question goes beyond that.

### Automatic sync

The `sync-worker` container syncs every enabled account every 15 minutes
and refreshes folder names hourly (`config/retrieval_settings.yaml`). Under
each chat answer you'll see when the last sync happened, and a warning for
any account that's failing (for example an expired approval, with the
command to fix it) or hasn't synced for an hour. To watch it:
`docker compose logs -f sync-worker` (counts only, never content).

The synced database lives in a Docker volume (`db`), shared read-only with
the chat. On its first start the worker copies in `data/structured.db`, so
nothing is re-downloaded; after that the file in `data/` isn't used.

### Starting automatically (Windows laptop)

At sign-in: Docker Desktop and Ollama start themselves (their own
auto-start settings), and `python scripts/autostart.py install` adds a
Startup-folder shortcut that runs `scripts/startup.py`: it waits for
Docker, refreshes the secret files, and runs `docker compose up -d` (which
also recovers from a manual `docker compose down`). The sync worker waits
for Ollama before each run, so start-up order doesn't matter. Log:
`logs/startup.log`. Undo with `python scripts/autostart.py remove`.

### Daily digest

The sync worker builds the digest (docs/digest.md) on the first sync after
6:30am on weekdays and 7:00am on weekends (`config/digest.yaml`, which also
holds the needs-attention rules). The local model sorts each new email into
needs attention / worth knowing / set aside, using your about-me notes if
you've written them; the layout is assembled in code. Read it by asking the
chat for "my digest". To build today's now: `python -m services.digest.build`
(prints it; runs in the sync-worker container).

### Chat in the browser

Open WebUI (http://127.0.0.1:3000) offers the assistant as the model
**home-agent**, served by the `agent-api` container. It answers from the last
sync, handles follow-up questions, and lists what it searched under each
answer. After changing code in `services/`, run `python scripts/up.py` again
to rebuild it.

If **home-agent** isn't in the model list: Admin Panel > Settings >
Connections > OpenAI API > add a connection with URL
`http://agent-api:8000/v1` and any API key (e.g. `none`).

Synced data is stored locally in the `db` Docker volume (SQLite) and Qdrant
(`data/vector_db/`). Neither is ever committed.

### Accounts

Accounts are listed in `config/accounts.yaml` under generic labels (never
email addresses; the file is committed). Each email and event is stored with
its account's label, and events that appear in several calendars are shown
once. Mail is read from every folder (except junk, deleted, and drafts),
and each sync refreshes folder and label names for all synced mail, so
"what's in my travel folder?" works and moved emails are picked up. The
local copy is upgraded in place for small layout changes and otherwise
rebuilt from the providers, so it never needs backing up.

## Moving to the Mac

Clone the repo, install Ollama for macOS, set `MODEL_PROFILE=mac`, and pick
the chat model in config/models.yaml. Copy over by hand only what you wrote
yourself in `data/` (profile, question set); synced email and calendar are
rebuilt by granting access again and running the sync. Then follow
docs/network.md and docs/secrets.md.

## Docs

- docs/digest.md: what the daily digest contains
- docs/secrets.md: how credentials are stored and used
- docs/network.md: remote access over Tailscale
- docs/templates/: about-me narrative and question-set templates
