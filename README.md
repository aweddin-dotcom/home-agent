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

## Moving to the Mac

Clone the repo, install Ollama for macOS, set `MODEL_PROFILE=mac`, pick the
chat model in config/models.yaml, and copy `data/` over by hand (it is never
in git). Then follow docs/network.md and docs/secrets.md.

## Docs

- docs/digest.md: what the daily digest contains
- docs/secrets.md: how credentials are stored and used
- docs/network.md: remote access over Tailscale
- docs/templates/: about-me narrative and question-set templates
