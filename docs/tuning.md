# Tuning guide

The commands and settings for checking on Home Agent and adjusting it as you
use it. Everything here stays on this machine.

## Running commands

Open PowerShell in the project folder (`C:\Users\awedd\Projects\Home-Agent`)
and use the project's Python:

```powershell
.venv\Scripts\python -m services.ingestion.sync --stats
```

On the Mac it's `.venv/bin/python`. Commands that work with your mail run
inside the right container by themselves (they print "running in the ...
container"), so Docker and Ollama need to be running.

Commands marked **personal** print your own mail details. They're for your
terminal; don't paste their output anywhere you wouldn't paste an email.

## Is everything working?

| Command | What it shows |
|---|---|
| `docker compose ps` | Which services are running |
| `docker compose logs sync-worker --since 2h` | Recent syncs, folder refreshes, digest building, and any errors |
| `.venv\Scripts\python -m services.ingestion.sync --stats` | Emails and events per account, date range, and when each last synced (counts only) |
| `...sync --stats --date 2026-10-01` | Also how many emails arrived that day |

Chat also adds a note under answers when an account hasn't synced recently.

## Tools

### Sync

| Command | What it does |
|---|---|
| `...python -m services.ingestion.sync` | Sync all accounts now, instead of waiting up to 15 minutes |
| `...sync --account outlook` | Sync one account |
| `...sync --remove outlook` | Delete one account's local copy (nothing changes at the provider). Follow with `--account outlook` to download it again |
| `...sync --rebuild` | Clear the whole local copy and download everything again (takes a while) |

### Digest

| Command | What it does |
|---|---|
| `...python -m services.digest.build` | Build today's digest now and print it (**personal**). Handy after changing `config/digest.yaml`. Chat's "digest" shows the latest one |

### Sender checks (phishing)

| Command | What it does |
|---|---|
| `...python -m services.common.senders` | Senders flagged in the last 30 days: level, name, domain, number of emails (**personal**) |
| `...python -m services.common.senders 90` | Same, for the last 90 days |

### Checking answer quality

| Command | What it does |
|---|---|
| `...python -m services.retrieval.ask "question"` | Answer one question, with the sources used (**personal**) |
| `...python -m services.retrieval.search "question"` | Just the emails that best match a question (**personal**) |
| `...python scripts/run_evals.py` | Run your question set (`data/evals/questions.md`) and grade the answers; report in `data/evals/results/`. `--only 2 5` for some questions, `--no-grade` to just collect answers |
| `...python scripts/try_fixtures.py` | Ask questions against the invented test data, with the real models. Safe to share the output |

### Accounts and secrets

| Command | What it does |
|---|---|
| `...python scripts/google_auth.py grant gmail ingestion` | Re-approve Gmail access (needed weekly while the Google app is in testing mode). `check` instead of `grant` tests it |
| `...python scripts/microsoft_auth.py grant outlook ingestion` | Re-approve Outlook access |
| `...python scripts/icloud_check.py` | Check the iCloud sign-in and list calendars with event counts |
| `...python scripts/secrets_cli.py status` | Which secrets are stored (values are never shown). `set NAME` to add or change one |

### Starting and stopping

| Command | What it does |
|---|---|
| `...python scripts/up.py` | Start or update everything (after code changes too) |
| `docker compose restart agent-api` | Restart the chat service (after changing the settings marked "restart" below) |
| `...python scripts/autostart.py status` | Whether Home Agent starts when you sign in. `install` / `remove` |

## Settings you can change

All in `config/` unless noted. Most take effect the next time they're used;
the ones marked **restart** need `docker compose restart agent-api`.

| File | What to change | Examples |
|---|---|---|
| `digest.yaml` | When the digest is built; how many items per section (`max_items`); folders it ignores (`skip_folders`); what counts as needing attention (`needs_attention`); what never does (`never`) | Add "Anything from my kids' school about a deadline" under `needs_attention` |
| `sender_checks.yaml` | `trusted:` senders that are genuine but get flagged; `brands:` companies to protect (with their real domains); `sending_services:` services that send for businesses you know | A school's alert service flagged as "doesn't match": add its domain under `trusted:` |
| `retrieval_settings.yaml` | How far back mail and calendars are synced, how often, how many search results feed an answer (`search.top_k`) | `email_days: 730` for two years of mail |
| `accounts.yaml` | Which accounts sync; `enabled: false` to pause one; iCloud calendars to skip (`skip_calendars`) | Skip a shared calendar you don't care about |
| `models.yaml` | The chat and embedding models per machine, and the context size (**restart**) | The Mac's model choice |
| `portfolio.yaml` | Portfolio Analyzer address (**restart**), digest Investments limits, broker names for statement reminders | `stale_statement_days: 30` |
| `pong.yaml` | Browser Pong address (**restart**) | |
| `.env` (project folder) | `MODEL_PROFILE`, `HOME_AGENT_TIMEZONE`, `UDISC_PLAYER`, `PONG_URL`, `PORTFOLIO_URL` (apply with `python scripts/up.py`) | `PONG_URL` moved with the games to the iMac |

Your own notes and data, in `data/` (never committed):

- `data/profile/about-me.md`: about you and your family. The digest uses it
  to know who's family; chat uses it for questions about you
- `data/evals/questions.md`: your question set for `run_evals.py`
- `data/udisc/`: the latest UDisc scorecard export

## Common tweaks

- **A real email was flagged as phishing or "doesn't match":** run the
  sender report, then add the domain (or the whole address) under
  `trusted:` in `sender_checks.yaml`.
- **The digest missed something important:** add a rule under
  `needs_attention` in `digest.yaml`, then rebuild today's digest to check.
- **The digest shows things you don't care about:** add a line under
  `never`, or a folder to `skip_folders`.
- **Chat got something wrong:** add the question to your question set with
  the right answer, so `run_evals.py` keeps checking it, and tell Claude
  Code what went wrong.
- **An account stopped syncing:** check the logs, then re-approve it with
  the `grant` command above.

## The other apps

From Git Bash on the laptop:

| Command | What it does |
|---|---|
| `~/Projects/deploy-to-imac.sh pong` (or `capybara`, `all`) | Copy the committed game to the iMac and restart it; match history is untouched |
| `~/Projects/publish-frisbee-pong.sh "what changed" --push` | Publish Browser Pong's committed version to the public Frisbee-Pong repo (without `--push`, just prepares and shows it) |
